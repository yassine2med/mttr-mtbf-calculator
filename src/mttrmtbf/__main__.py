"""Command line entry: python -m mttrmtbf report|formula."""

from __future__ import annotations

import argparse
import csv
import json
import sys

from .core import (FailureLogError, availability_from, compute, fleet, load_failures, parse_time)

SORTS = {
    "asset": lambda m: m.asset_id,
    "failures": lambda m: -m.failures,
    "mttr": lambda m: -(m.mttr_hours or 0),
    "mtbf": lambda m: m.mtbf_hours if m.mtbf_hours is not None else float("inf"),
    "availability": lambda m: m.availability,
}


def _num(value, digits=1):
    return "-" if value is None else f"{value:,.{digits}f}"


def main(argv: list[str] | None = None) -> int:
    """Return 0 on success, 1 when a failure is still open, 2 on bad input."""
    parser = argparse.ArgumentParser(prog="mttrmtbf", description="MTTR, MTBF and availability from a failure log")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("report", help="metrics per asset")
    r.add_argument("file")
    r.add_argument("--start", required=True, help="window start, YYYY-MM-DD")
    r.add_argument("--end", required=True, help="window end, YYYY-MM-DD")
    r.add_argument("--sort", choices=sorted(SORTS), default="asset")
    r.add_argument("--format", choices=["table", "csv", "json"], default="table")
    f = sub.add_parser("formula", help="availability from an MTBF and an MTTR")
    f.add_argument("mtbf", type=float)
    f.add_argument("mttr", type=float)
    args = parser.parse_args(argv)

    try:
        if args.command == "formula":
            print(f"availability = {args.mtbf:g} / ({args.mtbf:g} + {args.mttr:g}) = {availability_from(args.mtbf, args.mttr):.4f}")
            return 0
        metrics = compute(load_failures(args.file), parse_time(args.start), parse_time(args.end))
    except FileNotFoundError:
        print(f"File not found: {args.file}", file=sys.stderr)
        return 2
    except FailureLogError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    rows = sorted(metrics.values(), key=SORTS[args.sort])
    total = fleet(metrics)
    if args.format == "json":
        print(json.dumps([{"asset": m.asset_id, "failures": m.failures, "downtime_hours": round(m.downtime_hours, 2),
                           "mttr_hours": None if m.mttr_hours is None else round(m.mttr_hours, 2),
                           "mtbf_hours": None if m.mtbf_hours is None else round(m.mtbf_hours, 2),
                           "availability": round(m.availability, 5), "repeat_failures": m.repeat_failures,
                           "open_failures": m.open_failures} for m in rows], indent=2))
    elif args.format == "csv":
        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow(["asset", "failures", "downtime_hours", "mttr_hours", "mtbf_hours", "availability", "repeat", "open"])
        for m in rows:
            writer.writerow([m.asset_id, m.failures, f"{m.downtime_hours:.2f}", _num(m.mttr_hours, 2).replace(",", ""),
                             _num(m.mtbf_hours, 2).replace(",", ""), f"{m.availability:.5f}", m.repeat_failures, m.open_failures])
    else:
        print(f"{'asset':<11}{'fails':>6}{'down h':>9}{'MTTR h':>9}{'MTBF h':>10}{'avail %':>9}{'repeat':>8}{'open':>6}")
        for m in rows + [total]:
            label = "ALL" if m is total else m.asset_id
            print(f"{label:<11}{m.failures:>6}{m.downtime_hours:>9.1f}{_num(m.mttr_hours):>9}{_num(m.mtbf_hours):>10}"
                  f"{m.availability * 100:>9.2f}{m.repeat_failures:>8}{m.open_failures:>6}")
    return 1 if total.open_failures else 0


if __name__ == "__main__":
    sys.exit(main())
