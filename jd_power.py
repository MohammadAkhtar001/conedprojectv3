"""
ProPublica Nonprofit Explorer extractor.

Source: https://projects.propublica.org/nonprofits/api/v2/
Auth:   None.  Public API.
Rate:   Unspecified; we throttle to ~5 req/s/host.

Supplies:
  - charitable_giving   ($M, total grants paid + corporate contributions)
  - foundation_assets   ($M, foundation total assets at FY end)

Why we search by name instead of using a hard-coded EIN: hard-coding EINs
into the registry is fragile — one typo means permanent 404.  Foundations
also occasionally re-incorporate under new EINs (e.g. when reorganizing
under a new parent).  We use ProPublica's /search.json?q= endpoint to
resolve the foundation by name at runtime, then call /organizations/{EIN}.json
to fetch its filings.  The resolution is cached per-process so multiple
metrics for the same foundation reuse the lookup.

Confidence: 0.85.  IRS-sourced via ProPublica.

API endpoints (verified against ProPublica's published docs):
  GET /api/v2/search.json?q=NAME
  GET /api/v2/organizations/{EIN_INTEGER}.json
"""

from __future__ import annotations
import logging
from typing import Iterable, Optional

from pipeline.models import DataPoint, ExtractionAttempt, METRICS, CONFIDENCE, make_failure
from pipeline.fetcher import fetch, FetchError
from pipeline.validate import validate_value, ValidationFailure
from .base import Extractor, register
from .company_registry import Company

log = logging.getLogger("propublica_990")


# Process-level cache: foundation name → (resolved_ein, organization_data).
# Keyed on company.name so all metrics for a single company share one lookup.
_RESOLUTION_CACHE: dict[str, dict] = {}


