#!/usr/bin/env python3

import argparse
import json
import math
import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
import requests
from matplotlib.patches import Circle, Rectangle
from shapely.geometry import box, shape

OUT_DIR = Path("docs/iem-style-warning-images")
WFO = "LIX"

CANVAS_W = 700
CANVAS_H = 540
DPI = 100

SIDEBAR_BG = "#26343c"
HEADER_RED = "#c95559"
TEXT_WHITE = "#f6f6f6"
PANEL_RULE = "#b6535a"
LAND = "#f5f2de"
WATER = "#a9cff0"
ROAD = "#d4a23a"
BOUNDARY = "#4e4e4e"
WARNING_FILL = "#c95559"

WANTED_EVENTS = {
    "Tornado Warning": ("TO", "W"),
    "Severe Thunderstorm Warning": ("SV", "W"),
    "Flash Flood Warning": ("FF", "W"),
    "Special Marine Warning": ("MA", "W"),
    "Special Weather Statement": ("WW", "Y"),
}

CITY_POINTS = [
    ("New Orleans", 29.9511, -90.0715), ("Baton Rouge", 30.4515, -91.1871),
    ("Slidell", 30.2752, -89.7812), ("Mandeville", 30.3583, -90.0656),
    ("Covington", 30.4755, -90.1009), ("Hammond", 30.5044, -90.4612),
    ("Ponchatoula", 30.4388, -90.4415), ("Bogalusa", 30.7910, -89.8487),
    ("Picayune", 30.5255, -89.6795), ("Bay St. Louis", 30.3088, -89.3300),
    ("Gulfport", 30.3674, -89.0928), ("Biloxi", 30.3960, -88.8853),
    ("Pascagoula", 30.3658, -88.5561), ("McComb", 31.2446, -90.4532),
    ("Liberty", 31.1588, -90.8129), ("Kentwood", 30.9382, -90.5087),
    ("Amite", 30.7266, -90.5087), ("Franklinton", 30.8471, -90.1531),
    ("Laplace", 30.0666, -90.4801), ("Reserve", 30.0535, -90.5518),
    ("Lutcher", 30.0405, -90.6984), ("Paulina", 30.0260, -90.7148),
    ("Vacherie", 29.9671, -90.7054), ("Donaldsonville", 30.1010, -90.9929),
    ("Gonzales", 30.2385, -90.9201), ("Thibodaux", 29.7958, -90.8229),
    ("Houma", 29.5958, -90.7195), ("Galliano", 29.4422, -90.2992),
    ("Grand Isle", 29.2366, -89.9873), ("Port Sulphur", 29.4805, -89.6939),
    ("Kiln", 30.4096, -89.4359), ("Santa Rosa", 30.6727, -86.9816),
    ("Pearlington", 30.2466, -89.6117), ("Lakeshore", 30.2430, -89.4384),
    ("DeLisle", 30.3796, -89.2645), ("McNeill", 30.6677, -89.6367),
    ("Carriere", 30.6169, -89.6526), ("Ozona", 30.6288, -89.6437),
    ("Nicholson", 30.4771, -89.6939),
]


def fetch_json(url):
    headers = {"User-Agent": "KLIX IEM-style Warning Image Builder (mefferso@noaa.gov)", "Accept": "application/geo+json"}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def get_wfo_list(props):
    params = props.get("parameters") or {}
    wfos = params.get("WFO") or params.get("wfo") or []
    return [str(w).upper().replace("K", "") for w in wfos]


def is_klix_alert(feature):
    props = feature.get("properties") or {}
    event = props.get("event", "").strip()
    if event not in WANTED_EVENTS:
        return False
    if WFO in get_wfo_list(props):
        return True
    haystack = " ".join(str(x or "") for x in [feature.get("id"), props.get("id"), props.get("senderName"), props.get("headline"), props.get("description"), props.get("areaDesc")]).upper()
    return any(s in haystack for s in ["KLIX", "NWS NEW ORLEANS", "NEW ORLEANS/BATON ROUGE"])


