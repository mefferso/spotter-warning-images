#!/usr/bin/env python3
"""Exposure helpers for warning polygons.

Uses cached reference layers when present:
  data/reference/population_blocks.parquet
  data/reference/schools.parquet
  data/reference/hospitals.parquet

Falls back gracefully to zeros if layers have not been prepared yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
from shapely.geometry.base import BaseGeometry

REFERENCE_DIR = Path("data/reference")
POPULATION_FILE = REFERENCE_DIR / "population_blocks.parquet"
SCHOOLS_FILE = REFERENCE_DIR / "schools.parquet"
HOSPITALS_FILE = REFERENCE_DIR / "hospitals.parquet"
AREA_CRS = "EPSG:5070"


@dataclass(frozen=True)
class Exposure:
    population: int
    schools: int
    hospitals: int
    source: str


@lru_cache(maxsize=8)
def _read_layer(path_str: str) -> gpd.GeoDataFrame | None:
    path = Path(path_str)
    if not path.exists():
        return None
    try:
        gdf = gpd.read_parquet(path)
    except Exception:
        return None
    if gdf.empty or "geometry" not in gdf:
        return None
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    return gdf


def _warning_gdf(geom: BaseGeometry) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"id": [1]}, geometry=[geom], crs="EPSG:4326")


def _count_points(path: Path, warning: gpd.GeoDataFrame) -> int:
    gdf = _read_layer(str(path))
    if gdf is None:
        return 0
    try:
        poly = warning.to_crs(gdf.crs).geometry.iloc[0]
        subset = gdf[gdf.sindex.query(poly, predicate="intersects")]
        if subset.empty:
            return 0
        return int(subset.within(poly).sum())
    except Exception:
        return 0


def _area_weighted_population(warning: gpd.GeoDataFrame) -> int:
    blocks = _read_layer(str(POPULATION_FILE))
    if blocks is None:
        return 0
    if "population" not in blocks.columns:
        return 0

    try:
        warning_area = warning.to_crs(AREA_CRS).geometry.iloc[0]
        blocks_area = blocks.to_crs(AREA_CRS)
        candidate_idx = blocks_area.sindex.query(warning_area, predicate="intersects")
        candidates = blocks_area.iloc[candidate_idx].copy()
        if candidates.empty:
            return 0

        candidates["_block_area"] = candidates.geometry.area
        candidates = candidates[candidates["_block_area"] > 0].copy()
        candidates["_intersection_area"] = candidates.geometry.intersection(warning_area).area
        candidates = candidates[candidates["_intersection_area"] > 0].copy()
        if candidates.empty:
            return 0

        weighted = candidates["population"].astype(float) * (
            candidates["_intersection_area"] / candidates["_block_area"]
        )
        return int(round(float(weighted.sum())))
    except Exception:
        return 0


def calculate_exposure(geom: BaseGeometry) -> Exposure:
    """Calculate exposure values for a warning polygon.

    Returns zeros if reference files are missing. This avoids breaking the
    warning-image workflow before the prep workflow has been run.
    """
    warning = _warning_gdf(geom)
    pop = _area_weighted_population(warning)
    schools = _count_points(SCHOOLS_FILE, warning)
    hospitals = _count_points(HOSPITALS_FILE, warning)

    available = []
    if POPULATION_FILE.exists():
        available.append("population")
    if SCHOOLS_FILE.exists():
        available.append("schools")
    if HOSPITALS_FILE.exists():
        available.append("hospitals")

    return Exposure(
        population=pop,
        schools=schools,
        hospitals=hospitals,
        source=",".join(available) if available else "fallback_missing_reference_layers",
    )
