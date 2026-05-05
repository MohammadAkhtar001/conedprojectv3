"""
Orchestrator.

Walks the (company, metric) matrix.  For each pair, finds the registered
extractors in priority order, tries each one, and returns the first
DataPoint that succeeds.  If none succeed, returns a single failure
DataPoint with the union of all attempts attached.

There is no fallback to mock/seed data.  Failure stays failure.
"""

from __future__ import annotations
import logging
from typing import Iterable

from .models import DataPoint, ExtractionAttempt, make_failure, METRICS
from extractors import EXTRACTOR_PRIORITY
from extractors.company_registry import resolve

log = logging.getLogger("orchestrator")


def run_pipeline(company_names: Iterable[str], metrics: Iterable[str]) -> list[DataPoint]:
    """Main entry point.  Returns one DataPoint per (company, metric)."""
    results: list[DataPoint] = []

    for raw_name in company_names:
        company = resolve(raw_name)
        if not company:
            for m in metrics:
                results.append(make_failure(
                    company=raw_name, metric=m,
                    reason=(f"company '{raw_name}' not found in registry. "
                            "Add an entry to extractors/company_registry.py."),
                    attempts=[],
                ))
            continue

        for metric in metrics:
            if metric not in METRICS:
                results.append(make_failure(
                    company=company.name, metric=metric,
                    reason=f"unknown metric '{metric}'. See pipeline/models.py METRICS.",
                    attempts=[],
                ))
                continue

            results.append(_extract_one(company, metric))

    return results


def _extract_one(company, metric: str) -> DataPoint:
    extractor_classes = EXTRACTOR_PRIORITY.get(metric, [])
    if not extractor_classes:
        return make_failure(
            company=company.name, metric=metric,
            reason=f"no extractor registered for metric '{metric}'",
            attempts=[],
        )

    all_attempts: list[ExtractionAttempt] = []
    failure_reasons: list[str] = []

    for cls in extractor_classes:
        log.info("→ %s :: %s :: trying %s", company.name, metric, cls.__name__)
        extractor = cls()
        try:
            dps = extractor.extract(company, [metric])
        except Exception as e:
            log.exception("extractor %s blew up: %s", cls.__name__, e)
            failure_reasons.append(f"{cls.__name__}: unhandled exception: {e}")
            continue

        # Each extract() call returns a list (some extractors batch).  We
        # want the entry for `metric` specifically.
        for dp in dps:
            if dp.metric != metric:
                continue
            all_attempts.extend(dp.attempts)
            if dp.ok:
                # Prepend context: which extractors we tried before this one
                if len(extractor_classes) > 1:
                    earlier = [c.__name__ for c in extractor_classes
                               if c is not cls and EXTRACTOR_PRIORITY[metric].index(c)
                                  < EXTRACTOR_PRIORITY[metric].index(cls)]
                    if earlier:
                        prefix = f"Took data from {cls.__name__} after trying: {earlier}. "
                        dp.notes = (prefix + (dp.notes or "")).strip()
                # Replace the per-extractor attempts list with the union so
                # a downstream consumer sees the full audit trail.
                dp.attempts = all_attempts
                return dp
            else:
                reason = (dp.error or {}).get("reason") if dp.error else "unknown"
                failure_reasons.append(f"{cls.__name__}: {reason}")

    # All extractors failed.  Emit a single failure DataPoint.
    return make_failure(
        company=company.name, metric=metric,
        reason="all registered extractors failed; see attempts and notes for details",
        attempts=all_attempts,
        notes=" | ".join(failure_reasons) if failure_reasons else None,
    )
