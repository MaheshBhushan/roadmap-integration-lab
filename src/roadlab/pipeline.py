"""End-to-end run: snapshot -> read -> validate -> PostGIS + MongoDB -> compare -> gated publish -> report."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import shapely

from . import acquire, mongo, pg, report
from .compare import compare
from .config import Settings
from .metrics import StageProfiler
from .model import Segment
from .osm import read_pbf
from .overture import read_parquet
from .validate import Check, coordinate_checks


@dataclass
class RunResult:
    published: bool
    report_dir: Path
    checks: list[Check]
    comparison: dict[str, Any]
    runtime: dict[str, Any]

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]


def drop_envelope_only(segments: list[Segment], settings: Settings) -> tuple[list[Segment], dict[str, int]]:
    """Both sources select by bounding-box overlap. Remove segments whose envelope overlaps the region
    but whose line does not, so later "intersects region" checks only flag genuinely bad coordinates.

    Segments with non-finite or far-away vertices are kept so that the validation stage reports them.
    """
    tol = settings.thresholds.bbox_tolerance_deg
    x0, y0, x1, y1 = settings.bbox
    region = shapely.box(x0, y0, x1, y1)
    kept, dropped = [], {"osm": 0, "overture": 0}
    for seg in segments:
        plausible = len(seg.coords) >= 2 and all(
            math.isfinite(x) and math.isfinite(y) and x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol
            for x, y in seg.coords
        )
        if plausible and not shapely.linestrings(seg.coords).intersects(region):
            dropped[seg.source] += 1
        else:
            kept.append(seg)
    return kept, dropped


def run(settings: Settings, snapshot_dir: Path | None = None, snapshot_date: str | None = None,
        refresh: bool = False, overture_release: str | None = None, publish: bool = True) -> RunResult:
    profiler = StageProfiler()
    checks: list[Check] = []
    with profiler:
        with profiler.stage("acquire_snapshot"):
            if snapshot_dir is None:
                snapshot_dir = acquire.acquire(settings, snapshot_date, refresh, overture_release)
            manifest = acquire.load_manifest(snapshot_dir)
        if tuple(manifest["region"]["bbox"]) != tuple(settings.bbox):
            raise ValueError(f"snapshot bbox {manifest['region']['bbox']} != requested {settings.bbox}")
        report_dir = settings.reports_dir / manifest["snapshot_date"]
        report_dir.mkdir(parents=True, exist_ok=True)
        sources = manifest["sources"]

        with profiler.stage("snapshot_integrity"):
            for name, meta in sources.items():
                path = snapshot_dir / meta["file"]
                actual = acquire.sha256(path) if path.exists() else None
                checks.append(Check(f"snapshot_sha256_{name}", "snapshot", actual == meta["sha256"],
                                    actual, f"sha256 recorded in manifest ({meta['sha256'][:12]}...)"))

        with profiler.stage("read_osm_pbf"):
            osm = read_pbf(snapshot_dir / sources["osm"]["file"], settings.bbox)
        with profiler.stage("read_overture_parquet"):
            ovt = read_parquet(snapshot_dir / sources["overture"]["file"])
        segments, envelope_only = drop_envelope_only(osm.segments + ovt.segments, settings)
        intake = {
            "osm_highway_ways_bbox_overlap": osm.highway_ways_in_bbox,
            "osm_excluded_non_road_highway": dict(osm.excluded_by_class.most_common()),
            "osm_missing_node_locations": osm.missing_locations,
            "overture_rows": ovt.rows,
            "overture_excluded_subtypes": dict(ovt.excluded_by_subtype),
            "dropped_envelope_only": envelope_only,
            "segments": {s: sum(1 for x in segments if x.source == s) for s in ("osm", "overture")},
        }

        with profiler.stage("coordinate_checks"):
            coord_checks, loadable = coordinate_checks(segments, settings.bbox, settings.thresholds)
            checks += coord_checks

        conn = pg.connect(settings.pg_dsn)
        db = mongo.connect(settings)
        try:
            with profiler.stage("load_postgis"):
                pg.reset_staging(conn)
                written = pg.load(conn, loadable)
            with profiler.stage("postgis_checks"):
                checks += pg.checks(conn, settings, written)
                pg_digest = pg.ids_digest(conn)
            with profiler.stage("load_mongodb"):
                mongo_load = mongo.load(db, loadable)
            with profiler.stage("mongodb_checks"):
                checks += mongo.checks(db, settings, mongo_load, pg_digest)
            with profiler.stage("compare_sources"):
                comparison, cmp_checks = compare(conn, settings)
                checks += cmp_checks
            comparison["intake"] = intake
            storage = {"postgis_staging_bytes": pg.table_sizes(conn, pg.STAGING),
                       "mongodb_staging": mongo.storage_bytes(db, mongo.STAGING)}

            passed = all(c.passed for c in checks)
            published = False
            summary = {"checks_passed": sum(c.passed for c in checks), "checks_total": len(checks),
                       "segments": intake["segments"]}
            if passed and publish:
                with profiler.stage("publish"):
                    pg.publish(conn, manifest, summary)
                    mongo.publish(db, manifest, summary)
                published = True
        finally:
            conn.close()
            db.client.close()

    runtime = profiler.report()
    runtime["storage"] = storage
    result = RunResult(published, report_dir, checks, comparison, runtime)
    _write_reports(result, manifest)
    return result


def _write_reports(result: RunResult, manifest: dict[str, Any]) -> None:
    d = result.report_dir
    validation = {"passed": not result.failed, "published": result.published,
                  "checks": [c.as_dict() for c in result.checks]}

    def dump(name: str, payload: Any) -> None:
        (d / name).write_text(json.dumps(payload, indent=2, default=str) + "\n")

    dump("manifest.json", manifest)
    dump("validation.json", validation)
    dump("comparison.json", result.comparison)
    dump("runtime.json", result.runtime)
    (d / "report.md").write_text(report.render(manifest, validation, result.comparison, result.runtime))
