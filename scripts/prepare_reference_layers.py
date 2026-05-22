#!/usr/bin/env python3
"""Prepare cached GIS layers for IEM-style warning images."""

from __future__ import annotations

import argparse
import io
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import box

REFERENCE_DIR = Path("data/reference")
STATE_FIPS = {"LA": "22", "MS": "28", "AL": "01"}
STATE_BBOX = {
    "LA": (-94.1, 28.6, -88.7, 33.1),
    "MS": (-91.8, 30.0, -88.0, 35.1),
    "AL": (-88.6, 30.1, -84.8, 35.1),
}

HIFLD_SERVICES = {
    "hospitals": "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/Hospitals/FeatureServer/0/query",
    "public_schools": "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/Public_Schools/FeatureServer/0/query",
    "private_schools": "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/Private_Schools/FeatureServer/0/query",
}


def _download(url: str) -> bytes:
    r = requests.get(url, timeout=180, headers={"User-Agent": "spotter-warning-images reference builder"})
    r.raise_for_status()
    return r.content


def _read_zipped_shapefile(url: str) -> gpd.GeoDataFrame:
    data = _download(url)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(tmp_path)
        shp = next(tmp_path.glob("*.shp"))
        return gpd.read_file(shp)


def _state_filter(gdf: gpd.GeoDataFrame, states: Iterable[str]) -> gpd.GeoDataFrame:
    boxes = [box(*STATE_BBOX[s]) for s in states]
    mask = False
    for b in boxes:
        mask = mask | gdf.intersects(b)
    return gdf[mask].copy()


def _save(gdf: gpd.GeoDataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    gdf.to_parquet(path, index=False)
    print(f"wrote {len(gdf):,} features -> {path}")


def _normalize_block_geoids(blocks: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if "GEOID20" in blocks.columns:
        return blocks
    if "GEOID" in blocks.columns:
        return blocks.rename(columns={"GEOID": "GEOID20"})
    pieces = ["STATEFP20", "COUNTYFP20", "TRACTCE20", "BLOCKCE20"]
    if all(col in blocks.columns for col in pieces):
        blocks = blocks.copy()
        blocks["GEOID20"] = (
            blocks["STATEFP20"].astype(str).str.zfill(2)
            + blocks["COUNTYFP20"].astype(str).str.zfill(3)
            + blocks["TRACTCE20"].astype(str).str.zfill(6)
            + blocks["BLOCKCE20"].astype(str).str.zfill(4)
        )
        return blocks
    raise ValueError("Could not find or build GEOID20 for Census blocks")


def _block_population_series(blocks: gpd.GeoDataFrame) -> pd.Series:
    for col in ["POP20", "POP100", "P0010001", "POPULATION", "POP"]:
        if col in blocks.columns:
            print(f"using block population field: {col}")
            return pd.to_numeric(blocks[col], errors="coerce").fillna(0).astype(int)
    print("WARNING: no population field found in block shapefile; using zeros")
    return pd.Series([0] * len(blocks), index=blocks.index, dtype="int64")


def prepare_population(states: list[str]) -> None:
    out = []
    for state in states:
        fips = STATE_FIPS[state]
        print(f"population blocks: {state}")
        blocks_url = f"https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_{fips}_tabblock20.zip"
        blocks = _read_zipped_shapefile(blocks_url).to_crs("EPSG:4326")
        blocks = _normalize_block_geoids(blocks)
        blocks["population"] = _block_population_series(blocks)
        out.append(blocks[["GEOID20", "population", "geometry"]].copy())
    gdf = gpd.GeoDataFrame(pd.concat(out, ignore_index=True), geometry="geometry", crs="EPSG:4326")
    _save(gdf, REFERENCE_DIR / "population_blocks.parquet")


def _arcgis_geojson_query(url: str, states: list[str]) -> gpd.GeoDataFrame:
    state_where = " OR ".join([f"STATE = '{s}'" for s in states])
    frames = []
    offset = 0
    while True:
        params = {
            "f": "geojson",
            "where": state_where,
            "outFields": "*",
            "outSR": "4326",
            "resultOffset": offset,
            "resultRecordCount": 2000,
        }
        r = requests.get(url, params=params, timeout=120)
        r.raise_for_status()
        data = r.json()
        features = data.get("features") or []
        if not features:
            break
        frames.append(gpd.GeoDataFrame.from_features(features, crs="EPSG:4326"))
        if len(features) < 2000:
            break
        offset += 2000
    if not frames:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs="EPSG:4326")
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs="EPSG:4326")


