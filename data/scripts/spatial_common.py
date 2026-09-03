from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
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
BAY_AREA_BBOX = (-123.3, 37.2, -121.7, 38.0)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
METADATA_PATH = PROJECT_ROOT / "data" / "processed" / "spatial-data-metadata.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def update_metadata(product: str, output_path: Path, record_count: int, **details: Any) -> None:
    metadata: dict[str, Any] = {"schema_version": 1, "products": {}}
    if METADATA_PATH.exists():
        metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    generated_at = datetime.now(UTC).isoformat()
    metadata["generated_at"] = generated_at
    metadata.setdefault("products", {})[product] = {
        "path": output_path.resolve().relative_to(PROJECT_ROOT).as_posix(),
        "record_count": record_count,
        "size_bytes": output_path.stat().st_size,
        "sha256": sha256_file(output_path),
        "generated_at": generated_at,
        **details,
    }
    METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    METADATA_PATH.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
