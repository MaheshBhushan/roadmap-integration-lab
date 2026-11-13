"""Render the per-snapshot Markdown report from the JSON results."""

from __future__ import annotations

from typing import Any


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(v: Any) -> str:
        if v is None:
            return "-"
        if isinstance(v, float):
            return f"{v:.4g}" if abs(v) < 1 else f"{v:,.3f}".rstrip("0").rstrip(".")
        return str(v).replace("|", "\\|")

    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{100 * v:.1f}%"


def render(manifest: dict[str, Any], validation: dict[str, Any], comparison: dict[str, Any],
           runtime: dict[str, Any]) -> str:
    src = manifest["sources"]
    out = [f"# Snapshot {manifest['snapshot_date']} - {manifest['region']['name']}\n",
           f"Region bbox (EPSG:4326): `{manifest['region']['bbox']}`\n",
           f"- OSM: {src['osm']['provider']} `{src['osm']['file']}`, replication timestamp "
           f"{src['osm'].get('replication_timestamp')}, {src['osm']['bytes']:,} bytes",
           f"- Overture: release `{src['overture']['release']}`, {src['overture']['rows']:,} segment rows, "
           f"{src['overture']['bytes']:,} bytes\n",
           f"**Validation {'passed' if validation['passed'] else 'FAILED'}; "
           f"{'published' if validation['published'] else 'not published'}.**\n",
           "## Validation checks\n"]
    out.append(_table(["check", "stage", "result", "observed", "expected"], [
        [c["name"], c["stage"], "pass" if c["passed"] else "FAIL", c["observed"], c["expected"]]
        for c in validation["checks"]]))

    intake = comparison["intake"]
    out.append("\n## Intake\n")
    out.append(_table(["item", "value"], [[k, v] for k, v in intake.items()]))

    cov = comparison["coverage"]
    out.append(f"\n## Coverage (length within {cov['tolerance_m']} m of the other source, clipped to bbox)\n")
    out.append(_table(["source", "segments", "length km", "covered km", "covered"], [
        [s, e["segments"], e["length_km"], e["covered_km"], _pct(e["covered_ratio"])]
        for s, e in cov["sources"].items()]))
    classes = sorted({c for e in cov["sources"].values() for c in e["classes"]})
    out.append("\nPer road class:\n\n")
    rows = []
    for cls in classes:
        row: list[Any] = [cls]
        for s in ("osm", "overture"):
            e = cov["sources"].get(s, {}).get("classes", {}).get(cls)
            row += [e["length_km"], _pct(e["covered_ratio"])] if e else [None, None]
        rows.append(row)
    out.append(_table(["class", "OSM km", "OSM covered", "Overture km", "Overture covered"], rows))

    out.append("\n## Restriction attribute fill (share of drivable-road length)\n")
    out.append(_table(["source", "drivable km", "maxspeed", "oneway (directional)", "motor access tagged"], [
        [s, e["drivable_km"], _pct(e["maxspeed_share"]), _pct(e["oneway_share"]), _pct(e["motor_access_share"])]
        for s, e in comparison["attribute_fill"].items()]))

    out.append("\n## Linkage via Overture `sources[].record_id`\n")
    out.append(_table(["item", "value"], [[k, v] for k, v in comparison["linkage"].items()]))

    r = comparison["restrictions"]
    out.append(f"\n## Restriction comparison on {r['pairs']:,} (Overture segment, source OSM way) pairs\n")
    out.append(f"{r['pairs_with_partial_rules']:,} pairs with partial (`between`) rules excluded.\n\n")
    rows = []
    for attr, a in r["attributes"].items():
        t = a["all"]
        rows.append([attr, t.get("agree", 0), t.get("disagree", 0), t.get("osm_only", 0),
                     t.get("overture_only", 0), t.get("neither", 0), _pct(a["agreement_when_both_present"]),
                     a["different_osm_version"].get("disagree", 0)])
    out.append(_table(["attribute", "agree", "disagree", "OSM only", "Overture only", "neither",
                       "agreement", "disagree w/ newer OSM version"], rows))
    for attr, a in r["attributes"].items():
        if a["examples"]:
            out.append(f"\nExamples for `{attr}`:\n\n")
            out.append(_table(["overture id", "osm way", "same version", "OSM", "Overture", "raw OSM tag"], [
                [e["overture_id"], e["osm_way_id"], e["same_version"], e["osm"], e["overture"], e["osm_tag"]]
                for e in a["examples"]]))

    prof = comparison["schema_profile"]
    for table in ("osm_ways", "overture_segments"):
        out.append(f"\n## Source schema: `{table}` ({prof[table]['rows']:,} rows)\n")
        out.append(_table(["column", "type", "non-empty"], [
            [c["column"], c["type"], _pct(c["filled_share"])] for c in prof[table]["columns"]]))
    out.append(f"\nOSM tag keys on highway ways: {prof['osm_distinct_tag_keys']} distinct. Fill of selected keys:\n\n")
    out.append(_table(["tag", "ways with tag"], [[k, _pct(v)] for k, v in prof["osm_tag_fill"].items()]))

    out.append("\n## Runtime and memory (pipeline process)\n")
    out.append(f"Total {runtime['total_seconds']} s; peak RSS {runtime['peak_rss_mib']} MiB. Peaks are the "
               "kernel high-water mark VmHWM, reset before each stage "
               f"(reset {'succeeded' if runtime['per_stage_reset'] else 'unavailable; peaks are cumulative'}).\n\n")
    out.append(_table(["stage", "seconds", "RSS start MiB", "RSS end MiB", "peak RSS MiB"], [
        [s["stage"], s["seconds"], s["rss_start_mib"], s["rss_end_mib"], s["peak_rss_mib"]]
        for s in runtime["stages"]]))
    st = runtime["storage"]
    out.append("\nStaging size: PostGIS (table + indexes + TOAST) " + ", ".join(
        f"{k} {v / 2**20:.2f} MiB" for k, v in st["postgis_staging_bytes"].items())
        + f"; MongoDB uncompressed BSON {st['mongodb_staging']['data'] / 2**20:.2f} MiB.\n")
    return "\n\n".join(x.strip("\n") for x in out) + "\n"
