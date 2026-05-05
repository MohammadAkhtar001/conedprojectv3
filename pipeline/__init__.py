"""
Utility benchmark pipeline package.

Modules:
    models         — DataPoint, ExtractionAttempt, METRICS registry
    fetcher        — HTTP layer with retries, rate limiting, attempt logging
    validate       — per-metric validation (range, unit, null/NaN checks)
    orchestrator   — runs registered extractors and assembles DataPoints
    export         — CSV / Excel output
    ai_layer       — optional Anthropic-powered audit + insights
"""

__version__ = "1.0.0"
