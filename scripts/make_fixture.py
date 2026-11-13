"""Cut the committed test fixture out of a real snapshot.

    uv run python scripts/make_fixture.py data/snapshots/<date> tests/fixtures/snapshot

Keeps OSM highway ways (with their nodes) and Overture segment rows whose bounding box overlaps
FIXTURE_BBOX, and writes a manifest in the same format as `roadlab.acquire`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import osmium
import pyarrow.compute as pc
import pyarrow.parquet as pq

from roadlab.acquire import MANIFEST, load_manifest, sha256

# About 470 m x 450 m around Bremen Domshof / Marktplatz.
FIXTURE_BBOX = (8.805, 53.074, 8.812, 53.078)


def _overlaps(xs: list[float], ys: list[float]) -> bool:
    x0, y0, x1, y1 = FIXTURE_BBOX
    return min(xs) < x1 and max(xs) > x0 and min(ys) < y1 and max(ys) > y0


def cut_osm(src: Path, dest: Path) -> None:
    way_ids, node_ids = set(), set()
    fp = (osmium.FileProcessor(str(src), osmium.osm.NODE | osmium.osm.WAY).with_locations()
          .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY)).with_filter(osmium.filter.KeyFilter("highway")))
    for way in fp:
        if _overlaps([n.lon for n in way.nodes], [n.lat for n in way.nodes]):
            way_ids.add(way.id)
            node_ids.update(n.ref for n in way.nodes)
    dest.unlink(missing_ok=True)
    writer = osmium.SimpleWriter(str(dest))
    try:
        for obj in osmium.FileProcessor(str(src), osmium.osm.NODE | osmium.osm.WAY):
            if obj.is_node() and obj.id in node_ids:
                writer.add_node(obj)
            elif obj.is_way() and obj.id in way_ids:
                writer.add_way(obj)
    finally:
        writer.close()


def cut_overture(src: Path, dest: Path) -> int:
    table = pq.read_table(src)
    x0, y0, x1, y1 = FIXTURE_BBOX
    b = table.column("bbox")
    mask = pc.and_(pc.and_(pc.less(pc.struct_field(b, "xmin"), x1), pc.greater(pc.struct_field(b, "xmax"), x0)),
                   pc.and_(pc.less(pc.struct_field(b, "ymin"), y1), pc.greater(pc.struct_field(b, "ymax"), y0)))
    subset = table.filter(mask)
    pq.write_table(subset, dest, compression="zstd")
    return subset.num_rows


def main(snapshot: Path, out: Path) -> None:
    manifest = load_manifest(snapshot)
    (out / "osm").mkdir(parents=True, exist_ok=True)
    (out / "overture").mkdir(parents=True, exist_ok=True)
    osm_path, ovt_path = out / "osm" / "fixture.osm", out / "overture" / "segments.parquet"
    cut_osm(snapshot / manifest["sources"]["osm"]["file"], osm_path)
    rows = cut_overture(snapshot / manifest["sources"]["overture"]["file"], ovt_path)
    for key, path in (("osm", osm_path), ("overture", ovt_path)):
        meta = manifest["sources"][key]
        meta.update(file=str(path.relative_to(out)), bytes=path.stat().st_size, sha256=sha256(path),
                    cut_from=meta["file"])
    manifest["sources"]["overture"]["rows"] = rows
    manifest["region"] = {"name": "fixture-bremen-marktplatz", "bbox": list(FIXTURE_BBOX), "crs": "EPSG:4326"}
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