def dedupe(features):
    seen = set(); out = []
    for f in features:
        props = f.get("properties") or {}
        key = f.get("id") or props.get("id") or "|".join([props.get("event", ""), props.get("sent", ""), props.get("expires", ""), props.get("headline", "")])
        if key not in seen:
            seen.add(key); out.append(f)
    return out


def fetch_active_klix_warnings():
    features = []
    for area in ["LA", "MS"]:
        features.extend(fetch_json(f"https://api.weather.gov/alerts/active?area={area}").get("features") or [])
    return dedupe([f for f in features if is_klix_alert(f)])


def fetch_latest_klix_warning():
    features = []
    for area in ["LA", "MS"]:
        features.extend(fetch_json(f"https://api.weather.gov/alerts?area={area}&status=actual&message_type=alert&limit=100").get("features") or [])
    matches = dedupe([f for f in features if is_klix_alert(f)])
    matches.sort(key=lambda f: parse_time((f.get("properties") or {}).get("sent") or "") or datetime.fromtimestamp(0, tz=timezone.utc), reverse=True)
    return matches[:1]


def event_color(event):
    return {
        "Tornado Warning": "#c95559",
        "Severe Thunderstorm Warning": "#d0ad39",
        "Flash Flood Warning": "#c95559",
        "Special Marine Warning": "#9a70bd",
        "Special Weather Statement": "#c95559",
    }.get(event, HEADER_RED)


def safe_event_id(feature):
    props = feature.get("properties") or {}; params = props.get("parameters") or {}
    vtecs = params.get("VTEC") or params.get("vtec") or []
    vtec = vtecs[0] if vtecs else ""
    m = re.search(r"\.K?([A-Z]{3})\.([A-Z]{2})\.([A-Z])\.(\d{4})\.", vtec)
    if m:
        wfo, phen, sig, etn = m.groups(); return f"K{wfo}-{phen}-{sig}-{etn}"
    raw = feature.get("id") or props.get("id") or props.get("headline") or "warning"
    return re.sub(r"[^A-Za-z0-9_-]+", "-", raw)[-80:]


def map_extent_for_geom(geom):
    minx, miny, maxx, maxy = geom.bounds
    dx = max(maxx - minx, 0.42); dy = max(maxy - miny, 0.35)
    return [minx - dx * 0.50, maxx + dx * 0.42, miny - dy * 0.50, maxy + dy * 0.42]


def add_light_basemap(ax, extent, scale="10m", include_roads=True):
    ax.set_extent(extent, crs=ccrs.PlateCarree())
    ax.set_facecolor(LAND)
    ax.add_feature(cfeature.LAND.with_scale(scale), facecolor=LAND, zorder=0)
    ax.add_feature(cfeature.OCEAN.with_scale(scale), facecolor=WATER, zorder=0)
    ax.add_feature(cfeature.LAKES.with_scale(scale), facecolor=WATER, edgecolor="#7aa5c7", linewidth=0.35, zorder=1)
    ax.add_feature(cfeature.RIVERS.with_scale(scale), edgecolor="#8db2cc", linewidth=0.35, zorder=2)
    if include_roads:
        try:
            roads = cfeature.NaturalEarthFeature("cultural", "roads", "10m", facecolor="none", edgecolor=ROAD)
            ax.add_feature(roads, linewidth=0.45, alpha=0.85, zorder=3)
        except Exception:
            pass
    try:
        counties = cfeature.NaturalEarthFeature("cultural", "admin_2_counties", scale, facecolor="none", edgecolor="#777777")
        ax.add_feature(counties, linewidth=0.35, zorder=4)
    except Exception:
        pass
    ax.add_feature(cfeature.COASTLINE.with_scale(scale), edgecolor="#111111", linewidth=1.0, zorder=5)
    ax.add_feature(cfeature.STATES.with_scale(scale), edgecolor=BOUNDARY, linewidth=0.8, zorder=6)
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_edgecolor("#111111"); s.set_linewidth(0.7)


