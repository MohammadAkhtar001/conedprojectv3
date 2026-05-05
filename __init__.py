"""
Per-source extractor abstraction.

Each subclass:
 1. Knows EXACTLY how to extract one or more metrics from one source.
 2. Returns DataPoint objects.  On failure → DataPoint with value=None and
    structured error (NEVER a fallback value).
 3. Records every HTTP attempt as an ExtractionAttempt for transparency.

Extractors are NOT chained internally — orchestration (i.e. trying SEC
first, falling back to CSR if SEC has no data) is handled by the
orchestrator using the extractor registry.  This keeps each extractor
focused and easy to reason about.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Iterable
from pipeline.models import DataPoint
from .company_registry import Company


class Extractor(ABC):
    """Subclasses declare which metrics they can produce, then implement
    extract() to actually produce them for a given Company."""

    # Metric keys this extractor can supply.  e.g. ("revenue",) or
    # ("charitable_giving", "foundation_assets", "num_grants").
    supplies_metrics: tuple[str, ...] = ()

    # Human-friendly source name surfaced in DataPoint.source_name.
    source_name: str = "unknown"

    # Confidence score for values produced by this extractor (default).
    # Individual values may override (e.g. eGRID exact match vs derived).
    base_confidence: float = 0.50

    @abstractmethod
    def extract(self, company: Company, metrics: Iterable[str]) -> list[DataPoint]:
        """Extract the requested subset of supplies_metrics for `company`.

        `metrics` will only contain keys this extractor supplies.  Return
        one DataPoint per requested metric — successes AND failures."""
        raise NotImplementedError


# ── Registry ────────────────────────────────────────────────────────────────
# Mapping metric key → ordered list of extractors to try, BEST FIRST.
# The orchestrator walks the list in order; first extractor to return a
# DataPoint with ok=True wins.  If none succeed, the orchestrator emits a
# failure DataPoint with the union of all attempts.

# Populated by extractors/__init__.py to avoid import cycles.
EXTRACTOR_PRIORITY: dict[str, list[type[Extractor]]] = {}


def register(metric: str, extractor_cls: type[Extractor]):
    EXTRACTOR_PRIORITY.setdefault(metric, []).append(extractor_cls)
