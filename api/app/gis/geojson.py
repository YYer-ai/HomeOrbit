from __future__ import annotations

import math
from typing import Any


def is_position(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 2
        and all(
            isinstance(coordinate, (int, float))
            and not isinstance(coordinate, bool)
            and math.isfinite(coordinate)
            for coordinate in value
        )
    )


def is_linear_ring(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 4
        and all(is_position(position) for position in value)
        and value[0] == value[-1]
    )


def is_polygon_coordinates(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(is_linear_ring(ring) for ring in value)


def is_polygonal_geometry(geometry: Any) -> bool:
    if not isinstance(geometry, dict):
        return False
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "Polygon":
        return is_polygon_coordinates(coordinates)
    return (
        geometry.get("type") == "MultiPolygon"
        and isinstance(coordinates, list)
        and bool(coordinates)
        and all(is_polygon_coordinates(polygon) for polygon in coordinates)
    )


def is_polygonal_feature_collection(collection: Any) -> bool:
    if not isinstance(collection, dict) or collection.get("type") != "FeatureCollection":
        return False
    features = collection.get("features")
    return (
        isinstance(features, list)
        and bool(features)
        and all(
            isinstance(feature, dict)
            and feature.get("type") == "Feature"
            and is_polygonal_geometry(feature.get("geometry"))
            for feature in features
        )
    )