class ProPublica990Extractor(Extractor):
    supplies_metrics = ("charitable_giving", "foundation_assets")
    source_name = "ProPublica Nonprofit Explorer (IRS 990)"
    base_confidence = CONFIDENCE["propublica_990"]

    def extract(self, company: Company, metrics: Iterable[str]) -> list[DataPoint]:
        wanted = [m for m in metrics if m in self.supplies_metrics]
        if not wanted:
            return []

        results: list[DataPoint] = []
        attempts: list[ExtractionAttempt] = []

        # ── Resolve the foundation ──────────────────────────────────────────
        # Two strategies, in order:
        #   1. Search ProPublica by foundation name.  Most reliable; survives
        #      EIN changes and registry typos.
        #   2. If a foundation_ein is set in the registry, try it directly
        #      as a fallback (in case the foundation isn't well-indexed by
        #      its formal name on ProPublica).
        cache_key = company.name
        cached = _RESOLUTION_CACHE.get(cache_key)
        if cached:
            data = cached["data"]
            attempts.extend(cached["attempts"])
        else:
            search_name = company.foundation_name or company.name
            if not search_name:
                for m in wanted:
                    results.append(make_failure(
                        company=company.name, metric=m,
                        reason="no foundation name on file for this company",
                        attempts=[],
                    ))
                return results

            data = None
            search_attempts: list[ExtractionAttempt] = []

            # Strategy 1 — search
            try:
                data, sa = self._search_then_fetch(search_name, company)
                search_attempts.extend(sa)
            except FetchError as e:
                search_attempts.append(e.attempt)

            # Strategy 2 — direct EIN if registry has one
            if data is None and company.foundation_ein:
                try:
                    data, sa = self._fetch_by_ein(company.foundation_ein)
                    search_attempts.extend(sa)
                except FetchError as e:
                    search_attempts.append(e.attempt)

            attempts.extend(search_attempts)

            if data is None:
                for m in wanted:
                    results.append(make_failure(
                        company=company.name, metric=m,
                        reason=(f"could not resolve foundation '{search_name}' on ProPublica "
                                "(search and direct-EIN both failed)"),
                        attempts=attempts,
                    ))
                return results

            _RESOLUTION_CACHE[cache_key] = {"data": data, "attempts": list(attempts)}

        # ── Pick most recent filing with usable data ────────────────────────
        filings = data.get("filings_with_data", [])
        if not filings:
            for m in wanted:
                results.append(make_failure(
                    company=company.name, metric=m,
                    reason="ProPublica has no filings_with_data for this organization",
                    attempts=attempts,
                ))
            return results

        filings.sort(key=lambda f: f.get("tax_prd_yr") or 0, reverse=True)
        latest = filings[0]
        year = latest.get("tax_prd_yr")
        org = data.get("organization") or {}
        org_name = org.get("name") or company.foundation_name
        ein_used = org.get("ein") or company.foundation_ein
        ein_str = str(ein_used).zfill(9) if ein_used else "?"
        pp_ui_url = f"https://projects.propublica.org/nonprofits/organizations/{ein_used}"

        # ── Extract each requested metric ───────────────────────────────────
        for m in wanted:
            value = self._extract_metric(m, latest)
            if value is None:
                fields_present = sorted(
                    k for k in latest if any(t in k.lower() for t in ("tot", "grnt", "asset", "contrib"))
                )[:15]
                results.append(make_failure(
                    company=company.name, metric=m,
                    reason=f"no usable field in 990 filing for {m}",
                    attempts=attempts,
                    notes=f"FY{year}; relevant fields present: {fields_present}",
                ))
                continue
            unit = METRICS[m]["unit"]
            try:
                validated = validate_value(m, value, unit)
            except ValidationFailure as ve:
                results.append(make_failure(
                    company=company.name, metric=m,
                    reason=f"value rejected by validator: {ve.reason}",
                    attempts=attempts,
                    notes=f"raw {m}={value} {unit} from {org_name} FY{year}",
                ))
                continue
            results.append(DataPoint(
                company=company.name, metric=m,
                value=round(validated, 3),
                unit=unit,
                year=f"FY{year}" if year else None,
                source_url=pp_ui_url,
                source_name=f"{self.source_name} — {org_name} (EIN {ein_str})",
                confidence_score=self.base_confidence,
                attempts=attempts,
                notes=f"From IRS Form 990 / 990-PF, fiscal year {year}",
            ))

        return results

    # ── Resolution helpers ──────────────────────────────────────────────────

    def _search_then_fetch(self, name: str, company: Company) -> tuple[dict, list[ExtractionAttempt]]:
        """Search ProPublica by name, pick the best match, fetch its full record."""
        from urllib.parse import quote_plus
        search_url = (
            f"https://projects.propublica.org/nonprofits/api/v2/search.json"
            f"?q={quote_plus(name)}"
        )
        attempts: list[ExtractionAttempt] = []
        r, a = fetch(search_url, source=self.source_name + " (search)", timeout=20, expect="json")
        attempts.append(a)
        try:
            sresult = r.json()
        except Exception as e:
            raise FetchError(a, f"search response not JSON: {e}")
        orgs = sresult.get("organizations") or []
        if not orgs:
            return None, attempts

        # Score candidates: prefer ones whose name contains "foundation" AND
        # contains the company's distinguishing word (e.g. "Edison", "Duke").
        # This protects against e.g. picking "Edison International Foundation"
        # when we wanted "Consolidated Edison Foundation".
        company_words = set(w.lower() for w in company.name.split() if len(w) > 3)
        company_words.update(w.lower() for a in company.aliases for w in a.split() if len(w) > 3)

        def score(o: dict) -> int:
            n = (o.get("name") or "").lower()
            s = 0
            if "foundation" in n: s += 10
            if "charitable" in n: s += 5
            for w in company_words:
                if w in n: s += 4
            # Penalize obvious mismatches
            for bad in ("welfare benefit", "master trust", "pension", "society",
                        "employees", "retirement", "club", "international"):
                if bad in n: s -= 6
            return s

        orgs.sort(key=score, reverse=True)
        best = orgs[0]
        if score(best) <= 0:
            return None, attempts

        ein = best.get("ein")
        if not ein:
            return None, attempts

        data, fetch_attempts = self._fetch_by_ein(str(ein))
        attempts.extend(fetch_attempts)
        return data, attempts

    def _fetch_by_ein(self, ein: str) -> tuple[dict, list[ExtractionAttempt]]:
        """Direct fetch by EIN.  Accepts EIN as integer string or with dashes."""
        ein_int = int(str(ein).replace("-", ""))
        url = f"https://projects.propublica.org/nonprofits/api/v2/organizations/{ein_int}.json"
        r, a = fetch(url, source=self.source_name + " (org)", timeout=20, expect="json")
        try:
            data = r.json()
        except Exception as e:
            raise FetchError(a, f"org response not JSON: {e}")
        return data, [a]

    @staticmethod
    def _extract_metric(metric: str, filing: dict) -> Optional[float]:
        """Map our metric keys to ProPublica's normalized 990 field names.

        Field semantics (per ProPublica docs and IRS 990 schema):
          - 990-PF (private foundations — most utility corporate foundations):
              cttgrntpd     = contributions, gifts, grants PAID OUT
              totassetsend  = total assets at year end
          - 990 (operating non-profit, less common for utility foundations):
              totcntrbgfts  = total contributions / gifts received
              totalassetsend or totassetsend = total assets at year end
          - 990-EZ:
              grntspaid     = grants paid out
        """
        if metric == "charitable_giving":
            for key in ("cttgrntpd", "grntspaid", "totalcontribs", "totcntrbgfts",
                        "grsincmembrs", "totcontribs"):
                v = filing.get(key)
                if isinstance(v, (int, float)) and v > 0:
                    return v / 1e6   # dollars → millions
        elif metric == "foundation_assets":
            for key in ("totassetsend", "totalassetsend", "totasstend",
                        "totassetsendofyr"):
                v = filing.get(key)
                if isinstance(v, (int, float)) and v > 0:
                    return v / 1e6
        return None


register("charitable_giving", ProPublica990Extractor)
register("foundation_assets", ProPublica990Extractor)
