"""PostGIS staging load, geometry/CRS checks and atomic publication."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

import psycopg
import shapely

from .config import Settings
from .model import Segment
from .validate import Check

STAGING = "staging"
PUBLISHED = "published"
META = "roadlab_meta"
TABLES = ("osm_ways", "overture_segments", "road_segments")


def connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=True)


def _ewkb_hex(segments: list[Segment]) -> list[str]:
    geoms = [
        shapely.linestrings(s.coords) if len(s.coords) >= 2 else shapely.points(s.coords[0])
        for s in segments
    ]
    geoms = shapely.set_srid(geoms, 4326)
    return list(shapely.to_wkb(geoms, hex=True, include_srid=True))


def _json(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


def reset_staging(conn: psycopg.Connection) -> None:
    ddl = resources.files("roadlab").joinpath("sql/schema.sql").read_text()
    with conn.transaction():
        conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        conn.execute(f"DROP SCHEMA IF EXISTS {STAGING} CASCADE")
        conn.execute(f"CREATE SCHEMA {STAGING}")
        conn.execute(f"SET LOCAL search_path TO {STAGING}, public")
        conn.execute(ddl)


def load(conn: psycopg.Connection, segments: list[Segment]) -> dict[str, int]:
    osm = [s for s in segments if s.source == "osm"]
    ovt = [s for s in segments if s.source == "overture"]
    with conn.transaction(), conn.cursor() as cur:
        with cur.copy(
            f"COPY {STAGING}.osm_ways (way_id, version, osm_timestamp, highway, tags, geom) FROM STDIN"
        ) as copy:
            for seg, geom in zip(osm, _ewkb_hex(osm), strict=True):
                raw = seg.raw
                copy.write_row((seg.osm_way_id, raw["version"], raw["timestamp"], raw["tags"]["highway"],
                                _json(raw["tags"]), geom))
        cols = (
            "id", "version", "subtype", "class", "subclass", "names", "connectors", "road_surface",
            "road_flags", "width_rules", "level_rules", "access_restrictions", "speed_limits",
            "prohibited_transitions", "destinations", "routes", "sources",
        )
        scalar = {"id", "version", "subtype", "class", "subclass"}
        with cur.copy(f"COPY {STAGING}.overture_segments ({', '.join(cols)}, geom) FROM STDIN") as copy:
            for seg, geom in zip(ovt, _ewkb_hex(ovt), strict=True):
                row = [seg.raw.get(c) if c in scalar else
                       (None if seg.raw.get(c) is None else _json(seg.raw.get(c))) for c in cols]
                copy.write_row((*row, geom))
        with cur.copy(
            f"COPY {STAGING}.road_segments (source, source_id, osm_way_id, osm_version, road_class, name, "
            "maxspeed_kmh, oneway, motor_access, partial_rules, geom) FROM STDIN"
        ) as copy:
            for seg, geom in zip(segments, _ewkb_hex(segments), strict=True):
                copy.write_row((seg.source, seg.source_id, seg.osm_way_id, seg.osm_version, seg.road_class,
                                seg.name, seg.maxspeed_kmh, seg.oneway, seg.motor_access, seg.partial_rules,
                                geom))
        cur.execute(f"ANALYZE {STAGING}.road_segments")
    return {"osm_ways": len(osm), "overture_segments": len(ovt), "road_segments": len(segments)}


def _count_check(conn, name: str, sql: str, expected: str, params: tuple = ()) -> Check:
    """sql must return (count bigint, examples text[])."""
    try:
        count, examples = conn.execute(sql, params).fetchone()
    except psycopg.Error as exc:
        return Check(name, "postgis", False, "error", expected, [str(exc).strip()])
    return Check(name, "postgis", count == 0, count, expected, list(examples or []))


def checks(conn: psycopg.Connection, settings: Settings, expected_counts: dict[str, int]) -> list[Check]:
    xmin, ymin, xmax, ymax = settings.bbox
    tol = settings.thresholds.bbox_tolerance_deg
    rs = f"{STAGING}.road_segments"
    ex = "(array_agg(source || ':' || source_id ORDER BY source, source_id))[1:5]"
    out = [
        _count_check(conn, "pg_srid_4326", f"SELECT count(*), {ex} FROM {rs} WHERE ST_SRID(geom) <> 4326",
                     "0 geometries with SRID other than 4326"),
        _count_check(conn, "pg_geometry_type",
                     f"SELECT count(*), {ex} FROM {rs} WHERE GeometryType(geom) <> 'LINESTRING'",
                     "0 non-LineString geometries"),
        _count_check(conn, "pg_geometry_valid",
                     f"SELECT count(*), (array_agg(source || ':' || source_id || ' ' || ST_IsValidReason(geom)"
                     f" ORDER BY source, source_id))[1:5] FROM {rs} WHERE NOT ST_IsValid(geom)",
                     "0 geometries failing ST_IsValid"),
        _count_check(conn, "pg_non_degenerate",
                     f"SELECT count(*), {ex} FROM {rs} WHERE ST_IsEmpty(geom) OR ST_NPoints(geom) < 2 "
                     "OR ST_Length(geom::geography) = 0",
                     "0 empty or zero-length geometries"),
        _count_check(conn, "pg_within_region",
                     f"SELECT count(*), {ex} FROM {rs} WHERE NOT ST_CoveredBy(geom, "
                     "ST_Expand(ST_MakeEnvelope(%s, %s, %s, %s, 4326), %s))",
                     f"0 geometries outside bbox +/- {tol} deg", (xmin, ymin, xmax, ymax, tol)),
        _count_check(conn, "pg_intersects_region",
                     f"SELECT count(*), {ex} FROM {rs} WHERE NOT ST_Intersects(geom, "
                     "ST_MakeEnvelope(%s, %s, %s, %s, 4326))",
                     "0 geometries not touching the region bbox", (xmin, ymin, xmax, ymax)),
    ]
    # CRS round trip: project to the metric CRS used for lengths and back; drift must be negligible.
    limit = settings.thresholds.max_roundtrip_error_m
    try:
        drift = conn.execute(
            f"SELECT coalesce(max(ST_HausdorffDistance(geom, ST_Transform(ST_Transform("
            f"geom, %s), 4326))), 0) FROM {rs} WHERE ST_SRID(geom) = 4326",
            (settings.metric_srid,),
        ).fetchone()[0]
        # Hausdorff in degrees -> metres (upper bound using 111.32 km per degree).
        drift_m = float(drift) * 111_320
        out.append(Check("pg_crs_roundtrip", "postgis", drift_m <= limit, round(drift_m, 6),
                         f"<= {limit} m drift after EPSG:4326 -> EPSG:{settings.metric_srid} -> EPSG:4326"))
    except psycopg.Error as exc:
        out.append(Check("pg_crs_roundtrip", "postgis", False, "error", "transform succeeds", [str(exc)]))
    counts = dict(conn.execute(
        f"SELECT 'osm_ways', count(*) FROM {STAGING}.osm_ways UNION ALL "
        f"SELECT 'overture_segments', count(*) FROM {STAGING}.overture_segments UNION ALL "
        f"SELECT 'road_segments', count(*) FROM {rs}"
    ).fetchall())
    out.append(Check("pg_row_counts", "postgis", counts == expected_counts, counts,
                     f"equal to rows written {expected_counts}"))
    return out


def ids_digest(conn: psycopg.Connection, schema: str = STAGING) -> tuple[int, str]:
    return conn.execute(
        f"SELECT count(*), md5(coalesce(string_agg(source || ':' || source_id, ',' "
        f"ORDER BY source || ':' || source_id COLLATE \"C\"), '')) FROM {schema}.road_segments"
    ).fetchone()


def table_sizes(conn: psycopg.Connection, schema: str) -> dict[str, int]:
    return dict(conn.execute(
        "SELECT c.relname, pg_total_relation_size(c.oid) FROM pg_class c JOIN pg_namespace n "
        "ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind = 'r' ORDER BY 1", (schema,)
    ).fetchall())


def publish(conn: psycopg.Connection, manifest: dict[str, Any], summary: dict[str, Any]) -> None:
    """Tighten geometry typmods and swap staging -> published in one transaction."""
    with conn.transaction():
        for table in TABLES:
            conn.execute(f"ALTER TABLE {STAGING}.{table} ALTER COLUMN geom TYPE geometry(LineString, 4326)")
        conn.execute(f"DROP SCHEMA IF EXISTS {PUBLISHED} CASCADE")
        conn.execute(f"ALTER SCHEMA {STAGING} RENAME TO {PUBLISHED}")
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {META}")
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {META}.publications (id bigserial PRIMARY KEY, "
            "published_at timestamptz NOT NULL DEFAULT now(), snapshot_date date NOT NULL, "
            "manifest jsonb NOT NULL, summary jsonb NOT NULL)"
        )
        conn.execute(
            f"INSERT INTO {META}.publications (snapshot_date, manifest, summary) VALUES (%s, %s, %s)",
            (manifest["snapshot_date"], _json(manifest), _json(summary)),
        )
