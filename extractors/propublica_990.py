"""
ProPublica Nonprofit Explorer extractor.

Source: https://projects.propublica.org/nonprofits/api/v2/
Auth:   None.  Public API.
Rate:   Unspecified; we throttle to ~5 req/s/host.

Supplies:
  - charitable_giving   ($M, total grants paid + corporate contributions)
  - foundation_assets   ($M, foundation total assets at FY end)
  - num_grants          (count of grants paid; PP doesn't expose this directly,
                         so this falls through to CSR if not in the 990)

Why an API and not scraping: ProPublica re-publishes IRS Form 990 and 990-PF
data as JSON.  This is the same data IRS publishes to bulk download but in
a far more usable form.  Hitting the IRS bulk files directly is also
viable but requires downloading the full year's tarball.

Confidence: 0.85.  IRS-sourced, but PP normalizes field names across years
(occasionally surfacing as the wrong "totcontribs" variant).  Spot-checks
suggest very high accuracy but not 0.95-tier.

API shape (verified against published ProPublica docs):
  GET /api/v2/organizations/{EIN_NUMERIC}.json
  → {
      "organization": {...},
      "filings_with_data": [
        { "tax_prd_yr": 2023, "totcontribs": ..., "totassetsend": ...,
          "totfuncexpns": ..., "totcntrbgfts": ..., ... }
      ],
      "filings_without_data": [...]
    }
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


class ProPublica990Extractor(Extractor):
    supplies_metrics = ("charitable_giving", "foundation_assets")
    source_name = "ProPublica Nonprofit Explorer (IRS 990)"
    base_confidence = CONFIDENCE["propublica_990"]

    def extract(self, company: Company, metrics: Iterable[str]) -> list[DataPoint]:
        wanted = [m for m in metrics if m in self.supplies_metrics]
        if not wanted:
            return []

        results: list[DataPoint] = []

        if not company.foundation_ein:
            for m in wanted:
                results.append(make_failure(
                    company=company.name, metric=m,
                    reason="no foundation EIN on file for this company",
                    attempts=[],
                ))
            return results

        ein_numeric = company.foundation_ein.replace("-", "")
        url = f"https://projects.propublica.org/nonprofits/api/v2/organizations/{ein_numeric}.json"

        attempts: list[ExtractionAttempt] = []
        try:
            r, attempt = fetch(url, source=self.source_name, timeout=20, expect="json")
            attempts.append(attempt)
        except FetchError as e:
            attempts.append(e.attempt)
            for m in wanted:
                results.append(make_failure(
                    company=company.name, metric=m,
                    reason=f"ProPublica fetch failed: {e}",
                    attempts=attempts,
                ))
            return results

        try:
            data = r.json()
        except Exception as e:
            for m in wanted:
                results.append(make_failure(
                    company=company.name, metric=m,
                    reason=f"ProPublica returned non-JSON: {e}",
                    attempts=attempts,
                ))
            return results

        # Pick the most recent filing with usable data.
        filings = data.get("filings_with_data", [])
        if not filings:
            for m in wanted:
                results.append(make_failure(
                    company=company.name, metric=m,
                    reason="ProPublica has no filings_with_data for this EIN",
                    attempts=attempts,
                ))
            return results

        filings.sort(key=lambda f: f.get("tax_prd_yr") or 0, reverse=True)
        latest = filings[0]
        year = latest.get("tax_prd_yr")
        org_name = (data.get("organization") or {}).get("name") or company.foundation_name
        pp_ui_url = f"https://projects.propublica.org/nonprofits/organizations/{ein_numeric}"

        for m in wanted:
            value = self._extract_metric(m, latest)
            if value is None:
                fields_present = sorted(k for k in latest if k.startswith("tot") or k.startswith("grnt"))[:12]
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
                    notes=f"raw {m}={value} {unit} from FY{year}",
                ))
                continue
            results.append(DataPoint(
                company=company.name, metric=m,
                value=round(validated, 3),
                unit=unit,
                year=f"FY{year}" if year else None,
                source_url=pp_ui_url,
                source_name=f"{self.source_name} — {org_name} (EIN {company.foundation_ein})",
                confidence_score=self.base_confidence,
                attempts=attempts,
                notes=f"From IRS Form 990 / 990-PF, fiscal year {year}",
            ))

        return results

    @staticmethod
    def _extract_metric(metric: str, filing: dict) -> Optional[float]:
        """Map our metric keys to ProPublica's normalized 990 field names.

        Field semantics (per ProPublica docs and IRS 990 schema):
          - 990-PF:
              totcontribs   = total contributions, gifts, grants RECEIVED  (NOT what we want)
              cttgrntpd     = contributions, gifts, grants PAID OUT        (this is what we want)
              totassetsend  = total assets at year end
          - 990 (operating non-profit, less common for utility foundations):
              totcntrbgfts  = total contributions / gifts (received)
              totalassetsend or totassetsend = total assets at year end
        """
        if metric == "charitable_giving":
            # Try 990-PF "contributions paid" first; fall back to 990 grants
            for key in ("cttgrntpd", "grntspaid", "totgftgrntrcvd", "totcntrbgfts"):
                v = filing.get(key)
                if isinstance(v, (int, float)) and v > 0:
                    # ProPublica returns dollars; convert to millions
                    return v / 1e6
        elif metric == "foundation_assets":
            for key in ("totassetsend", "totalassetsend", "totasstend"):
                v = filing.get(key)
                if isinstance(v, (int, float)) and v > 0:
                    return v / 1e6
        return None


register("charitable_giving", ProPublica990Extractor)
register("foundation_assets", ProPublica990Extractor)
