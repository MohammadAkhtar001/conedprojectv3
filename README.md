# Utility Benchmark Pipeline

Production-grade data pipeline for benchmarking US electric & gas utilities on
operational, ESG, and philanthropy metrics. Built for the Con Edison Community
Partnerships team.

## What this is

A modular Python pipeline with one extractor per data source, no fallback /
mock / seed data, and confidence scoring for every value. When data can't be
found, the pipeline returns a structured error with the list of attempts —
**never** a fabricated value.

A Streamlit UI (`app.py`) wraps the pipeline for interactive use; a CLI
(`run.py`) is provided for batch / scripted runs.

## Why the previous versions failed (root cause analysis)

The previous Streamlit tool relied on:

1. **A "universal scraper"** — one set of regex patterns applied against
   whatever HTML came back from a generic `requests.get()`. SEC 10-Ks, J.D.
   Power result pages, and IRS 990 PDFs have nothing in common structurally;
   one parser cannot handle all three.
2. **Brittle CSS / regex selectors** that broke whenever a source changed
   markup. There was no per-source extractor isolating that fragility.
3. **No JS rendering.** Many corporate sustainability reports and J.D. Power
   pages are SPAs that return an empty `<div id="root">` to plain `requests`.
   The scraper would "succeed" (200 OK) and extract nothing, then silently
   fall through.
4. **No anti-bot handling.** SEC's `data.sec.gov` requires a contact email
   in the User-Agent. Generic `requests` UAs get a `403 Host not in
   allowlist` immediately.
5. **Misuse of scraping when JSON APIs exist.** SEC EDGAR has a fully
   documented JSON API (`/api/xbrl/companyfacts/`). ProPublica Nonprofit
   Explorer has a JSON API for IRS 990 data. EPA eGRID publishes XLSX
   datasets. The previous tool scraped HTML pages built *on top* of those
   APIs instead of hitting the APIs directly.
6. **No validation layer.** Whatever the regex pulled went straight through.
   A regex matching the wrong sentence would happily return a $50B revenue
   when the real number was $5B.
7. **Fallback masking failure.** When extraction failed, the system silently
   substituted "verified seed data" — making it look like extraction had
   succeeded. This is the failure mode the new pipeline is designed to
   eliminate.

## Architecture

```
┌─ Source Layer ────────────────────────────────────────────────┐
│   sec_edgar.py        Revenue (SEC company-facts JSON API)    │
│   propublica_990.py   Foundation grants/assets (PP API)       │
│   epa_egrid.py        Carbon emissions (EPA eGRID dataset)    │
│   eia_reliability.py  SAIDI / generation mix (EIA Form 861)   │
│   jd_power.py         Customer satisfaction (HTML, fallback)  │
│   csr_report.py       Catch-all: locate & parse CSR PDFs      │
└───────────────────────────────────────────────────────────────┘
                              │
┌─ Fetch Layer (pipeline/fetcher.py) ───────────────────────────┐
│   • requests with retries + jitter + per-host rate limiting   │
│   • Per-source User-Agent (SEC requires contact email)        │
│   • Optional Playwright fallback for JS-heavy pages           │
│   • Full request/response logging                             │
└───────────────────────────────────────────────────────────────┘
                              │
┌─ Parse Layer (per-extractor) ─────────────────────────────────┐
│   • SEC: parse XBRL JSON, pick latest 10-K FY value           │
│   • PP: parse 990-PF fields (totcontribs, totassetsend, ...)  │
│   • eGRID: read XLSX, sum plant-level CO2 by operator         │
│   • PDF: pdfplumber for tables, regex for narrative figures   │
└───────────────────────────────────────────────────────────────┘
                              │
┌─ Validation Layer (pipeline/validate.py) ─────────────────────┐
│   • Range checks per metric (Revenue 1B-80B, etc.)            │
│   • Unit consistency (Scope 1 only, USD millions, /100 score) │
│   • Empty-value rejection                                     │
│   • Returns (ok, reason) — failure → null + reason, NEVER     │
│     a substitute value                                        │
└───────────────────────────────────────────────────────────────┘
                              │
┌─ Output Layer (pipeline/orchestrator.py) ─────────────────────┐
│   For each (company, metric):                                 │
│   {                                                           │
│     company, metric, value, unit, year,                       │
│     source_url, source_name, confidence_score,                │
│     attempts: [...], error?: { reason, attempted_sources }    │
│   }                                                           │
└───────────────────────────────────────────────────────────────┘
```

## Confidence scoring

