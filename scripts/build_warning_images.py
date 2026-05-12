#!/usr/bin/env python3

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import requests
from shapely.geometry import shape

OUT_DIR = Path("docs/warning-images")
WFO = "LIX"

DARK = "#121212"       # Deeper background
LAND = "#242424"       # Dark gray land
WATER = "#1a1a1a"      # Even darker water
COUNTY_LINES = "#333333" # Subtle dark borders

WANTED_EVENTS = {
    "Tornado Warning": ("TO", "W"),
    "Severe Thunderstorm Warning": ("SV", "W"),
    "Flash Flood Warning": ("FF", "W"),
    "Special Marine Warning": ("MA", "W"),
    "Special Weather Statement": ("WW", "Y"),
}

CITY_POINTS = [
    ("New Orleans", 29.9511, -90.0715),
    ("Baton Rouge", 30.4515, -91.1871),
    ("Slidell", 30.2752, -89.7812),
    ("Mandeville", 30.3583, -90.0656),
    ("Covington", 30.4755, -90.1009),
    ("Hammond", 30.5044, -90.4612),
    ("Ponchatoula", 30.4388, -90.4415),
    ("Bogalusa", 30.7910, -89.8487),
    ("Picayune", 30.5255, -89.6795),
    ("Bay St. Louis", 30.3088, -89.3300),
    ("Gulfport", 30.3674, -89.0928),
    ("Biloxi", 30.3960, -88.8853),
    ("Pascagoula", 30.3658, -88.5561),
    ("McComb", 31.2446, -90.4532),
    ("Liberty", 31.1588, -90.8129),
    ("Kentwood", 30.9382, -90.5087),
    ("Amite", 30.7266, -90.5087),
    ("Franklinton", 30.8471, -90.1531),
    ("Laplace", 30.0666, -90.4801),
    ("Reserve", 30.0535, -90.5518),
    ("Lutcher", 30.0405, -90.6984),
    ("Grand Point", 30.0616, -90.7540),
    ("Paulina", 30.0260, -90.7148),
    ("Vacherie", 29.9671, -90.7054),
    ("Donaldsonville", 30.1010, -90.9929),
    ("Gonzales", 30.2385, -90.9201),
    ("Thibodaux", 29.7958, -90.8229),
    ("Houma", 29.5958, -90.7195),
    ("Galliano", 29.4422, -90.2992),
    ("Grand Isle", 29.2366, -89.9873),
    ("Port Sulphur", 29.4805, -89.6939),
]


