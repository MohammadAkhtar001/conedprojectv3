"""
Export utilities — CSV and Excel.

The Excel workbook is the artifact a Community Partnerships analyst can
hand off:
  - "Summary"     : run metadata, confidence histogram, extractor health
  - "Benchmark"   : wide companies × metrics matrix with units in headers
  - "Detailed"    : long format, every value with source URL & confidence
  - "Failures"    : every (company, metric) pair that returned null,
                    with reason and list of sources attempted
  - "Attempts"    : raw HTTP attempt log for full debuggability
  - "Charts data" : one block per metric, sorted, ready for native Excel
                    chart insertion

CSV is the long-format equivalent of "Detailed".
"""

from __future__ import annotations
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

from .models import DataPoint, METRICS


# ── CSV ─────────────────────────────────────────────────────────────────────


def to_dataframe(datapoints: Iterable[DataPoint]) -> pd.DataFrame:
    return pd.DataFrame([dp.to_flat_row() for dp in datapoints])


def to_csv(datapoints: Iterable[DataPoint], path: str | Path) -> Path:
    path = Path(path)
    df = to_dataframe(datapoints)
    df.to_csv(path, index=False)
    return path


# ── Excel ───────────────────────────────────────────────────────────────────


def to_excel(
    datapoints: list[DataPoint],
    path: str | Path,
    *,
    summary_text: str | None = None,
    insights: str | None = None,
    audit_summary: str | None = None,
) -> Path:
    """Write a multi-sheet workbook.  Pure data + provenance + summary,
    no fallback / fabricated rows."""

    path = Path(path)
    companies = sorted({dp.company for dp in datapoints})
    metrics_in_run = [m for m in METRICS if any(dp.metric == m for dp in datapoints)]

    ok_dps = [dp for dp in datapoints if dp.ok]
    fail_dps = [dp for dp in datapoints if not dp.ok]
    confidences = [dp.confidence_score for dp in ok_dps if dp.confidence_score is not None]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

    # Source-mix tally (only for successful values)
    source_mix: dict[str, int] = defaultdict(int)
    for dp in ok_dps:
        # Group by extractor base name (before the " — " annotation)
        key = (dp.source_name or "unknown").split(" — ")[0]
        source_mix[key] += 1

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        # ── Summary ────────────────────────────────────────────────────────
        summary_rows = [
            ["Utility Benchmark Pipeline — Run Summary"],
            [f"Generated: {datetime.utcnow().isoformat(timespec='seconds')}Z"],
            [""],
            ["Companies", ", ".join(companies)],
            ["Metrics",   ", ".join(METRICS[m]["label"] for m in metrics_in_run)],
            [""],
            ["── Coverage ──"],
            ["Total (company × metric) pairs", len(datapoints)],
            ["Successful extractions",         len(ok_dps)],
            ["Failed extractions (null)",      len(fail_dps)],
            ["Coverage %", f"{(len(ok_dps) / max(1, len(datapoints))) * 100:.1f}%"],
            [""],
            ["── Confidence ──"],
            ["Average confidence (successful values)", round(avg_conf, 3)],
        ]
        for threshold, label in [(0.90, "≥ 0.90 (gov API exact)"),
                                  (0.75, "≥ 0.75 (PP 990 / derived)"),
                                  (0.60, "≥ 0.60 (CSR PDF)"),
                                  (0.50, "≥ 0.50 (third-party survey)")]:
            n = sum(1 for c in confidences if c >= threshold)
            summary_rows.append([f"Values {label}", n])
        summary_rows.append([""])
        summary_rows.append(["── Source mix ──"])
        for src, n in sorted(source_mix.items(), key=lambda x: -x[1]):
            summary_rows.append([src, n])

        if audit_summary:
            summary_rows += [[""], ["── AI verification ──"], [audit_summary]]
        if insights:
            summary_rows += [[""], ["── AI-generated insights ──"]]
            for line in (insights or "").split("\n"):
                line = line.strip()
                if line:
                    summary_rows.append([line.replace("**", "")])
        if summary_text:
            summary_rows += [[""], ["── Notes ──"], [summary_text]]

        pd.DataFrame(summary_rows).to_excel(
            writer, sheet_name="Summary", index=False, header=False
        )

        # ── Benchmark (wide) ───────────────────────────────────────────────
        wide_rows = []
        for c in companies:
            row = {"Company": c}
            for m in metrics_in_run:
                meta = METRICS[m]
                col = f"{meta['label']} ({meta['unit']})"
                dp = next((d for d in datapoints if d.company == c and d.metric == m), None)
                row[col] = dp.value if dp and dp.ok else None
            wide_rows.append(row)
        pd.DataFrame(wide_rows).to_excel(writer, sheet_name="Benchmark", index=False)

        # ── Detailed (long) ────────────────────────────────────────────────
        detailed = pd.DataFrame([dp.to_flat_row() for dp in datapoints])
        detailed.to_excel(writer, sheet_name="Detailed", index=False)

        # ── Failures ───────────────────────────────────────────────────────
        if fail_dps:
            fail_df = pd.DataFrame([{
                "company": dp.company,
                "metric": dp.metric,
                "metric_label": METRICS.get(dp.metric, {}).get("label", dp.metric),
                "reason": (dp.error or {}).get("reason"),
                "attempted_sources": ", ".join((dp.error or {}).get("attempted_sources", [])),
                "attempt_count": len(dp.attempts),
                "notes": dp.notes,
            } for dp in fail_dps])
            fail_df.to_excel(writer, sheet_name="Failures", index=False)

        # ── Attempts log ───────────────────────────────────────────────────
        attempt_rows = []
        for dp in datapoints:
            for a in dp.attempts:
                attempt_rows.append({
                    "company": dp.company,
                    "metric": dp.metric,
                    "source": a.source,
                    "url": a.url,
                    "method": a.method,
                    "status_code": a.status_code,
                    "content_type": a.content_type,
                    "response_bytes": a.response_bytes,
                    "duration_ms": a.duration_ms,
                    "success": a.success,
                    "error": a.error,
                    "preview": a.response_preview,
                    "timestamp": a.timestamp,
                })
        if attempt_rows:
            pd.DataFrame(attempt_rows).to_excel(writer, sheet_name="Attempts", index=False)

        # ── Charts data (per metric, sorted) ───────────────────────────────
        chart_rows = []
        for m in metrics_in_run:
            meta = METRICS[m]
            chart_rows.append({"chart": f"{meta['label']} ({meta['unit']})"})
            chart_rows.append({"chart": "Company", "value": "Value", "rank": "Rank"})
            metric_dps = sorted(
                [dp for dp in datapoints if dp.metric == m and dp.ok],
                key=lambda d: d.value if d.value is not None else 0,
                reverse=not meta["lower_is_better"],
            )
            for i, dp in enumerate(metric_dps, 1):
                chart_rows.append({"chart": dp.company, "value": dp.value, "rank": i})
            chart_rows.append({})
        pd.DataFrame(chart_rows).to_excel(
            writer, sheet_name="Charts data", index=False, header=False
        )

    return path
