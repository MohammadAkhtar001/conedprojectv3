"""
EIA Form 861 reliability extractor.

Source: EIA Form 861 — Annual Electric Power Industry Report
        https://www.eia.gov/electricity/data/eia861/
        Releases an Excel workbook annually with the "Reliability" sheet
        containing SAIDI/SAIFI by utility.

Auth:   None.
Method: Download workbook → read Reliability sheet → look up by EIA Operator ID.

Supplies: saidi (System Average Interruption Duration Index, min/yr)

Currently implemented as a stub that returns a structured failure with
the steps a maintainer would follow to wire it up.  EIA's workbook
column names are stable but the file URL changes per release year, and
each operator has multiple sub-utilities.  This is the right place to
extend, not a thing to scrape from press releases.

Suggested implementation:
  1. Hardcode the latest EIA 861 reliability ZIP URL (one update per year).
  2. Download → unzip → read "Reliability_2023.xlsx" sheet.
  3. Filter rows where Utility Number ∈ company.eia_op_ids.
  4. Pull "SAIDI With MED" column (or "SAIDI Without MED" depending on
     reporting convention you want).  Average across multiple operating
     subsidiaries weighted by customer count.

Confidence when implemented: 0.85 (gov dataset, derived).
"""

from __future__ import annotations
from typing import Iterable

from pipeline.models import DataPoint, make_failure
from .base import Extractor, register
from .company_registry import Company


class EiaReliabilityExtractor(Extractor):
    supplies_metrics = ("saidi",)
    source_name = "EIA Form 861 (reliability)"
    base_confidence = 0.85

    def extract(self, company: Company, metrics: Iterable[str]) -> list[DataPoint]:
        if "saidi" not in metrics:
            return []
        # Honest stub: return null with a clear reason — NOT a fabricated value.
        return [make_failure(
            company=company.name, metric="saidi",
            reason=("EIA Form 861 reliability extractor is not yet implemented. "
                    "See docstring in extractors/eia_reliability.py for the "
                    "implementation outline."),
            attempts=[],
        )]


register("saidi", EiaReliabilityExtractor)
