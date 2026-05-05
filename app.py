"""
Streamlit UI for the utility benchmark pipeline.

Deploys cleanly to Streamlit Cloud.  See STREAMLIT_DEPLOYMENT.md.
"""

from __future__ import annotations
import io
import json
import sys
from pathlib import Path

# ── Path bootstrap (Streamlit Cloud sometimes runs from a different CWD) ────
# Make sure the directory containing this file is on sys.path so the
# `pipeline/` and `extractors/` packages can be imported regardless of how
# the app was launched.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import streamlit as st

# Wrap third-party deps in try/except so a missing package produces a
# clean diagnostic page in the browser instead of a redacted crash.
_missing_deps = []
try:
    import pandas as pd
except ModuleNotFoundError:
    _missing_deps.append("pandas")
try:
    import plotly.express as px
except ModuleNotFoundError:
    _missing_deps.append("plotly")

if _missing_deps:
    st.set_page_config(page_title="Utility Benchmark — missing dependency", page_icon="⚠️")
    st.error(f"**Missing Python package(s):** `{', '.join(_missing_deps)}`")
    st.markdown(
        f"""
        Streamlit Cloud installs packages listed in `requirements.txt` at the
        repo root.  If you're seeing this, it usually means one of:

        1. **`requirements.txt` is not at the same level as `app.py`** in your
           GitHub repo.  Both files must sit at the **repo root**.  Go to your
           GitHub repo's main page — you should see `app.py` AND `requirements.txt`
           in the file listing without clicking into any folder.

        2. **Streamlit cached a build from before `requirements.txt` existed.**
           In Streamlit Cloud → **Manage app → ⋯ menu → Reboot app**.  If that
           doesn't work, try **Delete app** and create it fresh from the same
           repo — this forces a clean install.

        3. **`requirements.txt` is missing or empty.**  It should contain at
           minimum:
           ```
           streamlit>=1.30
           pandas>=2.0
           plotly>=5.18
           openpyxl>=3.1
           pdfplumber>=0.11
           beautifulsoup4>=4.12
           lxml>=5.0
           requests>=2.31
           tenacity>=8.2
           python-dotenv>=1.0
           anthropic>=0.39
           ```

        **Diagnostics:**
        - `app.py` ran from: `{_HERE}`
        - Files next to app.py: `{sorted(p.name for p in _HERE.iterdir())}`
        - `requirements.txt` present at this level: `{(_HERE / 'requirements.txt').exists()}`
        """
    )
    st.stop()

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Surface import errors clearly in the Streamlit UI so deploy issues are
# easy to diagnose, instead of "data leaks redacted" cryptic messages.
try:
    from pipeline.orchestrator import run_pipeline
    from pipeline.export import to_excel
    from pipeline.ai_layer import verify, generate_insights
    from pipeline.models import METRICS
    from extractors.company_registry import REGISTRY
except ModuleNotFoundError as e:
    st.set_page_config(page_title="Utility Benchmark — import error", page_icon="⚠️")
    st.error(f"**Import failed:** `{e.name}` could not be found.")
    st.markdown(
        f"""
        This usually means the project's folder layout is wrong on the deploy host.

        **Expected layout** (relative to `app.py`):
        ```
        app.py
        run.py
        requirements.txt
        pipeline/
            __init__.py
            orchestrator.py
            ...
        extractors/
            __init__.py
            company_registry.py
            ...
        ```

        **Diagnostics from this run:**
        - `app.py` is at: `{_HERE}`
        - Files next to app.py: `{sorted(p.name for p in _HERE.iterdir())}`
        - `sys.path[0]`: `{sys.path[0]}`

        **Likely fixes:**
        1. On GitHub, make sure `pipeline/` and `extractors/` are at the **same level** as `app.py` — not nested inside another folder.
        2. Verify both folders contain a (possibly empty) `__init__.py`.
        3. In Streamlit Cloud → **Manage app → Settings**, set the *Main file path* to point at `app.py` at the project root (e.g. `app.py`, not `utility_pipeline/app.py`).
        """
    )
    st.stop()


st.set_page_config(
    page_title="Utility Benchmark",
    page_icon="⚡",
    layout="wide",
)

st.title("⚡ Utility Benchmark Pipeline")
st.caption(
    "Government APIs first · per-source extractors · no fallback / mock / seed data."
)

# ── Sidebar — inputs ────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Inputs")

    company_options = [c.name for c in REGISTRY.values()]
    companies = st.multiselect(
        "Companies",
        options=company_options,
        default=company_options[:4],
    )

    metric_options = list(METRICS.keys())
    metric_labels = {k: f"{m['label']} ({m['unit']})" for k, m in METRICS.items()}
    metrics = st.multiselect(
        "Metrics",
        options=metric_options,
        default=["revenue", "charitable_giving", "foundation_assets",
                 "carbon_emissions", "customer_satisfaction"],
        format_func=lambda k: metric_labels[k],
    )

    st.divider()
    use_ai = st.checkbox("Run AI verification + insights", value=True,
                         help="Requires ANTHROPIC_API_KEY env var. Leave off for pure data run.")
    st.caption(
        "Adding a company? Edit `extractors/company_registry.py` to add its CIK, "
        "foundation EIN, and eGRID operator names."
    )
    run_btn = st.button("Run benchmark", type="primary", use_container_width=True,
                        disabled=not (companies and metrics))


