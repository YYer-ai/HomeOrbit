from __future__ import annotations

import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare_acs.py"
SPEC = importlib.util.spec_from_file_location("prepare_acs", SCRIPT)
assert SPEC and SPEC.loader
prepare_acs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare_acs)


class PrepareAcsTests(unittest.TestCase):
    def test_parse_estimate_handles_census_sentinels(self) -> None:
        self.assertEqual(prepare_acs.parse_estimate("42"), 42)
        self.assertIsNone(prepare_acs.parse_estimate("-666666666"))
        self.assertIsNone(prepare_acs.parse_estimate(""))

    def test_build_payload_filters_and_merges_bay_area_tracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            included = "1400000US06075010100"
            excluded = "1400000US06053010100"
            self._write_table(
                root / "acsdt5y2023-b01003.dat",
                ["GEO_ID", "B01003_E001", "B01003_M001"],
                [[included, "1000", "25"], [excluded, "2000", "30"]],
            )
            self._write_table(
                root / "acsdt5y2023-b19013.dat",
                ["GEO_ID", "B19013_E001", "B19013_M001"],
                [[included, "120000", "5000"], [excluded, "90000", "4000"]],
            )
            commute_header = ["GEO_ID", "B08303_E001", "B08303_M001"] + [
                field
                for index in range(2, 14)
                for field in (f"B08303_E{index:03d}", f"B08303_M{index:03d}")
            ]
            commute_values = [included, "12", "1"] + [value for _ in range(12) for value in ("1", "0")]
            excluded_values = [excluded, "12", "1"] + [value for _ in range(12) for value in ("1", "0")]
            self._write_table(root / "acsdt5y2023-b08303.dat", commute_header, [commute_values, excluded_values])

            payload = prepare_acs.build_payload(root)

            self.assertEqual(payload["metadata"]["record_count"], 1)
            record = payload["records"][0]
            self.assertEqual(record["geoid"], included)
            self.assertEqual(record["county_name"], "San Francisco")
            self.assertEqual(record["population"], 1000)
            self.assertEqual(record["median_household_income"], 120000)
            self.assertEqual(record["estimated_mean_commute_minutes"], 35.4)

    @staticmethod
    def _write_table(path: Path, header: list[str], rows: list[list[str]]) -> None:
        with path.open("w", encoding="utf-8", newline="") as target:
            writer = csv.writer(target, delimiter="|", lineterminator="\n")
            writer.writerow(header)
            writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
