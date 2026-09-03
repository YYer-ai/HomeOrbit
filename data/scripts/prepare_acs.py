"""将 ACS 2023 五年表格式文件整理为湾区 tract 级 JSON。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any


BAY_AREA_COUNTIES = {
    "001": "Alameda",
    "013": "Contra Costa",
    "041": "Marin",
    "055": "Napa",
    "075": "San Francisco",
    "081": "San Mateo",
    "085": "Santa Clara",
    "095": "Solano",
    "097": "Sonoma",
}
TRACT_GEO_ID = re.compile(r"^1400000US06(?P<county>\d{3})(?P<tract>\d{6})$")
MISSING_SENTINELS = {-222222222, -333333333, -555555555, -666666666, -888888888}
COMMUTE_BIN_MIDPOINTS = (2.5, 7, 12, 17, 22, 27, 32, 37, 42, 52, 74.5, 100)


def parse_estimate(value: str | None) -> int | None:
    if value in (None, "", "null"):
        return None
    number = int(value)
    return None if number in MISSING_SENTINELS else number


def load_bay_area_rows(path: Path) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source, delimiter="|"):
            geoid = row["GEO_ID"]
            match = TRACT_GEO_ID.fullmatch(geoid)
            if match and match.group("county") in BAY_AREA_COUNTIES:
                rows[geoid] = row
    return rows


def estimate_mean_commute_minutes(row: dict[str, str]) -> float | None:
    counts = [parse_estimate(row.get(f"B08303_E{index:03d}")) for index in range(2, 14)]
    valid = [(count, midpoint) for count, midpoint in zip(counts, COMMUTE_BIN_MIDPOINTS) if count is not None]
    total = sum(count for count, _ in valid)
    if total <= 0:
        return None
    return round(sum(count * midpoint for count, midpoint in valid) / total, 1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def build_payload(input_dir: Path, year: int = 2023) -> dict[str, Any]:
    paths = {
        "population": input_dir / f"acsdt5y{year}-b01003.dat",
        "income": input_dir / f"acsdt5y{year}-b19013.dat",
        "commute": input_dir / f"acsdt5y{year}-b08303.dat",
    }
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(f"缺少 ACS 文件: {path}")

    tables = {name: load_bay_area_rows(path) for name, path in paths.items()}
    geoids = set(tables["population"])
    if not geoids or any(set(rows) != geoids for rows in tables.values()):
        counts = {name: len(rows) for name, rows in tables.items()}
        raise ValueError(f"ACS 三表的湾区 tract 集合不一致: {counts}")

    records = []
    for geoid in sorted(geoids):
        match = TRACT_GEO_ID.fullmatch(geoid)
        assert match is not None
        population = tables["population"][geoid]
        income = tables["income"][geoid]
        commute = tables["commute"][geoid]
        county_fips = match.group("county")
        records.append(
            {
                "geoid": geoid,
                "county_fips": county_fips,
                "county_name": BAY_AREA_COUNTIES[county_fips],
                "tract_code": match.group("tract"),
                "population": parse_estimate(population.get("B01003_E001")),
                "population_moe": parse_estimate(population.get("B01003_M001")),
                "median_household_income": parse_estimate(income.get("B19013_E001")),
                "median_household_income_moe": parse_estimate(income.get("B19013_M001")),
                "workers_with_commute_time": parse_estimate(commute.get("B08303_E001")),
                "estimated_mean_commute_minutes": estimate_mean_commute_minutes(commute),
            }
        )

    return {
        "metadata": {
            "source": "U.S. Census Bureau ACS 5-Year Table-Based Summary File",
            "year": year,
            "state_fips": "06",
            "geography": "census tract",
            "county_fips": sorted(BAY_AREA_COUNTIES),
            "record_count": len(records),
            "commute_estimate_note": "Weighted midpoint estimate; the 90+ minute bin uses 100 minutes.",
            "source_files": {
                name: {"name": path.name, "sha256": sha256(path)} for name, path in paths.items()
            },
        },
        "records": records,
    }


def main() -> None:
    data_dir = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=data_dir / "raw" / "acs" / "2023")
    parser.add_argument("--output", type=Path, default=data_dir / "processed" / "bay-area-acs-2023.json")
    args = parser.parse_args()

    payload = build_payload(args.input_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {payload['metadata']['record_count']} tracts -> {args.output}")


if __name__ == "__main__":
    main()
