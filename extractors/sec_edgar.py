"""
SEC EDGAR XBRL company-facts extractor.

Source: https://data.sec.gov/api/xbrl/companyfacts/CIK{10digit}.json
Auth:   None.  Requires a contact email in User-Agent (set via
        SEC_USER_AGENT env var) per SEC fair-access policy.
        Generic UAs receive 403 "Host not in allowlist".
Rate:   SEC asks for ≤ 10 req/s.  fetcher.py throttles to ~10 req/s/host.

Supplies: revenue (FY most recent, from form="10-K" fp="FY")

Why an extractor and not "scraping": SEC publishes structured XBRL data
as a stable JSON API.  Scraping the HTML EDGAR pages or 10-K PDFs is
strictly worse — it's slower, more brittle, and produces lower-confidence
data.

Confidence: 0.95.  This is the regulator-of-record JSON, exact match
to a tagged us-gaap:Revenues fact in a 10-K filing.
"""

from __future__ import annotations
import logging
from typing import Iterable

from pipeline.models import DataPoint, ExtractionAttempt, METRICS, CONFIDENCE, make_failure
from pipeline.fetcher import fetch, FetchError
from pipeline.validate import validate_value, ValidationFailure
from .base import Extractor, register
from .company_registry import Company

log = logging.getLogger("sec_edgar")

# us-gaap tags that indicate annual revenue — listed in priority order.
# Different filers use slightly different XBRL tags; we try the most
# common one (Revenues) first, then narrower fallbacks.
_REVENUE_TAGS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
)


class SecEdgarExtractor(Extractor):
    supplies_metrics = ("revenue",)
    source_name = "SEC EDGAR (XBRL company-facts)"
    base_confidence = CONFIDENCE["sec_xbrl_exact"]

    def extract(self, company: Company, metrics: Iterable[str]) -> list[DataPoint]:
        wanted = [m for m in metrics if m in self.supplies_metrics]
        if not wanted:
            return []

        attempts: list[ExtractionAttempt] = []

        if not company.cik:
            return [
                make_failure(
                    company=company.name, metric="revenue",
                    reason="no SEC CIK on file for this company "
                           "(may not be SEC-registered, e.g. UK parent)",
                    attempts=attempts,
                )
            ]

        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{company.cik}.json"
        try:
            r, attempt = fetch(url, source=self.source_name, timeout=20, expect="json")
            attempts.append(attempt)
        except FetchError as e:
            attempts.append(e.attempt)
            return [
                make_failure(
                    company=company.name, metric="revenue",
                    reason=f"SEC EDGAR fetch failed: {e}",
                    attempts=attempts,
                )
            ]

        try:
            data = r.json()
        except Exception as e:
            return [
                make_failure(
                    company=company.name, metric="revenue",
                    reason=f"SEC EDGAR returned non-JSON: {e}",
                    attempts=attempts,
                )
            ]

        us_gaap = data.get("facts", {}).get("us-gaap", {})
        latest = self._find_latest_annual_revenue(us_gaap)
        if not latest:
            available = sorted(k for k in us_gaap if "evenue" in k.lower() or "ales" in k.lower())[:8]
            return [
                make_failure(
                    company=company.name, metric="revenue",
                    reason=(
                        "no recognized annual revenue XBRL tag found; "
                        f"closest tags present: {available}"
                    ),
                    attempts=attempts,
                )
            ]

        tag, fact = latest
        # SEC reports raw USD; convert to billions (pipeline unit).
        value_b = fact["val"] / 1e9
        unit = METRICS["revenue"]["unit"]

        try:
            validated = validate_value("revenue", value_b, unit)
        except ValidationFailure as ve:
            return [
                make_failure(
                    company=company.name, metric="revenue",
                    reason=f"SEC value rejected by validator: {ve.reason}",
                    attempts=attempts,
                    notes=f"raw value was ${value_b:.2f}B from tag {tag}, end={fact.get('end')}",
                )
            ]

        # Filing URL for the source citation
        accn = fact.get("accn", "")
        accn_clean = accn.replace("-", "")
        filing_url = (
            f"https://www.sec.gov/Archives/edgar/data/{int(company.cik)}/{accn_clean}/"
            f"{accn}-index.htm"
            if accn else f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={company.cik}&type=10-K"
        )

        return [
            DataPoint(
                company=company.name,
                metric="revenue",
                value=round(validated, 3),
                unit=unit,
                year=fact.get("fy") and f"FY{fact['fy']}" or fact.get("end"),
                source_url=filing_url,
                source_name=f"{self.source_name} — tag us-gaap:{tag}",
                confidence_score=self.base_confidence,
                attempts=attempts,
                notes=f"From 10-K filing accn {accn}, period end {fact.get('end')}",
            )
        ]

    def _find_latest_annual_revenue(self, us_gaap: dict):
        """Walk the priority list of revenue tags; for each, find the most
        recent annual (fp='FY' on a 10-K) USD figure.  Return (tag, fact)."""
        for tag in _REVENUE_TAGS:
            facts = us_gaap.get(tag, {}).get("units", {}).get("USD", [])
            annual = [
                f for f in facts
                if f.get("form") == "10-K" and f.get("fp") == "FY" and f.get("val") is not None
            ]
            if not annual:
                continue
            annual.sort(key=lambda f: f.get("end", ""), reverse=True)
            return (tag, annual[0])
        return None


# Register on import
register("revenue", SecEdgarExtractor)
