"""End-to-end tests on the committed fixture against real PostGIS and MongoDB services.

Uses separate databases (roadlab_test) so a local published snapshot is not overwritten.
"""

import json
import os
import shutil
from pathlib import Path

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from pymongo import MongoClient

from roadlab.acquire import load_manifest, sha256
from roadlab.cli import main
from roadlab.config import Settings
from roadlab.pipeline import run

FIXTURE = Path(__file__).parent / "fixtures" / "snapshot"
ADMIN_DSN = os.environ.get("ROADLAB_PG_DSN", "postgresql://roadlab:roadlab@127.0.0.1:55433/roadlab")
TEST_DB = "roadlab_test"
MONGO_URI = os.environ.get("ROADLAB_MONGO_URI", "mongodb://127.0.0.1:57018")

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def settings(tmp_path_factory):
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {TEST_DB}")
    MongoClient(MONGO_URI).drop_database(TEST_DB)
    manifest = load_manifest(FIXTURE)
    base = tmp_path_factory.mktemp("run")
    return Settings(region=manifest["region"]["name"], bbox=tuple(manifest["region"]["bbox"]),
                    pg_dsn=ADMIN_DSN.rsplit("/", 1)[0] + "/" + TEST_DB, mongo_uri=MONGO_URI, mongo_db=TEST_DB,
                    data_dir=base / "data", reports_dir=base / "reports")


@pytest.fixture(scope="module")
def first_run(settings):
    return run(settings, snapshot_dir=FIXTURE)


def _published_state(settings):
    with psycopg.connect(settings.pg_dsn) as conn:
        pubs = conn.execute("SELECT count(*) FROM roadlab_meta.publications").fetchone()[0]
        rows = conn.execute("SELECT count(*) FROM published.road_segments").fetchone()[0]
    db = MongoClient(settings.mongo_uri)[settings.mongo_db]
    return pubs, rows, db.road_segments.count_documents({}), db.publications.count_documents({})


def _corrupt_copy(tmp_path: Path, mutate) -> Path:
    snap = tmp_path / "snapshot"
    shutil.copytree(FIXTURE, snap)
    mutate(snap)
    manifest = load_manifest(snap)
    for meta in manifest["sources"].values():
        meta["sha256"] = sha256(snap / meta["file"])
    (snap / "manifest.json").write_text(json.dumps(manifest))
    return snap


def test_fixture_run_passes_and_publishes(first_run, settings):
    assert first_run.failed == []
    assert first_run.published
    pubs, pg_rows, mongo_docs, mongo_pubs = _published_state(settings)
    assert pubs == 1 and mongo_pubs == 1
    assert pg_rows == mongo_docs == sum(first_run.comparison["intake"]["segments"].values()) > 0
    with psycopg.connect(settings.pg_dsn) as conn:
        typ = conn.execute("SELECT type, srid FROM geometry_columns WHERE f_table_schema = 'published' "
                           "AND f_table_name = 'road_segments'").fetchone()
        assert typ == ("LINESTRING", 4326)
        assert conn.execute("SELECT count(*) FROM pg_namespace WHERE nspname = 'staging'").fetchone()[0] == 0


def test_fixture_comparison_contents(first_run):
    cmp = first_run.comparison
    cov = cmp["coverage"]["sources"]
    assert set(cov) == {"osm", "overture"}
    assert cov["osm"]["covered_ratio"] >= 0.75 and cov["overture"]["covered_ratio"] >= 0.75
    link = cmp["linkage"]
    assert link["overture_without_osm_ref"] < link["overture_segments"]
    assert link["referenced_ways_present_in_osm"] > 0
    attrs = cmp["restrictions"]["attributes"]
    assert set(attrs) == {"road_class", "maxspeed_kmh", "oneway", "motor_access"}
    assert attrs["oneway"]["all"].get("agree", 0) > 0


def test_reports_written(first_run):
    d = first_run.report_dir
    for name in ("manifest.json", "validation.json", "comparison.json", "runtime.json", "report.md"):
        assert (d / name).is_file()
    runtime = json.loads((d / "runtime.json").read_text())
    stages = {s["stage"] for s in runtime["stages"]}
    assert {"load_postgis", "load_mongodb", "compare_sources", "publish"} <= stages
    assert runtime["peak_rss_mib"] > 0 and runtime["total_seconds"] > 0
    assert "Validation passed; published." in (d / "report.md").read_text()


def test_swapped_axes_block_publication(first_run, settings, tmp_path):
    before = _published_state(settings)

    def swap_first_overture_segment(snap: Path) -> None:
        path = snap / "overture" / "segments.parquet"
        table = pq.read_table(path)
        geoms = shapely.from_wkb(table.column("geometry").to_pylist())
        i = next(i for i, s in enumerate(table.column("subtype").to_pylist()) if s == "road")
        coords = shapely.get_coordinates(geoms[i])[:, ::-1]
        geoms[i] = shapely.linestrings(coords)
        idx = table.schema.get_field_index("geometry")
        table = table.set_column(idx, table.schema.field(idx), pa.array(shapely.to_wkb(geoms).tolist(),
                                                                        type=pa.binary()))
        pq.write_table(table, path)

    result = run(settings, snapshot_dir=_corrupt_copy(tmp_path, swap_first_overture_segment))
    failed = {c.name for c in result.failed}
    assert {"coordinates_in_region", "pg_within_region", "pg_intersects_region",
            "mongo_geo_intersects_region"} <= failed
    assert not result.published
    assert _published_state(settings) == before
    assert "Validation FAILED; not published." in (result.report_dir / "report.md").read_text()


def test_tampered_snapshot_fails_integrity_and_cli_exit_code(first_run, settings, tmp_path):
    before = _published_state(settings)
    snap = tmp_path / "snapshot"
    shutil.copytree(FIXTURE, snap)
    with (snap / "osm" / "fixture.osm").open("a") as fh:
        fh.write("\n")
    argv = ["run", "--snapshot-dir", str(snap), "--bbox", *map(str, settings.bbox),
            "--reports-dir", str(tmp_path / "reports")]
    env = {"ROADLAB_PG_DSN": settings.pg_dsn, "ROADLAB_MONGO_URI": settings.mongo_uri,
           "ROADLAB_MONGO_DB": settings.mongo_db}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        assert main(argv) == 2
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    validation = json.loads(next((tmp_path / "reports").glob("*/validation.json")).read_text())
    failed = {c["name"] for c in validation["checks"] if not c["passed"]}
    assert failed == {"snapshot_sha256_osm"}
    assert _published_state(settings) == before
