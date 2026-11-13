"""Download a dated snapshot of both sources into data/snapshots/<YYYY-MM-DD>/ with a manifest."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import osmium
import pyarrow.parquet as pq
import requests

from .config import GEOFABRIK_URL, Settings

MANIFEST = "manifest.json"
USER_AGENT = "roadmap-integration-lab/0.1 (+https://github.com/MaheshBhushan/roadmap-integration-lab)"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_osm(dest_dir: Path, url: str) -> dict[str, Any]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60, headers={"User-Agent": USER_AGENT}) as resp:
        resp.raise_for_status()
        # Geofabrik redirects *-latest.osm.pbf to the dated file, e.g. bremen-261110.osm.pbf.
        name = resp.url.rsplit("/", 1)[-1]
        target = dest_dir / name
        tmp = target.with_suffix(".part")
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(1 << 20):
                fh.write(chunk)
        tmp.replace(target)
    header = osmium.io.Reader(str(target), osmium.osm.NOTHING).header()
    return {
        "provider": "Geofabrik",
        "license": "ODbL-1.0",
        "requested_url": url,
        "resolved_url": resp.url,
        "file": str(target.relative_to(dest_dir.parent)),
        "bytes": target.stat().st_size,
        "sha256": sha256(target),
        "replication_timestamp": header.get("osmosis_replication_timestamp"),
    }


def _download_overture(dest_dir: Path, bbox: tuple[float, float, float, float], release: str | None) -> dict:
    from overturemaps import core

    dest_dir.mkdir(parents=True, exist_ok=True)
    release = release or core.get_latest_release()
    reader = core.record_batch_reader("segment", bbox=bbox, release=release)
    table = reader.read_all()
    target = dest_dir / "segments.parquet"
    pq.write_table(table, target)
    return {
        "provider": "Overture Maps Foundation",
        "license": "ODbL-1.0 (transportation theme)",
        "release": release,
        "theme": "transportation",
        "type": "segment",
        "file": str(target.relative_to(dest_dir.parent)),
        "rows": table.num_rows,
        "bytes": target.stat().st_size,
        "sha256": sha256(target),
    }


def acquire(settings: Settings, snapshot_date: str | None = None, refresh: bool = False,
            overture_release: str | None = None) -> Path:
    """Fetch both sources for settings.bbox. Re-uses an existing complete snapshot unless refresh=True."""
    snapshot_date = snapshot_date or dt.date.today().isoformat()
    snap = settings.data_dir / "snapshots" / snapshot_date
    if (snap / MANIFEST).exists() and not refresh:
        return snap
    snap.mkdir(parents=True, exist_ok=True)
    manifest = {
        "snapshot_date": snapshot_date,
        "acquired_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "region": {"name": settings.region, "bbox": list(settings.bbox), "crs": "EPSG:4326"},
        "sources": {
            "osm": _download_osm(snap / "osm", GEOFABRIK_URL),
            "overture": _download_overture(snap / "overture", settings.bbox, overture_release),
        },
    }
    (snap / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    return snap


def load_manifest(snapshot_dir: Path) -> dict[str, Any]:
    return json.loads((snapshot_dir / MANIFEST).read_text())