def add_warning_polygon(ax, geom, color):
    geoms = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    for g in geoms:
        ax.add_geometries([g], crs=ccrs.PlateCarree(), facecolor=color, edgecolor=color, linewidth=1.2, alpha=0.98, zorder=20)


def add_city_labels(ax, extent, geom, max_count=12):
    minx, maxx, miny, maxy = extent[0], extent[1], extent[2], extent[3]
    centroid = geom.centroid; ranked = []
    for name, lat, lon in CITY_POINTS:
        if minx <= lon <= maxx and miny <= lat <= maxy:
            dist = math.hypot(lon - centroid.x, lat - centroid.y)
            ranked.append((dist, name, lat, lon))
    for _, name, lat, lon in sorted(ranked)[:max_count]:
        ax.text(lon, lat, name, transform=ccrs.PlateCarree(), fontsize=8.2, color="#6f6f6f", ha="center", va="center", zorder=30, path_effects=[pe.withStroke(linewidth=2.0, foreground=LAND)])


def draw_icon_person_water(ax, x, y, scale=1.0):
    ax.add_patch(Circle((x + 0.020*scale, y + 0.037*scale), 0.008*scale, fc=TEXT_WHITE, ec="none"))
    ax.plot([x+0.020*scale, x+0.008*scale, x+0.025*scale], [y+0.028*scale, y+0.006*scale, y+0.008*scale], color=TEXT_WHITE, lw=2.0)
    ax.plot([x+0.016*scale, x+0.040*scale], [y+0.020*scale, y+0.028*scale], color=TEXT_WHITE, lw=2.0)
    for i in range(3):
        yy = y - 0.005*scale - i*0.010*scale
        ax.plot([x, x+0.055*scale], [yy, yy], color=TEXT_WHITE, lw=1.8, alpha=0.9)


def draw_icon_car_water(ax, x, y, scale=1.0):
    ax.add_patch(Rectangle((x+0.010*scale, y+0.016*scale), 0.034*scale, 0.022*scale, fc="none", ec=TEXT_WHITE, lw=2))
    ax.add_patch(Rectangle((x+0.016*scale, y+0.038*scale), 0.022*scale, 0.010*scale, fc="none", ec=TEXT_WHITE, lw=2))
    ax.add_patch(Circle((x+0.017*scale, y+0.013*scale), 0.005*scale, fc=TEXT_WHITE, ec="none"))
    ax.add_patch(Circle((x+0.039*scale, y+0.013*scale), 0.005*scale, fc=TEXT_WHITE, ec="none"))
    for i in range(3):
        yy = y - 0.005*scale - i*0.010*scale
        ax.plot([x, x+0.055*scale], [yy, yy], color=TEXT_WHITE, lw=1.8, alpha=0.9)


def draw_info_icon(ax, x, y, scale=1.0):
    ax.add_patch(Circle((x+0.026*scale, y+0.026*scale), 0.026*scale, fc="none", ec=TEXT_WHITE, lw=2.3))
    ax.text(x+0.026*scale, y+0.020*scale, "i", ha="center", va="center", color=TEXT_WHITE, fontsize=24*scale, fontweight="bold")


def fmt_valid_until(dt):
    if not dt:
        return "Unknown"
    # Keep UTC-to-local simple for KLIX: CDT in warm season, CST otherwise. This is only display text.
    month = dt.month
    offset = -5 if 3 <= month <= 11 else -6
    local = dt.astimezone(timezone.utc).replace(tzinfo=None)
    from datetime import timedelta
    local = local + timedelta(hours=offset)
    ampm = "AM" if local.hour < 12 else "PM"
    hour = local.hour % 12 or 12
    minute = f"{local.minute:02d}"
    dow = local.strftime("%A")
    tz = "CDT" if offset == -5 else "CST"
    return f"{hour}:{minute} {ampm} {tz} {dow}\n{local:%b} {local.day}, {local.year}"


