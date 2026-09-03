from __future__ import annotations

import json
import sys
from pathlib import Path

import osmium

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from prepare_facilities import (  # noqa: E402
    FILTER_KEYS,
    GEOD,
    _is_duplicate_transit,
    build_facilities_product,
    classify_facility,
    read_osm_source_metadata,
)


FIXTURE = Path(__file__).parent / "fixtures" / "facilities.osm"


def test_classify_facility_tags() -> None:
    assert classify_facility({"amenity": "school"}) == "education"
    assert classify_facility({"shop": "supermarket"}) == "daily_shopping"
    assert classify_facility({"leisure": "park"}) == "parks"
    assert classify_facility({"highway": "bus_stop"}) == "public_transport"
    assert classify_facility({"amenity": "marketplace"}) == "daily_shopping"
    assert classify_facility({"shop": "greengrocer"}) == "daily_shopping"
    assert classify_facility({"landuse": "recreation_ground"}) == "parks"
    assert classify_facility({"landuse": "village_green"}) == "parks"
    assert "landuse" in FILTER_KEYS


def test_extracts_five_facility_categories_and_deduplicates_transit(tmp_path: Path) -> None:
    output_path = tmp_path / "facilities.ndjson"
    count = build_facilities_product(FIXTURE, output_path)
    records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]

    assert count == 7
    assert {record["category"] for record in records} == {
        "education",
        "daily_shopping",
        "public_transport",
        "healthcare",
        "parks",
    }
    assert all({"osm_key", "name", "geometry", "analysis_point"} <= record.keys() for record in records)
    assert sum(record["category"] == "public_transport" for record in records) == 1
    assert all(record["name"] != "Point Park Must Be Excluded" for record in records)
    park = next(record for record in records if record["category"] == "parks")
    hospital = next(record for record in records if record["category"] == "healthcare")
    assert park["geometry"]["type"] in {"Polygon", "MultiPolygon"}
    assert hospital["geometry"]["type"] in {"Polygon", "MultiPolygon"}
    assert park["analysis_point"]["type"] == "Point"
    assert all(record["geometry"]["type"] == "MultiPolygon" for record in records if record["category"] == "parks")


def test_reads_replication_metadata_from_osm_header(tmp_path: Path) -> None:
    pbf_path = tmp_path / "header.osm.pbf"
    header = osmium.io.Header()
    header.set("osmosis_replication_timestamp", "2026-08-26T20:22:15Z")
    header.set("osmosis_replication_sequence_number", "4892")
    header.set("osmosis_replication_base_url", "https://example.test/updates")
    osmium.io.Writer(pbf_path, header).close()
    metadata = read_osm_source_metadata(pbf_path)
    assert metadata["replication_timestamp"] == "2026-08-26T20:22:15Z"
    assert metadata["replication_sequence"] == 4892
    assert metadata["replication_base_url"] == "https://example.test/updates"


def test_transit_dedup_includes_30m_boundary_and_normalizes_name() -> None:
    lon, lat, _back_azimuth = GEOD.fwd(-122.42, 37.77, 90, 30)
    record = {
        "category": "public_transport",
        "name": "  MAIN   stop ",
        "analysis_point": {"type": "Point", "coordinates": [lon, lat]},
    }
    assert _is_duplicate_transit(record, {"main stop": [[-122.42, 37.77]]})
