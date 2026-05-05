#!/usr/bin/env python3
"""
CLI entry point.

Usage:
    python run.py --companies "Con Edison" "Duke Energy" \\
                  --metrics revenue charitable_giving carbon_emissions \\
                  --out results.xlsx [--csv results.csv] [--json results.json]
"""

from __future__ import annotations
import argparse
import json
import logging
import sys
from pathlib import Path

# Path bootstrap so this script works from any CWD
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from pipeline.orchestrator import run_pipeline
from pipeline.export import to_csv, to_excel
from pipeline.ai_layer import verify, generate_insights
from pipeline.models import METRICS


def main():
    p = argparse.ArgumentParser(description="Utility Benchmark Pipeline")
    p.add_argument("--companies", nargs="+", required=True,
                   help="Company names (must match registry; see extractors/company_registry.py)")
    p.add_argument("--metrics", nargs="+", required=True,
                   help=f"Metric keys: {', '.join(METRICS.keys())}")
    p.add_argument("--out", default="results.xlsx",
                   help="Path to Excel output (default: results.xlsx)")
    p.add_argument("--csv", default=None, help="Optional CSV output path")
    p.add_argument("--json", default=None, help="Optional JSON output path")
    p.add_argument("--no-ai", action="store_true",
                   help="Skip AI verification and insights even if API key is set")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)-22s %(levelname)-7s %(message)s",
    )

    for m in args.metrics:
        if m not in METRICS:
            print(f"ERROR: unknown metric '{m}'. Valid: {list(METRICS)}", file=sys.stderr)
            sys.exit(2)

    print(f"Running pipeline for {len(args.companies)} companies × {len(args.metrics)} metrics…")
    datapoints = run_pipeline(args.companies, args.metrics)

    n_ok = sum(1 for dp in datapoints if dp.ok)
    n_fail = len(datapoints) - n_ok
    print(f"Done. {n_ok} succeeded, {n_fail} returned null (with reason).")

    audit = None
    insights = None
    if not args.no_ai:
        print("Running AI verification…")
        audit = verify(datapoints)
        if audit:
            print("\n── AI Audit ──\n" + audit + "\n")
        print("Generating insights…")
        insights = generate_insights(datapoints, audit)
        if insights:
            print("\n── AI Insights ──\n" + insights + "\n")

    out_path = Path(args.out)
    to_excel(datapoints, out_path, audit_summary=audit, insights=insights)
    print(f"Excel  → {out_path}")

    if args.csv:
        to_csv(datapoints, args.csv)
        print(f"CSV    → {args.csv}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump([dp.to_dict() for dp in datapoints], f, indent=2, default=str)
        print(f"JSON   → {args.json}")


if __name__ == "__main__":
    main()
