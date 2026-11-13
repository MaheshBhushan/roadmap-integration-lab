"""Runtime settings. Everything can be overridden with environment variables or CLI flags."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Bremen Mitte (Altstadt, Bahnhofsvorstadt, Ostertor), roughly 3.3 km x 2.8 km.
DEFAULT_BBOX = (8.78, 53.065, 8.83, 53.09)
DEFAULT_REGION = "bremen-mitte"
GEOFABRIK_URL = "https://download.geofabrik.de/europe/germany/bremen-latest.osm.pbf"


@dataclass(frozen=True)
class Thresholds:
    """Publication gate. A snapshot is published only if every check passes."""

    min_segments_per_source: int = 10
    # Lon/lat values outside the region bbox grown by this many degrees count as coordinate errors
    # (catches swapped axes or projected coordinates mislabelled as EPSG:4326).
    bbox_tolerance_deg: float = 0.05
    # Max allowed drift in metres after a 4326 -> 25832 -> 4326 round trip.
    max_roundtrip_error_m: float = 0.01
    # Share of each source's road length that must lie within match_tolerance_m of the other source.
    min_coverage_ratio: float = 0.75
    match_tolerance_m: float = 10.0


@dataclass(frozen=True)
class Settings:
    region: str = DEFAULT_REGION
    bbox: tuple[float, float, float, float] = DEFAULT_BBOX
    pg_dsn: str = field(
        default_factory=lambda: os.environ.get(
            "ROADLAB_PG_DSN", "postgresql://roadlab:roadlab@127.0.0.1:55433/roadlab"
        )
    )
    mongo_uri: str = field(
        default_factory=lambda: os.environ.get("ROADLAB_MONGO_URI", "mongodb://127.0.0.1:57018")
    )
    mongo_db: str = field(default_factory=lambda: os.environ.get("ROADLAB_MONGO_DB", "roadlab"))
    data_dir: Path = Path("data")
    reports_dir: Path = Path("reports")
    # Metric CRS used for lengths and buffers (ETRS89 / UTM zone 32N covers Bremen).
    metric_srid: int = 25832
    thresholds: Thresholds = Thresholds()