def fetch_json(url):
    headers = {
        "User-Agent": "KLIX Spotter Warning Image Builder (mefferso@noaa.gov)",
        "Accept": "application/geo+json",
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def get_wfo_list(props):
    params = props.get("parameters") or {}
    wfos = params.get("WFO") or params.get("wfo") or []
    return [str(w).upper().replace("K", "") for w in wfos]


def is_klix_alert(feature):
    props = feature.get("properties") or {}
    event = props.get("event", "").strip()
    if event not in WANTED_EVENTS:
        return False

    wfos = get_wfo_list(props)
    if WFO in wfos:
        return True

    haystack = " ".join([
        str(feature.get("id") or ""),
        str(props.get("id") or ""),
        str(props.get("senderName") or ""),
        str(props.get("headline") or ""),
        str(props.get("description") or ""),
        str(props.get("instruction") or ""),
        str(props.get("areaDesc") or ""),
    ]).upper()
    return any(s in haystack for s in ["KLIX", "NWS NEW ORLEANS", "NEW ORLEANS/BATON ROUGE"])


def fetch_active_klix_warnings():
    features = []
    for area in ["LA", "MS"]:
        gj = fetch_json(f"https://api.weather.gov/alerts/active?area={area}")
        features.extend(gj.get("features") or [])
    return dedupe([f for f in features if is_klix_alert(f)])


def fetch_latest_klix_warning():
    features = []
    for area in ["LA", "MS"]:
        gj = fetch_json(
            f"https://api.weather.gov/alerts?area={area}&status=actual&message_type=alert&limit=100"
        )
        features.extend(gj.get("features") or [])
    matches = dedupe([f for f in features if is_klix_alert(f)])
    matches.sort(
        key=lambda f: parse_time(
            (f.get("properties") or {}).get("sent")
            or (f.get("properties") or {}).get("effective")
            or ""
        ),
        reverse=True,
    )
    return matches[:1]


def dedupe(features):
    seen = set()
    out = []
    for f in features:
        props = f.get("properties") or {}
        key = f.get("id") or props.get("id") or "|".join([
            props.get("event", ""),
            props.get("sent", ""),
            props.get("expires", ""),
            props.get("headline", ""),
        ])
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def parse_time(value):
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def event_color(event):
    if event == "Tornado Warning":
        return "#cf575c"
    if event == "Severe Thunderstorm Warning":
        return "#d5b13f"
    if event == "Flash Flood Warning":
        return "#3e7d3e"
    if event == "Special Marine Warning":
        return "#8a5bb8"
    return "#cf575c"


def safe_event_id(feature):
    props = feature.get("properties") or {}
    params = props.get("parameters") or {}
    vtecs = params.get("VTEC") or params.get("vtec") or []
    vtec = vtecs[0] if vtecs else ""
    m = re.search(r"\.K?([A-Z]{3})\.([A-Z]{2})\.([A-Z])\.(\d{4})\.", vtec)
    if m:
        wfo, phen, sig, etn = m.groups()
        return f"K{wfo}-{phen}-{sig}-{etn}"
    raw = feature.get("id") or props.get("id") or props.get("headline") or "warning"
    raw = re.sub(r"[^A-Za-z0-9_-]+", "-", raw)
    return raw[-80:]


def map_extent_for_geom(geom):
    minx, miny, maxx, maxy = geom.bounds
    dx = max(maxx - minx, 0.18)
    dy = max(maxy - miny, 0.18)

    # User requested zoomed out more than prior image.
    pad_x = max(dx * 1.05, 0.24)
    pad_y = max(dy * 1.05, 0.20)
    return [minx - pad_x, maxx + pad_x, miny - pad_y, maxy + pad_y]


def add_base_map(ax, extent, detail_scale="10m"):
    ax.set_extent(extent, crs=ccrs.PlateCarree())
    ax.set_facecolor(LAND)
    ax.add_feature(cfeature.LAND.with_scale(detail_scale), facecolor=LAND, zorder=0)
    ax.add_feature(cfeature.OCEAN.with_scale(detail_scale), facecolor=WATER, zorder=0)
    ax.add_feature(cfeature.LAKES.with_scale(detail_scale), facecolor=WATER, edgecolor="#9ab6c5", linewidth=0.35, zorder=1)
    ax.add_feature(cfeature.RIVERS.with_scale(detail_scale), edgecolor="#aec8d2", linewidth=0.45, zorder=2)
    ax.add_feature(cfeature.COASTLINE.with_scale(detail_scale), edgecolor="#555555", linewidth=0.65, zorder=3)
    ax.add_feature(cfeature.STATES.with_scale(detail_scale), edgecolor="#555555", linewidth=0.9, zorder=4)
    try:
        counties = cfeature.NaturalEarthFeature(
            "cultural", "admin_2_counties", detail_scale,
            facecolor="none", edgecolor="#8e8e8e"
        )
        ax.add_feature(counties, linewidth=0.35, alpha=0.75, zorder=4)
    except Exception:
        pass

    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_edgecolor(DARK)
        spine.set_linewidth(1.4)


def add_warning_polygon(ax, geom, color):
    geoms = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    for g in geoms:
        ax.add_geometries(
            [g],
            crs=ccrs.PlateCarree(),
            facecolor=color,
            edgecolor=color,       # Match border to the event color
            linewidth=3.5,         # Thicker, bolder edge
            alpha=0.35,            # Lower opacity to see the map
            zorder=10,             # [cite: 16]
        )


def add_city_labels(ax, extent, geom):
    minx, maxx, miny, maxy = extent[0], extent[1], extent[2], extent[3]
    centroid = geom.centroid
    ranked = []
    for name, lat, lon in CITY_POINTS:
        if minx <= lon <= maxx and miny <= lat <= maxy:
            dist = ((lon - centroid.x) ** 2 + (lat - centroid.y) ** 2) ** 0.5
            ranked.append((dist, name, lat, lon))

    for _, name, lat, lon in sorted(ranked)[:12]:
        # Add a subtle dot for the city location
        ax.plot(lon, lat, marker='o', color='white', markersize=3, transform=ccrs.PlateCarree(), zorder=19)
        
        # Draw the text slightly offset from the dot
        ax.text(
            lon, lat - 0.02, name,
            transform=ccrs.PlateCarree(),
            fontsize=9,
            fontweight="bold",
            color="#ffffff",  # White text for dark mode
            ha="center", va="top",
            zorder=20,        # 
            path_effects=[pe.withStroke(linewidth=1.5, foreground="#000000")], # Thin black stroke
        )


def draw_warning_image(feature, output_path):
    props = feature.get("properties") or {}
    geom_json = feature.get("geometry")
    if not geom_json:
        return False

    geom = shape(geom_json)
    event = props.get("event", "Warning")
    color = event_color(event)
    extent = map_extent_for_geom(geom)

    # Simple card: warning header + full map only.
    fig = plt.figure(figsize=(10.0, 7.5), dpi=160)
    fig.patch.set_facecolor(DARK)

    ax_header = fig.add_axes([0.025, 0.865, 0.950, 0.110])
    ax_map = fig.add_axes([0.025, 0.035, 0.950, 0.830], projection=ccrs.PlateCarree())

    ax_header.set_facecolor(color)
    ax_header.text(
        0.5, 0.50, event,
        ha="center", va="center",
        fontsize=30,
        color="white",
        fontweight="bold",
        family="DejaVu Sans",
    )
    ax_header.set_xticks([])
    ax_header.set_yticks([])
    for s in ax_header.spines.values():
        s.set_visible(False)

    add_base_map(ax_map, extent, detail_scale="10m")
    add_warning_polygon(ax_map, geom, color)
    add_city_labels(ax_map, extent, geom)
    ax_map.text(
        0.988, 0.018, "NWS New Orleans/Baton Rouge",
        transform=ax_map.transAxes,
        ha="right", va="bottom",
        fontsize=8.5,
        color="#24415a",
        bbox=dict(facecolor="white", alpha=0.72, edgecolor="none", pad=2),
        zorder=50,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, facecolor=fig.get_facecolor(), bbox_inches=None, pad_inches=0)
    plt.close(fig)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["active", "latest"],
        default="active",
        help="active = active warnings only; latest = latest recently issued KLIX warning for testing",
    )
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.mode == "latest":
        features = fetch_latest_klix_warning()
        mode_used = "latest"
    else:
        features = fetch_active_klix_warnings()
        mode_used = "active"
        if not features:
            features = fetch_latest_klix_warning()
            mode_used = "latest_fallback"

    if not features:
        print("No KLIX matching warnings found.")

    records = []
    for feature in features:
        props = feature.get("properties") or {}
        img_id = safe_event_id(feature)
        filename = f"{img_id}.png"
        out_path = OUT_DIR / filename

        ok = draw_warning_image(feature, out_path)
        if not ok:
            continue

        latest_path = OUT_DIR / "latest.png"
        latest_path.write_bytes(out_path.read_bytes())
        records.append({
            "id": feature.get("id") or props.get("id") or "",
            "event": props.get("event") or "",
            "headline": props.get("headline") or "",
            "sent": props.get("sent") or "",
            "effective": props.get("effective") or "",
            "expires": props.get("expires") or "",
            "areaDesc": props.get("areaDesc") or "",
            "filename": filename,
            "raw_url": f"https://raw.githubusercontent.com/mefferso/spotter-warning-images/main/docs/warning-images/{filename}",
            "latest_raw_url": "https://raw.githubusercontent.com/mefferso/spotter-warning-images/main/docs/warning-images/latest.png",
        })

    latest_json = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode_used,
        "wfo": WFO,
        "count": len(records),
        "warnings": records,
    }
    (OUT_DIR / "latest.json").write_text(json.dumps(latest_json, indent=2), encoding="utf-8")
    print(json.dumps(latest_json, indent=2))


if __name__ == "__main__":
    main()
