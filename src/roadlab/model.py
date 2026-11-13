"""Unified road-segment model shared by both sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Overture `segment.class` values for subtype=road. OSM highway=* values are mapped onto this set;
# anything else (platform, bus_stop, construction, proposed, ...) is not a road segment.
ROAD_CLASSES = frozenset(
    {
        "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
        "living_street", "service", "pedestrian", "footway", "steps", "path", "track", "cycleway",
        "bridleway", "unknown",
    }
)

# Values for the normalised restriction fields.
ONEWAY_VALUES = ("forward", "backward", "both")
ACCESS_VALUES = ("allowed", "denied", "destination", "customers", "delivery", "private", "other")


@dataclass
class Segment:
    source: str  # "osm" or "overture"
    source_id: str
    coords: list[tuple[float, float]]  # lon, lat (EPSG:4326 axis order x, y)
    road_class: str
    name: str | None = None
    osm_way_id: int | None = None
    osm_version: int | None = None
    maxspeed_kmh: float | None = None
    oneway: str | None = None  # forward | backward | both | None (unknown/conditional)
    motor_access: str | None = None  # see ACCESS_VALUES, None = not tagged
    partial_rules: bool = False  # some restriction only applies to part of the segment
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"