| Score | Meaning                                          | Source examples         |
|------:|--------------------------------------------------|-------------------------|
|  0.95 | Government API / regulator dataset, exact match  | SEC XBRL, EPA eGRID     |
|  0.85 | Government API / regulator dataset, derived      | EIA generation→%; eGRID summed |
|  0.75 | ProPublica 990 (IRS-sourced JSON, normalized)    | foundation giving       |
|  0.60 | Company CSR / sustainability report (PDF parsed) | renewable %, volunteer hrs |
|  0.50 | Third-party survey published page (J.D. Power)   | customer satisfaction   |
|  0.30 | Press release / news (last resort)               |                         |

A run is considered acceptable when the median confidence is ≥ 0.70 and no
data point is below 0.50 without an explicit user opt-in.

## Strict rules followed

- **No fallback / mock / seed data anywhere in the pipeline.** Failed
  extraction returns `value: null` with a structured `error` block.
- Each source has its own extractor — no universal scraper.
- APIs preferred over scraping. Scraping is only used where no API exists.
- All HTTP traffic is logged with status, content-type, response length,
  and first 500 chars of the body. Failures are visible.
- Extractors expose `attempts` so callers see which sources were tried in
  what order with what results.

## What the pipeline can / can't do today

**Working with real APIs, no scraping needed:**
- Revenue (SEC EDGAR company-facts JSON)
- Foundation Charitable Giving / Assets / Grant counts (ProPublica 990)
- Carbon Emissions Scope 1 (EPA eGRID dataset)

**Requires HTML scraping + JS render fallback:**
- Renewable Energy % (some utilities expose it via EIA, others CSR-only)
- SAIDI (state PUC dockets, format varies by state)
- Customer Satisfaction (J.D. Power result pages)
- Energy Assistance ($M), Volunteer Hours, Employee Match (CSR PDFs only)

**Honest expectation:** with all extractors enabled, ~70-85% of metrics
return a real value with confidence ≥ 0.60 for major US utilities. The
remaining 15-30% return `null` with an error reason. **Those nulls are
features, not bugs** — they're the difference between a benchmark you can
defend and one you can't.

## Setup

```bash
git clone <this repo>
cd utility_pipeline
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium                          # for JS-render fallback (optional)

# Set required environment variables
cp .env.example .env
# Edit .env to set SEC_USER_AGENT (required by SEC) and ANTHROPIC_API_KEY (optional, for AI verify/insights)
```

## Run

**CLI:**
```bash
python run.py --companies "Con Edison" "Duke Energy" \
              --metrics revenue charitable_giving carbon_emissions \
              --out results.csv
```

**Streamlit:**
```bash
streamlit run app.py
```

## Deploying to Streamlit Cloud

1. Push this repo to GitHub.
2. On share.streamlit.io: New app → point at `app.py` → Advanced settings →
   add the secrets from `.env.example` (especially `SEC_USER_AGENT` and
   `ANTHROPIC_API_KEY`).
3. Note: Streamlit Cloud runs on Linux without a system browser by default.
   Playwright fallback works there but you may need to add a `packages.txt`
   with system deps. See `STREAMLIT_DEPLOYMENT.md`.

## Project layout

```
utility_pipeline/
├── README.md                    ← this file
├── STREAMLIT_DEPLOYMENT.md
├── requirements.txt
├── packages.txt                 ← apt packages for Streamlit Cloud (Playwright)
├── .env.example
├── .gitignore
├── run.py                       ← CLI entry point
├── app.py                       ← Streamlit UI
├── pipeline/
│   ├── __init__.py
│   ├── models.py                ← DataPoint, ExtractionAttempt, etc.
│   ├── fetcher.py               ← HTTP layer w/ retries, rate limit, logging
│   ├── validate.py              ← validation rules per metric
│   ├── orchestrator.py          ← runs extractors, assembles output
│   ├── ai_layer.py              ← optional Anthropic verification + insights
│   └── export.py                ← CSV / Excel export
└── extractors/
    ├── __init__.py              ← registry mapping (company,metric) → extractor
    ├── base.py                  ← Extractor abstract base class
    ├── company_registry.py      ← name → CIK / EIN / utility-ID mapping
    ├── sec_edgar.py             ← Revenue
    ├── propublica_990.py        ← foundation philanthropy metrics
    ├── epa_egrid.py             ← Scope 1 emissions
    ├── eia_reliability.py       ← SAIDI, renewable %
    ├── jd_power.py              ← customer satisfaction
    └── csr_report.py            ← catch-all for PDF-disclosed metrics
```

## License

MIT.