def safety_lines(event):
    if event == "Flash Flood Warning":
        return [("person_water", "Move immediately to\nhigher ground!"), ("car_water", "Avoid walking or driving\nthrough flood waters!")]
    if event == "Tornado Warning":
        return [("info", "Take shelter now!"), ("info", "Move to an interior room\non the lowest floor.")]
    if event == "Severe Thunderstorm Warning":
        return [("info", "Move indoors now."), ("info", "Stay away from windows.")]
    if event == "Special Marine Warning":
        return [("info", "Seek safe harbor now."), ("info", "Remain below deck if\ncaught on the water.")]
    return [("info", "Monitor later forecasts."), ("info", "Be prepared to act.")]


def draw_sidebar(ax, props, geom, color):
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    ax.add_patch(Rectangle((0, 0), 1, 1, fc=SIDEBAR_BG, ec="none"))
    expires = parse_time(props.get("expires") or props.get("ends"))
    ax.text(0.50, 0.945, "Valid Until", ha="center", va="center", color=TEXT_WHITE, fontsize=9)
    ax.text(0.50, 0.885, fmt_valid_until(expires), ha="center", va="center", color=TEXT_WHITE, fontsize=10.5, linespacing=1.25)

    ax.text(0.045, 0.785, "Safety Information", ha="left", va="center", color=color, fontsize=11, fontweight="bold")
    ax.plot([0.045, 0.970], [0.765, 0.765], color=color, lw=1)
    y = 0.690
    for icon, text in safety_lines(props.get("event", "")):
        if icon == "person_water": draw_icon_person_water(ax, 0.105, y-0.035, 1.0)
        elif icon == "car_water": draw_icon_car_water(ax, 0.105, y-0.035, 1.0)
        else: draw_info_icon(ax, 0.110, y-0.030, 0.75)
        ax.text(0.350, y, text, ha="left", va="center", color=TEXT_WHITE, fontsize=9.2, linespacing=1.15)
        y -= 0.125

    ax.text(0.045, 0.465, "Potential Exposure", ha="left", va="center", color=color, fontsize=11, fontweight="bold")
    ax.plot([0.045, 0.970], [0.448, 0.448], color=color, lw=1)
    draw_info_icon(ax, 0.115, 0.340, 0.78)

    # Placeholder exposure until real GIS layers are added. Better than fake precision.
    ax.text(0.355, 0.370, "Population: Pending", ha="left", va="center", color=TEXT_WHITE, fontsize=9.2)
    ax.text(0.355, 0.325, "Schools: Pending", ha="left", va="center", color=TEXT_WHITE, fontsize=9.2)
    ax.text(0.355, 0.282, "Hospitals: Pending", ha="left", va="center", color=TEXT_WHITE, fontsize=9.2)


def draw_overview(ax, geom, color):
    extent = [-92.7, -87.9, 28.7, 31.6]
    add_light_basemap(ax, extent, scale="50m", include_roads=False)
    add_warning_polygon(ax, geom, color)
    ax.text(-91.35, 30.1, "LA", fontsize=8, color="#333333", transform=ccrs.PlateCarree())
    ax.text(-89.85, 30.55, "MS", fontsize=8, color="#333333", transform=ccrs.PlateCarree())
    ax.text(-88.8, 30.55, "AL", fontsize=8, color="#333333", transform=ccrs.PlateCarree())
    ax.text(-87.95, 29.95, "FL", fontsize=8, color="#333333", transform=ccrs.PlateCarree())


