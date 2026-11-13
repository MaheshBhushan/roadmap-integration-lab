"""Read Overture Maps transportation `segment` rows (GeoParquet) and normalise their restrictions."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import shapely

from .model import Segment

MPH_TO_KMH = 1.609344
_OSM_RECORD = re.compile(r"^w(\d+)@(\d+)$")
_CAR_MODES = {"motor_vehicle", "car", "vehicle"}
_USING_LABELS = {"at_destination": "destination", "as_customer": "customers", "to_deliver": "delivery"}
_RECOGNIZED_LABELS = {"as_private": "private"}

# Columns kept verbatim (as JSON) in the raw PostGIS table / Mongo document.
RAW_COLUMNS = (
    "id", "version", "subtype", "class", "subclass", "names", "connectors", "road_surface", "road_flags",
    "width_rules", "level_rules", "access_restrictions", "speed_limits", "prohibited_transitions",
    "destinations", "routes", "sources",
)


def osm_way_ref(sources: list[dict[str, Any]] | None) -> tuple[int | None, int | None]:
    """Return (way_id, way_version) from the first OpenStreetMap source record, e.g. 'w4708816@34'."""
    for src in sources or []:
        match = _OSM_RECORD.match(src.get("record_id") or "")
        if match and src.get("dataset") == "OpenStreetMap":
            return int(match.group(1)), int(match.group(2))
    return None, None


def _unconditional(when: dict[str, Any] | None) -> bool:
    return not when or not any(when.get(k) for k in ("during", "heading", "using", "recognized", "mode", "vehicle"))


def parse_speed(rules: list[dict[str, Any]] | None) -> tuple[float | None, bool]:
    """Unconditional whole-segment max speed in km/h, plus a flag for partial (linearly referenced) rules."""
    speed, partial = None, False
    for rule in rules or []:
        if rule.get("between") is not None:
            partial = True
            continue
        if not _unconditional(rule.get("when")) or not rule.get("max_speed"):
            continue
        value = float(rule["max_speed"]["value"])
        if rule["max_speed"].get("unit") == "mph":
            value = round(value * MPH_TO_KMH, 1)
        speed = value
    return speed, partial


def parse_access(rules: list[dict[str, Any]] | None) -> tuple[str | None, str | None, bool]:
    """Return (oneway, motor_access, partial) from Overture access_restrictions.

    Overture encodes OSM oneway=yes as `denied` with `when.heading = backward`, and access=destination as a
    base `denied` rule plus an `allowed` rule with `when.using = [at_destination]`.
    """
    oneway = "both"
    generic = mode_specific = conditional = None
    partial = False
    for rule in rules or []:
        if rule.get("between") is not None:
            partial = True
            continue
        when = rule.get("when") or {}
        if when.get("during") or when.get("vehicle"):
            continue  # time windows and size/weight limits are not compared
        modes = set(when.get("mode") or [])
        heading = when.get("heading")
        if heading:
            if not modes and rule["access_type"] == "denied" and not when.get("using"):
                oneway = "forward" if heading == "backward" else "backward"
            continue
        if modes and not modes & _CAR_MODES:
            continue
        label = None
        for key in when.get("using") or []:
            label = label or _USING_LABELS.get(key, "other")
        for key in when.get("recognized") or []:
            label = label or _RECOGNIZED_LABELS.get(key, "other")
        if label:
            if rule["access_type"] != "denied":
                conditional = label
            continue
        value = "denied" if rule["access_type"] == "denied" else "allowed"
        if modes:
            mode_specific = value
        else:
            generic = value
    access = mode_specific or generic
    if conditional and access in (None, "denied"):
        access = conditional
    return oneway, access, partial


def row_to_segment(row: dict[str, Any], coords: list[tuple[float, float]]) -> Segment:
    speed, speed_partial = parse_speed(row.get("speed_limits"))
    oneway, access, access_partial = parse_access(row.get("access_restrictions"))
    way_id, way_version = osm_way_ref(row.get("sources"))
    names = row.get("names") or {}
    return Segment(
        source="overture",
        source_id=row["id"],
        coords=coords,
        road_class=row.get("class") or "unknown",
        name=names.get("primary"),
        osm_way_id=way_id,
        osm_version=way_version,
        maxspeed_kmh=speed,
        oneway=oneway,
        motor_access=access,
        partial_rules=speed_partial or access_partial,
        raw={k: row.get(k) for k in RAW_COLUMNS},
    )


@dataclass
class OvertureReadResult:
    segments: list[Segment]
    rows: int = 0
    excluded_by_subtype: Counter = field(default_factory=Counter)


def read_parquet(path: Path) -> OvertureReadResult:
    table = pq.read_table(path)
    geoms = shapely.from_wkb(table.column("geometry").to_pylist())
    result = OvertureReadResult(segments=[], rows=table.num_rows)
    for row, geom in zip(table.drop_columns(["geometry"]).to_pylist(), geoms, strict=True):
        if row.get("subtype") != "road":
            result.excluded_by_subtype[row.get("subtype")] += 1
            continue
        coords = [tuple(c) for c in shapely.get_coordinates(geom).tolist()]
        result.segments.append(row_to_segment(row, coords))
    return result
