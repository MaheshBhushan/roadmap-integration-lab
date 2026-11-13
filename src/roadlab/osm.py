"""Read highway ways from an OSM PBF extract and normalise their restriction tags."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import osmium

from .model import ROAD_CLASSES, Segment

MPH_TO_KMH = 1.609344

# Implicit German speed limits (https://wiki.openstreetmap.org/wiki/Key:maxspeed#Implicit_maxspeed_values).
_IMPLICIT_MAXSPEED = {
    "de:urban": 50.0,
    "de:rural": 100.0,
    "de:zone30": 30.0,
    "de:zone:30": 30.0,
    "de:zone20": 20.0,
    "de:zone:20": 20.0,
    "de:zone10": 10.0,
    "de:zone:10": 10.0,
    "de:bicycle_road": 30.0,
}
_NUMERIC_SPEED = re.compile(r"^(\d+(?:\.\d+)?)\s*(mph|km/h|kmh|kph)?$")

_ACCESS_MAP = {
    "yes": "allowed",
    "permissive": "allowed",
    "designated": "allowed",
    "no": "denied",
    "destination": "destination",
    "customers": "customers",
    "delivery": "delivery",
    "private": "private",
}
# Most specific key first: the first one present decides car access.
_ACCESS_KEYS = ("motorcar", "motor_vehicle", "vehicle", "access")


def parse_maxspeed(value: str | None) -> float | None:
    """Return a km/h number for maxspeed tag values, None for absent/unlimited/symbolic values."""
    if not value:
        return None
    v = value.strip().lower()
    match = _NUMERIC_SPEED.match(v)
    if match:
        speed = float(match.group(1))
        if match.group(2) == "mph":
            speed = round(speed * MPH_TO_KMH, 1)
        return speed
    return _IMPLICIT_MAXSPEED.get(v)


def parse_oneway(tags: dict[str, str]) -> str | None:
    value = tags.get("oneway")
    if value is None:
        if tags.get("junction") in ("roundabout", "circular") or tags.get("highway") == "motorway":
            return "forward"
        return "both"
    v = value.strip().lower()
    if v in ("yes", "true", "1"):
        return "forward"
    if v in ("-1", "reverse"):
        return "backward"
    if v in ("no", "false", "0"):
        return "both"
    return None  # reversible, alternating, conditional values


def parse_motor_access(tags: dict[str, str]) -> str | None:
    for key in _ACCESS_KEYS:
        value = tags.get(key)
        if value is not None:
            return _ACCESS_MAP.get(value.strip().lower(), "other")
    return None


def road_class(highway: str | None) -> str | None:
    if not highway:
        return None
    h = highway.removesuffix("_link")
    if h == "road":
        return "unknown"
    return h if h in ROAD_CLASSES else None


def way_to_segment(way_id: int, version: int, timestamp: str, tags: dict[str, str],
                   coords: list[tuple[float, float]]) -> Segment | None:
    cls = road_class(tags.get("highway"))
    if cls is None:
        return None
    partial = any(k in tags for k in ("maxspeed:forward", "maxspeed:backward")) and "maxspeed" not in tags
    return Segment(
        source="osm",
        source_id=str(way_id),
        coords=coords,
        road_class=cls,
        name=tags.get("name"),
        osm_way_id=way_id,
        osm_version=version,
        maxspeed_kmh=parse_maxspeed(tags.get("maxspeed")),
        oneway=parse_oneway(tags),
        motor_access=parse_motor_access(tags),
        partial_rules=partial,
        raw={"version": version, "timestamp": timestamp, "tags": tags},
    )


def _intersects(coords: list[tuple[float, float]], bbox: tuple[float, float, float, float]) -> bool:
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    return min(xs) < bbox[2] and max(xs) > bbox[0] and min(ys) < bbox[3] and max(ys) > bbox[1]


@dataclass
class OsmReadResult:
    segments: list[Segment]
    highway_ways_in_bbox: int = 0
    excluded_by_class: Counter = field(default_factory=Counter)
    missing_locations: int = 0


def read_pbf(path: Path, bbox: tuple[float, float, float, float]) -> OsmReadResult:
    """Return highway ways whose envelope intersects bbox (same rule Overture uses for its bbox filter)."""
    result = OsmReadResult(segments=[])
    processor = (
        osmium.FileProcessor(str(path), osmium.osm.NODE | osmium.osm.WAY)
        .with_locations()
        .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
        .with_filter(osmium.filter.KeyFilter("highway"))
    )
    for way in processor:
        try:
            coords = [(n.lon, n.lat) for n in way.nodes]
        except osmium.InvalidLocationError:
            result.missing_locations += 1
            continue
        if len(coords) < 1 or not _intersects(coords, bbox):
            continue
        result.highway_ways_in_bbox += 1
        tags = {t.k: t.v for t in way.tags}
        seg = way_to_segment(way.id, way.version, way.timestamp.isoformat(), tags, coords)
        if seg is None:
            result.excluded_by_class[tags["highway"]] += 1
        else:
            result.segments.append(seg)
    return result
