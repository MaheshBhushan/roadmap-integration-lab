"""Command line entry point: `roadlab run`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import DEFAULT_BBOX, DEFAULT_REGION, Settings
from .pipeline import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="roadlab", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="acquire snapshot, load PostGIS + MongoDB, validate, compare, publish")
    p.add_argument("--snapshot-date", help="snapshot folder name (default: today, YYYY-MM-DD)")
    p.add_argument("--snapshot-dir", type=Path, help="use an existing snapshot directory (with manifest.json)")
    p.add_argument("--refresh", action="store_true", help="re-download even if the dated snapshot exists")
    p.add_argument("--overture-release", help="Overture release, e.g. 2026-10-21.0 (default: latest)")
    p.add_argument("--bbox", type=float, nargs=4, metavar=("XMIN", "YMIN", "XMAX", "YMAX"),
                   default=list(DEFAULT_BBOX))
    p.add_argument("--region", default=DEFAULT_REGION)
    p.add_argument("--data-dir", type=Path, default=Path("data"))
    p.add_argument("--reports-dir", type=Path, default=Path("reports"))
    p.add_argument("--no-publish", action="store_true", help="validate and report only")
    args = parser.parse_args(argv)

    settings = Settings(region=args.region, bbox=tuple(args.bbox), data_dir=args.data_dir,
                        reports_dir=args.reports_dir)
    result = run(settings, snapshot_dir=args.snapshot_dir, snapshot_date=args.snapshot_date,
                 refresh=args.refresh, overture_release=args.overture_release, publish=not args.no_publish)
    for check in result.failed:
        print(f"FAIL {check.name}: observed {check.observed}, expected {check.expected}", file=sys.stderr)
    status = "published" if result.published else "NOT published"
    print(f"{len(result.checks) - len(result.failed)}/{len(result.checks)} checks passed; {status}; "
          f"{result.runtime['total_seconds']} s, peak RSS {result.runtime['peak_rss_mib']} MiB; "
          f"report: {result.report_dir / 'report.md'}")
    return 0 if not result.failed else 2


if __name__ == "__main__":
    sys.exit(main())
