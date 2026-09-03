from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseModel):
    """应用运行配置，仅读取本项目进程使用的环境变量。"""

    database_url: str = "postgresql://homeorbit:homeorbit_dev@127.0.0.1:5432/homeorbit"
    valhalla_url: str = "http://127.0.0.1:8002"
    valhalla_timeout_seconds: float = Field(default=5.0, gt=0)
    data_root: Path = PROJECT_ROOT / "data"
    population_density_path: Path = PROJECT_ROOT / "web" / "public" / "data" / "bay-area-tract-density.geojson"
    dataset_version: str = "bay-area-acs-2023"
    acs_year: int = 2023
    osm_version: str = "california-latest"

    def __init__(self, **values: Any) -> None:
        env_names = {
            "database_url": "HOMEORBIT_DATABASE_URL",
            "valhalla_url": "HOMEORBIT_VALHALLA_URL",
            "valhalla_timeout_seconds": "HOMEORBIT_VALHALLA_TIMEOUT_SECONDS",
            "data_root": "HOMEORBIT_DATA_ROOT",
            "population_density_path": "HOMEORBIT_POPULATION_DENSITY_PATH",
            "dataset_version": "HOMEORBIT_DATASET_VERSION",
            "acs_year": "HOMEORBIT_ACS_YEAR",
            "osm_version": "HOMEORBIT_OSM_VERSION",
        }
        for field_name, env_name in env_names.items():
            if field_name not in values and (env_value := os.getenv(env_name)) is not None:
                values[field_name] = env_value
        super().__init__(**values)


def get_settings() -> Settings:
    return Settings()
