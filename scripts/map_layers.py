#!/usr/bin/env python3
"""Cached map-layer helpers for IEM-style warning images."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import matplotlib.patheffects as pe
from cartopy import crs as ccrs
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

REFERENCE_DIR = Path("data/reference")
PLACES_FILE = REFERENCE_DIR / "places.parquet"
ROADS_FILE = REFERENCE_DIR / "roads.parquet"


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


def add_cached_roads(ax, extent, road_color="#c89433") -> bool:
    roads = _read_layer(str(ROADS_FILE))
    if roads is None:
        return False
    try:
        minx, maxx, miny, maxy = extent
        bbox = box(minx, miny, maxx, maxy)
        candidates = roads.iloc[roads.sindex.query(bbox, predicate="intersects")]
        if candidates.empty:
            return False
        for geom in candidates.geometry:
            if geom is None or geom.is_empty:
                continue
            geoms = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
            for line in geoms:
                x, y = line.xy
                ax.plot(
                    x,
                    y,
                    transform=ccrs.PlateCarree(),
                    color=road_color,
                    linewidth=0.45,
                    alpha=0.75,
                    zorder=3,
                )
        return True
    except Exception:
        return False


def get_place_labels(extent, warning_geom: BaseGeometry, max_count=11):
    places = _read_layer(str(PLACES_FILE))
    if places is None:
        return []
    try:
        minx, maxx, miny, maxy = extent
        bbox = box(minx, miny, maxx, maxy)
        candidates = places.iloc[places.sindex.query(bbox, predicate="intersects")].copy()
        if candidates.empty:
            return []
        centroid = warning_geom.centroid
        candidates["_dist"] = candidates.geometry.distance(centroid)
        if "population" not in candidates.columns:
            candidates["population"] = 0
        candidates["_score"] = candidates["_dist"] - (
            candidates["population"].fillna(0).astype(float) / 10000000.0
        )
        candidates = candidates.sort_values("_score").head(max_count)
        labels = []
        for _, row in candidates.iterrows():
            name = row.get("name") or row.get("NAME") or row.get("feature_name") or ""
            if not name:
                continue
            pt = row.geometry
            labels.append((str(name), float(pt.y), float(pt.x)))
        return labels
    except Exception:
        return []


def add_place_labels(ax, labels, land_color="#f4f0df"):
    for name, lat, lon in labels:
        ax.plot(
            lon,
            lat,
            marker=".",
            color="#222222",
            markersize=2.5,
            transform=ccrs.PlateCarree(),
            zorder=31,
        )
        ax.text(
            lon,
            lat + 0.025,
            name,
            transform=ccrs.PlateCarree(),
            fontsize=7.5,
            color="#7b7b7b",
            ha="center",
            va="bottom",
            zorder=32,
            path_effects=[pe.withStroke(linewidth=1.4, foreground=land_color)],
        )
