"""Coverage, restriction and schema comparison computed in PostGIS on the staged snapshot."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import psycopg

from .config import Settings
from .pg import STAGING
from .validate import Check

# Classes where car speed limits / one-way / access are meaningful.
DRIVABLE = ("motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
            "living_street", "service")
ATTRIBUTES = ("road_class", "maxspeed_kmh", "oneway", "motor_access")


def _prepare_metric(conn: psycopg.Connection, settings: Settings) -> None:
    """Valid segments clipped to the region and projected to the metric CRS, with a spatial index."""
    conn.execute("DROP TABLE IF EXISTS seg_m")
    conn.execute(
        f"CREATE TEMP TABLE seg_m AS SELECT source, source_id, road_class, maxspeed_kmh, oneway, motor_access, "
        f"ST_Intersection(ST_Transform(geom, %(srid)s), ST_Transform(ST_MakeEnvelope(%(x0)s, %(y0)s, %(x1)s, "
        f"%(y1)s, 4326), %(srid)s)) AS g FROM {STAGING}.road_segments "
        f"WHERE ST_SRID(geom) = 4326 AND ST_IsValid(geom) AND GeometryType(geom) = 'LINESTRING'",
        {"srid": settings.metric_srid, "x0": settings.bbox[0], "y0": settings.bbox[1],
         "x1": settings.bbox[2], "y1": settings.bbox[3]},
    )
    conn.execute("DELETE FROM seg_m WHERE ST_IsEmpty(g) OR ST_Length(g) = 0")
    conn.execute("CREATE INDEX ON seg_m USING gist (g)")
    conn.execute("ANALYZE seg_m")


def coverage(conn: psycopg.Connection, settings: Settings) -> dict[str, Any]:
    """Per source and class: clipped length, and the part lying within tolerance of the other source."""
    tol = settings.thresholds.match_tolerance_m
    rows = conn.execute(
        """
        SELECT s.source, s.road_class, count(*), sum(ST_Length(s.g)),
               sum(coalesce(ST_Length(ST_Intersection(s.g, cov.b)), 0))
        FROM seg_m s
        LEFT JOIN LATERAL (
            SELECT ST_Union(ST_Buffer(o.g, %(tol)s, 'quad_segs=4')) AS b FROM seg_m o
            WHERE o.source <> s.source AND ST_DWithin(o.g, s.g, %(tol)s)
        ) cov ON true
        GROUP BY 1, 2 ORDER BY 1, 4 DESC
        """,
        {"tol": tol},
    ).fetchall()
    by_source: dict[str, dict[str, Any]] = defaultdict(lambda: {"segments": 0, "length_km": 0.0,
                                                                 "covered_km": 0.0, "classes": {}})
    for source, cls, n, length, covered in rows:
        entry = by_source[source]
        entry["segments"] += n
        entry["length_km"] += length / 1000
        entry["covered_km"] += covered / 1000
        entry["classes"][cls] = {"segments": n, "length_km": round(length / 1000, 3),
                                 "covered_ratio": round(covered / length, 4) if length else None}
    for entry in by_source.values():
        entry["covered_ratio"] = round(entry["covered_km"] / entry["length_km"], 4) if entry["length_km"] else 0
        entry["length_km"] = round(entry["length_km"], 3)
        entry["covered_km"] = round(entry["covered_km"], 3)
    return {"tolerance_m": tol, "sources": dict(by_source)}


def attribute_fill(conn: psycopg.Connection) -> dict[str, Any]:
    """Length share of drivable roads that carry each restriction attribute, per source."""
    rows = conn.execute(
        """
        SELECT source, sum(ST_Length(g)),
               sum(ST_Length(g)) FILTER (WHERE maxspeed_kmh IS NOT NULL),
               sum(ST_Length(g)) FILTER (WHERE oneway IN ('forward', 'backward')),
               sum(ST_Length(g)) FILTER (WHERE motor_access IS NOT NULL)
        FROM seg_m WHERE road_class = ANY(%s) GROUP BY source ORDER BY source
        """,
        (list(DRIVABLE),),
    ).fetchall()
    return {
        source: {"drivable_km": round(total / 1000, 3),
                 "maxspeed_share": round((speed or 0) / total, 4),
                 "oneway_share": round((oneway or 0) / total, 4),
                 "motor_access_share": round((access or 0) / total, 4)}
        for source, total, speed, oneway, access in rows
    }


def linkage(conn: psycopg.Connection) -> dict[str, Any]:
    """Overture records the OSM way and version each segment came from; join on that."""
    row = conn.execute(
        f"""
        WITH ovt AS (SELECT * FROM {STAGING}.road_segments WHERE source = 'overture'),
             osm AS (SELECT * FROM {STAGING}.road_segments WHERE source = 'osm')
        SELECT
          (SELECT count(*) FROM ovt),
          (SELECT count(*) FROM ovt WHERE osm_way_id IS NULL),
          (SELECT count(DISTINCT osm_way_id) FROM ovt),
          (SELECT count(*) FROM osm),
          (SELECT count(*) FROM osm WHERE osm_way_id IN (SELECT osm_way_id FROM ovt)),
          (SELECT count(DISTINCT ovt.osm_way_id) FROM ovt JOIN osm USING (osm_way_id)),
          (SELECT count(*) FROM ovt JOIN osm USING (osm_way_id) WHERE osm.osm_version = ovt.osm_version),
          (SELECT count(*) FROM ovt JOIN osm USING (osm_way_id) WHERE osm.osm_version > ovt.osm_version),
          (SELECT count(*) FROM ovt JOIN osm USING (osm_way_id) WHERE osm.osm_version < ovt.osm_version)
        """
    ).fetchone()
    keys = ("overture_segments", "overture_without_osm_ref", "overture_distinct_osm_ways", "osm_ways",
            "osm_ways_referenced_by_overture", "referenced_ways_present_in_osm", "pairs_same_version",
            "pairs_osm_newer", "pairs_overture_newer")
    return dict(zip(keys, row, strict=True))


def restrictions(conn: psycopg.Connection, examples: int = 8) -> dict[str, Any]:
    """Compare normalised attributes for (Overture segment, source OSM way) pairs.

    Pairs where either side has partial (linearly referenced) rules are excluded from the attribute
    comparison and counted separately.
    """
    pairs = conn.execute(
        f"""
        SELECT v.source_id, v.osm_way_id, v.osm_version = o.osm_version,
               o.road_class, v.road_class, o.maxspeed_kmh, v.maxspeed_kmh, o.oneway, v.oneway,
               o.motor_access, v.motor_access, o.partial_rules OR v.partial_rules,
               w.tags ->> 'maxspeed', w.tags ->> 'oneway',
               coalesce(w.tags ->> 'motorcar', w.tags ->> 'motor_vehicle', w.tags ->> 'vehicle',
                        w.tags ->> 'access')
        FROM {STAGING}.road_segments v
        JOIN {STAGING}.road_segments o ON o.source = 'osm' AND o.osm_way_id = v.osm_way_id
        JOIN {STAGING}.osm_ways w ON w.way_id = v.osm_way_id
        WHERE v.source = 'overture'
        ORDER BY v.source_id
        """
    ).fetchall()
    result: dict[str, Any] = {"pairs": len(pairs), "pairs_with_partial_rules": 0, "attributes": {}}
    tallies = {a: {True: Counter(), False: Counter()} for a in ATTRIBUTES}
    mismatch_examples: dict[str, list[dict[str, Any]]] = {a: [] for a in ATTRIBUTES}
    for (ovt_id, way_id, same_version, *values, partial, tag_speed, tag_oneway, tag_access) in pairs:
        if partial:
            result["pairs_with_partial_rules"] += 1
            continue
        raw_tag = {"road_class": None, "maxspeed_kmh": tag_speed, "oneway": tag_oneway,
                   "motor_access": tag_access}
        for i, attr in enumerate(ATTRIBUTES):
            osm_v, ovt_v = values[2 * i], values[2 * i + 1]
            if osm_v is None and ovt_v is None:
                outcome = "neither"
            elif osm_v is None:
                outcome = "overture_only"
            elif ovt_v is None:
                outcome = "osm_only"
            else:
                outcome = "agree" if osm_v == ovt_v else "disagree"
            tallies[attr][bool(same_version)][outcome] += 1
            if outcome not in ("agree", "neither") and len(mismatch_examples[attr]) < examples:
                mismatch_examples[attr].append({
                    "overture_id": ovt_id, "osm_way_id": way_id, "same_version": bool(same_version),
                    "osm": osm_v, "overture": ovt_v, "osm_tag": raw_tag[attr],
                })
    for attr in ATTRIBUTES:
        same, newer = tallies[attr][True], tallies[attr][False]
        total = same + newer
        compared = total["agree"] + total["disagree"]
        result["attributes"][attr] = {
            "all": dict(total),
            "same_osm_version": dict(same),
            "different_osm_version": dict(newer),
            "agreement_when_both_present": round(total["agree"] / compared, 4) if compared else None,
            "examples": mismatch_examples[attr],
        }
    return result


def column_profile(conn: psycopg.Connection) -> dict[str, Any]:
    """Source schema documentation from the loaded tables: column types and non-null share."""
    profile: dict[str, Any] = {}
    for table in ("osm_ways", "overture_segments"):
        cols = conn.execute(
            "SELECT column_name, CASE WHEN data_type = 'USER-DEFINED' THEN udt_name ELSE data_type END "
            "FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
            "ORDER BY ordinal_position", (STAGING, table),
        ).fetchall()
        total = conn.execute(f"SELECT count(*) FROM {STAGING}.{table}").fetchone()[0]
        entries = []
        for name, dtype in cols:
            col = f'"{name}"'
            if dtype == "jsonb":
                filled_sql = (f"count(*) FILTER (WHERE {col} IS NOT NULL AND {col} <> 'null'::jsonb "
                              f"AND {col} <> '[]'::jsonb AND {col} <> '{{}}'::jsonb)")
            else:
                filled_sql = f"count({col})"
            filled = conn.execute(f"SELECT {filled_sql} FROM {STAGING}.{table}").fetchone()[0]
            entries.append({"column": name, "type": dtype,
                            "filled_share": round(filled / total, 4) if total else None})
        profile[table] = {"rows": total, "columns": entries}
    keys = conn.execute(
        f"SELECT k, count(*) FROM {STAGING}.osm_ways, jsonb_object_keys(tags) k "
        "WHERE k IN ('highway', 'name', 'maxspeed', 'maxspeed:conditional', 'oneway', 'oneway:bicycle', "
        "'access', 'vehicle', 'motor_vehicle', 'motorcar', 'surface', 'lanes', 'lit', 'sidewalk', "
        "'cycleway', 'junction', 'bridge', 'tunnel', 'layer', 'width') GROUP BY k ORDER BY 2 DESC"
    ).fetchall()
    total = profile["osm_ways"]["rows"]
    profile["osm_tag_fill"] = {k: round(n / total, 4) for k, n in keys} if total else {}
    profile["osm_distinct_tag_keys"] = conn.execute(
        f"SELECT count(DISTINCT k) FROM {STAGING}.osm_ways, jsonb_object_keys(tags) k"
    ).fetchone()[0]
    return profile


def compare(conn: psycopg.Connection, settings: Settings) -> tuple[dict[str, Any], list[Check]]:
    _prepare_metric(conn, settings)
    result = {
        "coverage": coverage(conn, settings),
        "attribute_fill": attribute_fill(conn),
        "linkage": linkage(conn),
        "restrictions": restrictions(conn),
        "schema_profile": column_profile(conn),
    }
    minimum = settings.thresholds.min_coverage_ratio
    checks = []
    for source in ("osm", "overture"):
        ratio = result["coverage"]["sources"].get(source, {}).get("covered_ratio", 0)
        checks.append(Check(f"coverage_{source}", "comparison", ratio >= minimum, ratio,
                            f">= {minimum} of {source} length within "
                            f"{settings.thresholds.match_tolerance_m} m of the other source"))
    return result, checks
