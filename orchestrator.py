"""
Optional AI-driven verification + insights layer.

If ANTHROPIC_API_KEY is set, after the pipeline produces DataPoints we
ask Claude to:
  1. Audit the table for outliers, scale errors, peer inconsistencies
     (with structural context — integrated generators legitimately emit
     ~10× more Scope 1 CO₂ than pure distributors).
  2. Generate strategic insights for a Community Partnerships team
     reading the benchmark.

This layer NEVER modifies values.  If verification flags an outlier, it
is reported in the audit text — the underlying DataPoint is unchanged.
The strict "no fallback" rule means we don't let the AI invent or
substitute values; it only annotates.

If ANTHROPIC_API_KEY is not set, both functions return None and the
pipeline still produces complete data and exports.
"""

from __future__ import annotations
import json
import logging
import os
from typing import Optional

from .models import DataPoint, METRICS

log = logging.getLogger("ai_layer")


def _client():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    try:
        from anthropic import Anthropic
        return Anthropic(api_key=key)
    except ImportError:
        log.warning("anthropic package not installed; AI layer disabled")
        return None


def verify(datapoints: list[DataPoint]) -> Optional[str]:
    """Returns an audit summary string, or None if AI layer disabled."""
    client = _client()
    if not client:
        return None

    rows = []
    for dp in datapoints:
        if not dp.ok:
            continue
        meta = METRICS.get(dp.metric, {})
        rows.append(
            f"{dp.company} | {meta.get('label', dp.metric)} = {dp.value} {dp.unit} "
            f"(source: {dp.source_name}, confidence {dp.confidence_score})"
        )
    if not rows:
        return None

    range_block = "\n".join(
        f"- {meta['label']} ({key}): expected {meta['expected_range']}, "
        f"plausible {meta['plausible_min']}–{meta['plausible_max']} {meta['unit']}"
        for key, meta in METRICS.items()
        if any(dp.metric == key and dp.ok for dp in datapoints)
    )

    prompt = f"""You are auditing a freshly compiled benchmark of US electric/gas utilities. Catch values that look wrong: wrong order of magnitude, wrong unit, peer-group outliers, scale mismatches.

Expected ranges:
{range_block}

Compiled values:
{chr(10).join(rows)}

Structural context that is NOT an error:
- Integrated generators (Duke, Southern, Dominion) legitimately emit ~10× more Scope 1 CO₂ than pure distributors (Con Edison, Eversource, National Grid).
- Charitable Giving and Foundation Assets correlate with company size; small absolute giving from a small utility is not an outlier.

Write 2–4 sentences summarizing data quality, then list any specific values you'd flag for review with a one-line reason each. Do not invent or suggest replacement values."""

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if b.type == "text").strip()
    except Exception as e:
        log.warning("AI verify failed: %s", e)
        return f"(AI verification failed: {e})"


def generate_insights(datapoints: list[DataPoint], audit: Optional[str] = None) -> Optional[str]:
    client = _client()
    if not client:
        return None

    summary_rows = []
    for dp in datapoints:
        if not dp.ok:
            continue
        meta = METRICS.get(dp.metric, {})
        summary_rows.append(
            f"{dp.company} | {meta.get('label', dp.metric)} = {dp.value} {dp.unit}"
        )
    if not summary_rows:
        return None

    prompt = f"""You are a utility-industry strategy analyst writing for a Community Partnerships executive briefing. Below is a benchmark of US electric/gas utilities. Write 4–6 concise insights, each starting with a bolded headline, followed by 1–2 sentences.

Focus on:
- Competitive positioning on operations AND philanthropy
- Structural differences (integrated generator vs distributor)
- Where the company sits on size-normalized philanthropy metrics
- Clear strategic implications a Community Partnerships VP could act on

Plain English. No preamble.

Benchmark:
{chr(10).join(summary_rows)}

{f'Auditor notes: {audit}' if audit else ''}

Return a markdown bullet list. Use **bold** for headlines."""

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if b.type == "text").strip()
    except Exception as e:
        log.warning("AI insights failed: %s", e)
        return f"(AI insights failed: {e})"