def draw_main_map(ax, geom, color):
    extent = map_extent_for_geom(geom)
    add_light_basemap(ax, extent, scale="10m", include_roads=True)
    add_warning_polygon(ax, geom, color)
    add_city_labels(ax, extent, geom, max_count=13)
    # crude interstate shields/labels for visual similarity
    ax.text(0.28, 0.19, "I 10", transform=ax.transAxes, fontsize=6.5, color="white", ha="center", va="center", bbox=dict(boxstyle="circle,pad=0.14", fc="#3568b7", ec="none"), zorder=60)
    ax.text(0.34, 0.86, "I 59", transform=ax.transAxes, fontsize=6.5, color="white", ha="center", va="center", bbox=dict(boxstyle="circle,pad=0.14", fc="#3568b7", ec="none"), zorder=60)


def draw_iem_style_image(feature, output_path):
    props = feature.get("properties") or {}
    geom_json = feature.get("geometry")
    if not geom_json:
        return False
    geom = shape(geom_json)
    event = props.get("event", "Warning")
    color = event_color(event)

    fig = plt.figure(figsize=(CANVAS_W / DPI, CANVAS_H / DPI), dpi=DPI)
    fig.patch.set_facecolor(SIDEBAR_BG)

    header_ax = fig.add_axes([0.015, 0.892, 0.970, 0.108])
    header_ax.set_facecolor(color)
    header_ax.set_xticks([]); header_ax.set_yticks([])
    for s in header_ax.spines.values(): s.set_visible(False)
    header_ax.text(0.5, 0.50, event, ha="center", va="center", color=TEXT_WHITE, fontsize=28, fontweight="normal")

    sidebar_ax = fig.add_axes([0.015, 0.000, 0.320, 0.880])
    draw_sidebar(sidebar_ax, props, geom, color)

    map_ax = fig.add_axes([0.345, 0.025, 0.640, 0.855], projection=ccrs.PlateCarree())
    draw_main_map(map_ax, geom, color)

    overview_ax = fig.add_axes([0.030, 0.020, 0.290, 0.275], projection=ccrs.PlateCarree())
    draw_overview(overview_ax, geom, color)

    fig.text(0.965, 0.020, "@NWSNewOrleans", ha="right", va="bottom", fontsize=7.5, color="#333333")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=DPI, facecolor=fig.get_facecolor(), bbox_inches=None, pad_inches=0)
    plt.close(fig)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["active", "latest"], default="active")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.mode == "latest":
        features = fetch_latest_klix_warning(); mode_used = "latest"
    else:
        features = fetch_active_klix_warnings(); mode_used = "active"
        if not features:
            features = fetch_latest_klix_warning(); mode_used = "latest_fallback"

    records = []
    for feature in features:
        props = feature.get("properties") or {}
        filename = f"{safe_event_id(feature)}.png"
        out_path = OUT_DIR / filename
        if not draw_iem_style_image(feature, out_path):
            continue
        (OUT_DIR / "latest.png").write_bytes(out_path.read_bytes())
        records.append({
            "id": feature.get("id") or props.get("id") or "",
            "event": props.get("event") or "",
            "headline": props.get("headline") or "",
            "sent": props.get("sent") or "",
            "effective": props.get("effective") or "",
            "expires": props.get("expires") or "",
            "areaDesc": props.get("areaDesc") or "",
            "filename": filename,
            "raw_url": f"https://raw.githubusercontent.com/mefferso/spotter-warning-images/main/docs/iem-style-warning-images/{filename}",
            "latest_raw_url": "https://raw.githubusercontent.com/mefferso/spotter-warning-images/main/docs/iem-style-warning-images/latest.png",
        })

    latest_json = {"generated_at": datetime.now(timezone.utc).isoformat(), "mode": mode_used, "wfo": WFO, "count": len(records), "warnings": records}
    (OUT_DIR / "latest.json").write_text(json.dumps(latest_json, indent=2), encoding="utf-8")
    print(json.dumps(latest_json, indent=2))


if __name__ == "__main__":
    main()
