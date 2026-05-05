"""
J.D. Power Customer Satisfaction extractor.

Source: https://www.jdpower.com/business/press-releases (annual study results)
Auth:   None.
Method: HTML scraping with Playwright fallback (J.D. Power's site is
        partially client-rendered).

Supplies: customer_satisfaction (J.D. Power Residential Electric
          Satisfaction Study, normalized to /100 from raw /1000)

Honest limitation: J.D. Power's results are published as press releases
with company scores embedded in narrative text.  There is no JSON API
and no downloadable dataset for free.  This extractor:

  1. Fetches the most recent annual press release URL (hardcoded each
     year — when they publish a 2026 study, update PRESS_RELEASE_URL).
  2. Renders it with Playwright if static HTML doesn't contain the
     company's score.
  3. Looks for the company name in the page and pulls the nearest
     3-digit score.

If the page format changes, this returns null with a clear reason rather
than guessing — exactly what the directive requires.

Confidence: 0.50.  Third-party survey, scraped from press release.
"""

from __future__ import annotations
import logging
import re
from typing import Iterable

from pipeline.models import DataPoint, ExtractionAttempt, METRICS, CONFIDENCE, make_failure
from pipeline.fetcher import fetch, FetchError, fetch_rendered_html
from pipeline.validate import validate_value, ValidationFailure
from .base import Extractor, register
from .company_registry import Company

log = logging.getLogger("jd_power")

# Most recent press release for the Residential Electric Satisfaction Study.
# Update annually.  This is one URL per year; J.D. Power publishes the East
# and West regions on the same page in recent years.
PRESS_RELEASE_URL = (
    "https://www.jdpower.com/business/press-releases/"
    "2024-electric-utility-residential-customer-satisfaction-study"
)
STUDY_YEAR = 2024


class JdPowerExtractor(Extractor):
    supplies_metrics = ("customer_satisfaction",)
    source_name = "J.D. Power Residential Electric Satisfaction Study"
    base_confidence = CONFIDENCE["third_party_survey"]

    def extract(self, company: Company, metrics: Iterable[str]) -> list[DataPoint]:
        if "customer_satisfaction" not in metrics:
            return []

        attempts: list[ExtractionAttempt] = []
        html: str | None = None

        # Try plain HTTP first
        try:
            r, attempt = fetch(PRESS_RELEASE_URL, source=self.source_name, timeout=20)
            attempts.append(attempt)
            html = r.text
        except FetchError as e:
            attempts.append(e.attempt)

        # If the static HTML doesn't mention the company name, try Playwright
        if html is None or company.name.split()[0].lower() not in html.lower():
            try:
                rendered, attempt = fetch_rendered_html(
                    PRESS_RELEASE_URL,
                    source=self.source_name,
                    wait_selector=None,
                    timeout_ms=20000,
                )
                attempts.append(attempt)
                html = rendered
            except FetchError as e:
                attempts.append(e.attempt)

        if not html:
            return [make_failure(
                company=company.name, metric="customer_satisfaction",
                reason="J.D. Power page could not be retrieved (static + Playwright both failed)",
                attempts=attempts,
            )]

        score = self._extract_company_score(html, company)
        if score is None:
            return [make_failure(
                company=company.name, metric="customer_satisfaction",
                reason=("no score found near company name in J.D. Power press release; "
                        "page format may have changed, or company may not have ranked"),
                attempts=attempts,
                notes=f"Searched URL: {PRESS_RELEASE_URL}",
            )]

        # Raw J.D. Power scores are out of 1000 — normalize to /100
        normalized = score / 10.0
        unit = METRICS["customer_satisfaction"]["unit"]

        try:
            validated = validate_value("customer_satisfaction", normalized, unit)
        except ValidationFailure as ve:
            return [make_failure(
                company=company.name, metric="customer_satisfaction",
                reason=f"value rejected by validator: {ve.reason}",
                attempts=attempts,
                notes=f"raw score from J.D. Power: {score}/1000",
            )]

        return [DataPoint(
            company=company.name, metric="customer_satisfaction",
            value=round(validated, 1),
            unit=unit,
            year=str(STUDY_YEAR),
            source_url=PRESS_RELEASE_URL,
            source_name=f"{self.source_name} {STUDY_YEAR}",
            confidence_score=self.base_confidence,
            attempts=attempts,
            notes=f"Raw J.D. Power score {score}/1000 normalized to /100",
        )]

    def _extract_company_score(self, html: str, company: Company) -> float | None:
        """Find the company name in the press release HTML and pull the
        nearest 3-digit number that looks like a J.D. Power score (600-900)."""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text("\n", strip=True)

        # Try canonical name first, then aliases
        candidates = (company.name,) + company.aliases
        for needle in candidates:
            idx = text.lower().find(needle.lower())
            if idx == -1:
                continue
            window = text[max(0, idx - 200): idx + 400]
            # J.D. Power scores are 3-digit, typically 600-900
            for m in re.finditer(r"\b(6\d{2}|7\d{2}|8\d{2})\b", window):
                return float(m.group(1))
        return None


register("customer_satisfaction", JdPowerExtractor)
