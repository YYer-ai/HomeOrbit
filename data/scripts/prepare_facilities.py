from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import osmium
from pyproj import Geod
from shapely.geometry import box, mapping, shape

from spatial_common import BAY_AREA_BBOX, PROJECT_ROOT, sha256_file, update_metadata


DEFAULT_INPUT_PATH = PROJECT_ROOT / "data" / "raw" / "california-latest.osm.pbf"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "processed" / "bay-area-facilities.ndjson"
BAY_AREA_GEOMETRY = box(*BAY_AREA_BBOX)
GEOD = Geod(ellps="WGS84")
SCRIPT_VERSION = "2.1.0"
RULES_VERSION = "2026-09-01.1"
DEDUPE_DISTANCE_METERS = 30
OSM_SOURCE_URL = "https://download.geofabrik.de/north-america/us/california-latest.osm.pbf"
FACILITY_RULES = {
    "education": {"amenity": {"school", "college", "university", "kindergarten", "library"}},
    "healthcare": {"amenity": {"hospital", "clinic", "doctors", "dentist", "pharmacy"}},
    "daily_shopping": {"amenity": {"marketplace"}, "shop": {"supermarket", "convenience", "grocery", "greengrocer", "department_store", "mall"}},
    "parks": {"leisure": {"park", "playground", "garden", "nature_reserve"}, "landuse": {"recreation_ground", "village_green"}},
    "public_transport": {"highway": {"bus_stop"}, "public_transport": {"platform", "station", "stop_position"}, "railway": {"station", "halt", "tram_stop", "subway_entrance"}, "amenity": {"bus_station"}},
}
FILTER_KEYS = tuple(sorted({key for rules in FACILITY_RULES.values() for key in rules}))


def read_osm_source_metadata(input_path: Path) -> dict[str, str | int]:
    reader = osmium.io.Reader(input_path)
    try:
        header = reader.header()
        timestamp = header.get("osmosis_replication_timestamp") or header.get("timestamp")
        sequence = header.get("osmosis_replication_sequence_number")
        base_url = header.get("osmosis_replication_base_url")
    finally:
        reader.close()
    if not timestamp:
        raise ValueError(f"OSM input has no replication timestamp: {input_path}")
    metadata: dict[str, str | int] = {"replication_timestamp": timestamp}
    if sequence:
        metadata["replication_sequence"] = int(sequence)
    if base_url:
        metadata["replication_base_url"] = base_url
    return metadata


def classify_facility(tags: Mapping[str, str]) -> str | None:
    for category, rules in FACILITY_RULES.items():
        if any(tags.get(key) in values for key, values in rules.items()):
            return category
    return None


def _osm_key(entity: object) -> str:
    if isinstance(entity, osmium.osm.Node):
        return f"n{entity.id}"
    if isinstance(entity, osmium.osm.Area):
        prefix = "w" if entity.from_way() else "r"
        return f"{prefix}{entity.orig_id()}"
    raise TypeError(f"Unsupported OSM entity: {type(entity)!r}")


def _is_duplicate_transit(record: dict, transit_by_name: dict[str, list[list[float]]]) -> bool:
    if record["category"] != "public_transport" or not record["name"]:
        return False
    name = " ".join(record["name"].casefold().split())
    point = record["analysis_point"]["coordinates"]
    for other_point in transit_by_name.get(name, []):
        _az1, _az2, distance = GEOD.inv(point[0], point[1], other_point[0], other_point[1])
        if distance <= DEDUPE_DISTANCE_METERS:
            return True
    return False


def build_facilities_product(input_path: Path, output_path: Path) -> int:
    processor = osmium.FileProcessor(input_path).with_areas().with_filter(
        osmium.filter.KeyFilter(*FILTER_KEYS)
    )
    factory = osmium.geom.GeoJSONFactory()
    accepted: list[dict] = []
    transit_by_name: dict[str, list[list[float]]] = {}
    geometry_errors = 0

    for entity in processor:
        if not isinstance(entity, (osmium.osm.Node, osmium.osm.Area)):
            continue
        tags = {tag.k: tag.v for tag in entity.tags}
        category = classify_facility(tags)
        if category is None:
            continue
        if category == "parks" and not isinstance(entity, osmium.osm.Area):
            continue
        try:
            geometry_json = factory.create_point(entity) if isinstance(entity, osmium.osm.Node) else factory.create_multipolygon(entity)
            geometry = shape(json.loads(geometry_json))
        except (RuntimeError, ValueError):
            geometry_errors += 1
            continue
        if geometry.is_empty or not geometry.intersects(BAY_AREA_GEOMETRY):
            continue
        record = {
            "osm_key": _osm_key(entity),
            "category": category,
            "name": tags.get("name", ""),
            "geometry": mapping(geometry),
            "analysis_point": mapping(geometry.representative_point()),
        }
        if _is_duplicate_transit(record, transit_by_name):
            continue
        accepted.append(record)
        if category == "public_transport" and record["name"]:
            normalized_name = " ".join(record["name"].casefold().split())
            transit_by_name.setdefault(normalized_name, []).append(record["analysis_point"]["coordinates"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as output:
        for record in accepted:
            output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    if output_path.resolve().is_relative_to(PROJECT_ROOT):
        source_metadata = read_osm_source_metadata(input_path)
        update_metadata(
            "facilities", output_path, len(accepted), geometry_errors=geometry_errors,
            input={"path": input_path.resolve().relative_to(PROJECT_ROOT).as_posix(), "source": OSM_SOURCE_URL,
                   "size_bytes": input_path.stat().st_size,
                   "sha256": sha256_file(input_path), **source_metadata},
            bbox=list(BAY_AREA_BBOX), dedupe_distance_m=DEDUPE_DISTANCE_METERS,
            rules_version=RULES_VERSION, script_version=SCRIPT_VERSION,
        )
    print(f"Skipped {geometry_errors} facilities with invalid geometry")
    return len(accepted)


def main() -> None:
    count = build_facilities_product(DEFAULT_INPUT_PATH, DEFAULT_OUTPUT_PATH)
    print(f"Wrote {count} Bay Area facilities")


if __name__ == "__main__":
    main()
