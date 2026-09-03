from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import shapefile
import pytest


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from prepare_population import build_population_products, normalize_acs_geoid  # noqa: E402


def _create_tract_zip(tmp_path: Path, *, geoid: str = "06075010100", aland: int = 2_000_000) -> Path:
    source = tmp_path / "tracts"
    writer = shapefile.Writer(str(source), shapeType=shapefile.POLYGON)
    writer.field("STATEFP", "C", 2)
    writer.field("COUNTYFP", "C", 3)
    writer.field("GEOID", "C", 11)
    writer.field("ALAND", "N", 18, 0)
    writer.poly([[[-122.43, 37.76], [-122.41, 37.76], [-122.41, 37.78], [-122.43, 37.78], [-122.43, 37.76]]])
    writer.record("06", "075", geoid, aland)
    writer.close()
    zip_path = tmp_path / "tracts.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for extension in ("shp", "shx", "dbf"):
            archive.write(source.with_suffix(f".{extension}"), arcname=f"tracts.{extension}")
    return zip_path


def test_normalize_acs_geoid() -> None:
    assert normalize_acs_geoid("1400000US06075010100") == "06075010100"


def test_population_join_and_density(tmp_path: Path) -> None:
    acs_path = tmp_path / "acs.json"
    acs_path.write_text(
        json.dumps(
            {
                "metadata": {"year": 2023},
                "records": [
                    {
                        "geoid": "1400000US06075010100",
                        "county_name": "San Francisco",
                        "population": 4_000,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    ndjson_path = tmp_path / "tracts.ndjson"
    geojson_path = tmp_path / "tract-density.geojson"

    count = build_population_products(
        acs_path,
        _create_tract_zip(tmp_path),
        ndjson_path,
        geojson_path,
        expected_water_geoids=set(),
        expected_total=1,
    )

    assert count == 1
    tract = json.loads(ndjson_path.read_text(encoding="utf-8"))
    assert tract["geoid"] == "06075010100"
    assert tract["population"] == 4_000
    assert tract["aland_m2"] == 2_000_000
    assert tract["population_density_km2"] == 2_000
    assert tract["geometry"]["type"] == "Polygon"
    feature = json.loads(geojson_path.read_text(encoding="utf-8"))["features"][0]
    assert feature["properties"]["county_name"] == "San Francisco"
    assert feature["properties"]["acs_year"] == 2023


def test_rejects_unapproved_water_tract_set(tmp_path: Path) -> None:
    acs_path = tmp_path / "acs.json"
    acs_path.write_text(json.dumps({"metadata": {"year": 2023}, "records": [
        {"geoid": "1400000US06075010100", "county_name": "San Francisco", "population": 4_000},
        {"geoid": "1400000US06075990100", "county_name": "San Francisco", "population": 0},
    ]}), encoding="utf-8")
    water_path = tmp_path / "water.geojson"
    water_path.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "geometry": {"type": "MultiPolygon", "coordinates": [[[[-122.5, 37.7], [-122.4, 37.7], [-122.4, 37.8], [-122.5, 37.8], [-122.5, 37.7]]]]},
        "properties": {"GEOID": "06075990100", "STATE": "06", "COUNTY": "075", "AREALAND": 0, "AREAWATER": 1},
    }]}), encoding="utf-8")

    with pytest.raises(ValueError, match="approved water tract GEOIDs"):
        build_population_products(
            acs_path, _create_tract_zip(tmp_path), tmp_path / "out.ndjson", tmp_path / "out.geojson",
            water_path, expected_water_geoids={"06075990200"}, expected_total=2,
        )


@pytest.mark.parametrize("case", ["duplicate", "missing_population", "unmatched", "nonpositive_aland"])
def test_rejects_invalid_land_inputs(tmp_path: Path, case: str) -> None:
    record = {"geoid": "1400000US06075010100", "county_name": "San Francisco", "population": 4_000}
    records = [record, dict(record)] if case == "duplicate" else [dict(record)]
    if case == "missing_population":
        records[0]["population"] = None
    if case == "unmatched":
        records[0]["geoid"] = "1400000US06075010200"
    acs_path = tmp_path / "acs.json"
    acs_path.write_text(json.dumps({"metadata": {"year": 2023}, "records": records}), encoding="utf-8")
    expected = {"duplicate": "Duplicate ACS", "missing_population": "Missing population", "unmatched": "No ACS record", "nonpositive_aland": "Non-positive ALAND"}[case]
    with pytest.raises(ValueError, match=expected):
        build_population_products(
            acs_path, _create_tract_zip(tmp_path, aland=0 if case == "nonpositive_aland" else 2_000_000),
            tmp_path / "out.ndjson", tmp_path / "out.geojson", expected_water_geoids=set(), expected_total=1,
        )


@pytest.mark.parametrize(
    ("property_name", "property_value", "population"),
    [("STATE", "07", 0), ("COUNTY", "081", 0), ("AREAWATER", 0, 0), ("AREAWATER", 1, 1)],
)
def test_rejects_invalid_approved_water_attributes(
    tmp_path: Path, property_name: str, property_value: str | int, population: int,
) -> None:
    water_geoid = "06075990100"
    acs_path = tmp_path / "acs.json"
    acs_path.write_text(json.dumps({"metadata": {"year": 2023}, "records": [
        {"geoid": "1400000US06075010100", "county_name": "San Francisco", "population": 4_000},
        {"geoid": f"1400000US{water_geoid}", "county_name": "San Francisco", "population": population},
    ]}), encoding="utf-8")
    properties = {"GEOID": water_geoid, "STATE": "06", "COUNTY": "075", "AREALAND": 0, "AREAWATER": 1}
    properties[property_name] = property_value
    water_path = tmp_path / "water.geojson"
    water_path.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": properties,
        "geometry": {"type": "MultiPolygon", "coordinates": [[[[-122.5, 37.7], [-122.4, 37.7], [-122.4, 37.8], [-122.5, 37.8], [-122.5, 37.7]]]]},
    }]}), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid water tract attributes"):
        build_population_products(
            acs_path, _create_tract_zip(tmp_path), tmp_path / "out.ndjson", tmp_path / "out.geojson",
            water_path, expected_water_geoids={water_geoid}, expected_total=2,
        )


def test_rejects_wrong_final_total(tmp_path: Path) -> None:
    acs_path = tmp_path / "acs.json"
    acs_path.write_text(json.dumps({"metadata": {"year": 2023}, "records": [
        {"geoid": "1400000US06075010100", "county_name": "San Francisco", "population": 4_000},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="Expected 2 tracts, found 1"):
        build_population_products(
            acs_path, _create_tract_zip(tmp_path), tmp_path / "out.ndjson", tmp_path / "out.geojson",
            expected_water_geoids=set(), expected_total=2,
        )