def prepare_schools(states: list[str]) -> None:
    frames = []
    for key in ["public_schools", "private_schools"]:
        print(f"schools: {key}")
        gdf = _arcgis_geojson_query(HIFLD_SERVICES[key], states)
        if not gdf.empty:
            gdf["source_layer"] = key
            frames.append(gdf)
    if frames:
        schools = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs="EPSG:4326")
    else:
        schools = gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs="EPSG:4326")
    cols = [c for c in ["NAME", "SCHOOL_NAME", "source_layer", "geometry"] if c in schools.columns]
    _save(schools[cols] if cols else schools, REFERENCE_DIR / "schools.parquet")


def prepare_hospitals(states: list[str]) -> None:
    print("hospitals")
    hospitals = _arcgis_geojson_query(HIFLD_SERVICES["hospitals"], states)
    cols = [c for c in ["NAME", "TYPE", "STATUS", "STATE", "geometry"] if c in hospitals.columns]
    _save(hospitals[cols] if cols else hospitals, REFERENCE_DIR / "hospitals.parquet")


def prepare_places(states: list[str]) -> None:
    frames = []
    for state in states:
        fips = STATE_FIPS[state]
        print(f"places: {state}")
        url = f"https://www2.census.gov/geo/tiger/TIGER2023/PLACE/tl_2023_{fips}_place.zip"
        places = _read_zipped_shapefile(url).to_crs("EPSG:4326")
        places["geometry"] = places.geometry.representative_point()
        places["state"] = state
        if "NAME" in places.columns:
            places["name"] = places["NAME"]
        elif "NAMELSAD" in places.columns:
            places["name"] = places["NAMELSAD"]
        else:
            places["name"] = ""
        places["population"] = 0
        frames.append(places[["name", "state", "population", "geometry"]])
    gdf = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs="EPSG:4326")
    _save(gdf, REFERENCE_DIR / "places.parquet")


def prepare_roads(states: list[str]) -> None:
    frames = []
    print("primary roads: US")
    try:
        primary = _read_zipped_shapefile("https://www2.census.gov/geo/tiger/TIGER2023/PRIMARYROADS/tl_2023_us_primaryroads.zip").to_crs("EPSG:4326")
        primary = _state_filter(primary, states)
        frames.append(primary)
    except Exception as exc:
        print(f"primary roads failed: {exc}")
    for state in states:
        fips = STATE_FIPS[state]
        print(f"primary/secondary roads: {state}")
        try:
            url = f"https://www2.census.gov/geo/tiger/TIGER2023/PRISECROADS/tl_2023_{fips}_prisecroads.zip"
            roads = _read_zipped_shapefile(url).to_crs("EPSG:4326")
            frames.append(roads)
        except Exception as exc:
            print(f"roads failed for {state}: {exc}")
    if frames:
        roads = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs="EPSG:4326")
    else:
        roads = gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs="EPSG:4326")
    cols = [c for c in ["FULLNAME", "RTTYP", "MTFCC", "geometry"] if c in roads.columns]
    _save(roads[cols] if cols else roads, REFERENCE_DIR / "roads.parquet")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--states", nargs="+", default=["LA", "MS", "AL"], choices=sorted(STATE_FIPS))
    parser.add_argument("--skip-population", action="store_true")
    parser.add_argument("--skip-schools", action="store_true")
    parser.add_argument("--skip-hospitals", action="store_true")
    parser.add_argument("--skip-places", action="store_true")
    parser.add_argument("--skip-roads", action="store_true")
    args = parser.parse_args()

    states = args.states
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)

    if not args.skip_population:
        prepare_population(states)
    if not args.skip_schools:
        prepare_schools(states)
    if not args.skip_hospitals:
        prepare_hospitals(states)
    if not args.skip_places:
        prepare_places(states)
    if not args.skip_roads:
        prepare_roads(states)

    manifest = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "states": states,
        "files": sorted(p.name for p in REFERENCE_DIR.glob("*.parquet")),
    }
    (REFERENCE_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