# ── Run ─────────────────────────────────────────────────────────────────────
if run_btn:
    st.session_state.pop("datapoints", None)
    progress = st.empty()
    progress.info(f"Running pipeline for {len(companies)} companies × {len(metrics)} metrics…")

    with st.spinner("Extracting…"):
        datapoints = run_pipeline(companies, metrics)
    st.session_state["datapoints"] = datapoints

    audit_text = None
    insights_text = None
    if use_ai:
        with st.spinner("AI verification…"):
            audit_text = verify(datapoints)
        with st.spinner("Generating insights…"):
            insights_text = generate_insights(datapoints, audit_text)
    st.session_state["audit"] = audit_text
    st.session_state["insights"] = insights_text

    progress.success("Done.")


# ── Render results ──────────────────────────────────────────────────────────
if "datapoints" in st.session_state:
    dps = st.session_state["datapoints"]
    df_long = pd.DataFrame([dp.to_flat_row() for dp in dps])

    n_ok = int(df_long["ok"].sum()) if not df_long.empty else 0
    n_fail = len(df_long) - n_ok
    confidences = df_long.loc[df_long["ok"], "confidence_score"].dropna()
    avg_conf = float(confidences.mean()) if len(confidences) else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total pairs", len(df_long))
    c2.metric("Successful", n_ok, f"{(n_ok / max(1, len(df_long))) * 100:.0f}%")
    c3.metric("Failed (null)", n_fail)
    c4.metric("Avg confidence", f"{avg_conf:.2f}" if avg_conf else "—")

    tab_table, tab_charts, tab_failures, tab_attempts, tab_audit, tab_export = st.tabs(
        ["Benchmark", "Charts", "Failures", "Attempts log", "AI Audit & Insights", "Export"]
    )

    with tab_table:
        st.subheader("Wide format (Companies × Metrics)")
        if df_long.empty:
            st.info("No data.")
        else:
            metric_cols = []
            wide = pd.DataFrame({"Company": sorted(df_long["company"].unique())})
            for m in [m for m in METRICS if m in df_long["metric"].unique()]:
                meta = METRICS[m]
                col_name = f"{meta['label']} ({meta['unit']})"
                metric_cols.append(col_name)
                wide[col_name] = wide["Company"].map(
                    lambda c: df_long[(df_long["company"] == c) & (df_long["metric"] == m)
                                      & (df_long["ok"])]["value"].max()
                )
            st.dataframe(wide, use_container_width=True, hide_index=True)

        st.subheader("Long format with sources & confidence")
        st.dataframe(
            df_long[["company", "metric_label", "value", "unit", "year",
                     "confidence_score", "source_name", "source_url", "ok"]],
            use_container_width=True,
            hide_index=True,
        )

    with tab_charts:
        for m in [m for m in METRICS if m in df_long["metric"].unique()]:
            meta = METRICS[m]
            sub = df_long[(df_long["metric"] == m) & (df_long["ok"])].copy()
            if sub.empty:
                st.info(f"No successful values for {meta['label']}.")
                continue
            sub = sub.sort_values("value", ascending=meta["lower_is_better"])
            fig = px.bar(
                sub, x="value", y="company", orientation="h",
                title=f"{meta['label']} ({meta['unit']})",
                color="confidence_score",
                color_continuous_scale="Blues",
                hover_data=["source_name", "year"],
            )
            fig.update_layout(yaxis_title=None)
            st.plotly_chart(fig, use_container_width=True)

    with tab_failures:
        fails = df_long[~df_long["ok"]]
        if fails.empty:
            st.success("No failures — every (company, metric) pair returned a value.")
        else:
            st.warning(f"{len(fails)} pairs returned null — see reasons below. "
                       "**These are not errors to hide; they're honest reports of what data could not be obtained.**")
            st.dataframe(
                fails[["company", "metric_label", "error_reason", "num_attempts", "notes"]],
                use_container_width=True, hide_index=True,
            )

    with tab_attempts:
        rows = []
        for dp in dps:
            for a in dp.attempts:
                rows.append({
                    "company": dp.company, "metric": dp.metric,
                    "source": a.source, "method": a.method, "url": a.url,
                    "status": a.status_code, "ms": a.duration_ms,
                    "ok": a.success, "error": a.error,
                })
        if not rows:
            st.info("No HTTP attempts logged (perhaps all metrics failed at registry lookup).")
        else:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tab_audit:
        a = st.session_state.get("audit")
        i = st.session_state.get("insights")
        if not (a or i):
            st.info(
                "AI verification + insights are off, or `ANTHROPIC_API_KEY` is not set. "
                "Run with the AI checkbox enabled and a valid API key to populate this tab."
            )
        if a:
            st.subheader("Audit")
            st.markdown(a)
        if i:
            st.subheader("Insights")
            st.markdown(i)

    with tab_export:
        st.subheader("Download")

        # Build Excel in-memory
        buf = io.BytesIO()
        # to_excel writes to a path; we round-trip through a temp file
        tmp_path = Path(".cache/_export.xlsx")
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        to_excel(
            dps, tmp_path,
            audit_summary=st.session_state.get("audit"),
            insights=st.session_state.get("insights"),
        )
        buf.write(tmp_path.read_bytes())

        st.download_button(
            "Download Excel workbook (.xlsx)",
            data=buf.getvalue(),
            file_name="utility_benchmark.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
        st.download_button(
            "Download long-format CSV",
            data=df_long.to_csv(index=False).encode("utf-8"),
            file_name="utility_benchmark.csv",
            mime="text/csv",
            use_container_width=True,
        )
        st.download_button(
            "Download JSON (full DataPoints + attempts)",
            data=json.dumps(
                [dp.to_dict() for dp in dps],
                indent=2,
                default=str,   # serializes datetimes, dataclasses-as-dicts already done
            ).encode("utf-8"),
            file_name="utility_benchmark.json",
            mime="application/json",
            use_container_width=True,
        )

else:
    st.info("Configure inputs in the sidebar and click **Run benchmark**.")
