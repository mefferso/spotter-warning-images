#!/usr/bin/env python3

import argparse
import json
import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import requests
from shapely.geometry import shape

OUT_DIR = Path("docs/warning-images")
WFO = "LIX"
LOCAL_TZ = ZoneInfo("America/Chicago")

WANTED_EVENTS = {
    "Tornado Warning": ("TO", "W"),
    "Severe Thunderstorm Warning": ("SV", "W"),
    "Flash Flood Warning": ("FF", "W"),
    "Special Marine Warning": ("MA", "W"),
    "Special Weather Statement": ("WW", "Y"),
}

# Hand-picked LIX-area labels. This keeps the graphic useful without needing an
# external map-tile service. Add/remove places here as needed.
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


def fmt_time_local(value):
    dt = parse_time(value)
    if dt.year < 2000:
        return "N/A"
    local = dt.astimezone(LOCAL_TZ)
    return local.strftime("%-I:%M %p %Z\n%A\n%b %-d, %Y")


def first_param(props, key):
    params = props.get("parameters") or {}
    value = params.get(key) or params.get(key.lower()) or []
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return ""


def event_color(event):
    if event == "Tornado Warning":
        return "#d05a5f"
    if event == "Severe Thunderstorm Warning":
        return "#d9b23f"
    if event == "Flash Flood Warning":
        return "#3f7f3f"
    if event == "Special Marine Warning":
        return "#8a5bb8"
    return "#d05a5f"


def event_main_threat(event):
    if event == "Tornado Warning":
        return "TORNADO"
    if event == "Severe Thunderstorm Warning":
        return "SEVERE STORM"
    if event == "Flash Flood Warning":
        return "FLASH FLOOD"
    if event == "Special Marine Warning":
        return "MARINE WARNING"
    return "WARNING"


def safe_event_id(feature):
    props = feature.get("properties") or {}
    params = props.get("parameters") or {}
    vtecs = params.get("VTEC") or params.get("vtec") or []
    vtec = vtecs[0] if vtecs else ""

    # Example: /O.NEW.KLIX.TO.W.0012.260511T2320Z-260512T0200Z/
    m = re.search(r"\.K?([A-Z]{3})\.([A-Z]{2})\.([A-Z])\.(\d{4})\.", vtec)
    if m:
        wfo, phen, sig, etn = m.groups()
        return f"K{wfo}-{phen}-{sig}-{etn}"

    raw = feature.get("id") or props.get("id") or props.get("headline") or "warning"
    raw = re.sub(r"[^A-Za-z0-9_-]+", "-", raw)
    return raw[-80:]


def wrapped(text, width=34, max_lines=4):
    if not text:
        return "N/A"
    lines = textwrap.wrap(str(text), width=width)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,;:") + "..."
    return "\n".join(lines)


def map_extent_for_geom(geom):
    minx, miny, maxx, maxy = geom.bounds
    dx = max(maxx - minx, 0.18)
    dy = max(maxy - miny, 0.18)

    # Make extent closer to the official social card: not too much padding, but
    # enough room for nearby town labels.
    pad_x = max(dx * 0.55, 0.12)
    pad_y = max(dy * 0.55, 0.10)
    return [minx - pad_x, maxx + pad_x, miny - pad_y, maxy + pad_y]


def add_base_map(ax, extent, detail_scale="10m"):
    ax.set_extent(extent, crs=ccrs.PlateCarree())
    ax.set_facecolor("#f4f0dc")

    ax.add_feature(cfeature.LAND.with_scale(detail_scale), facecolor="#f4f0dc", zorder=0)
    ax.add_feature(cfeature.OCEAN.with_scale(detail_scale), facecolor="#cde8f5", zorder=0)
    ax.add_feature(cfeature.LAKES.with_scale(detail_scale), facecolor="#cde8f5", edgecolor="#9ab6c5", linewidth=0.4, zorder=1)
    ax.add_feature(cfeature.RIVERS.with_scale(detail_scale), edgecolor="#9ab6c5", linewidth=0.5, zorder=2)
    ax.add_feature(cfeature.COASTLINE.with_scale(detail_scale), edgecolor="#555555", linewidth=0.7, zorder=3)
    ax.add_feature(cfeature.STATES.with_scale(detail_scale), edgecolor="#555555", linewidth=0.8, zorder=3)

    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_edgecolor("#202c33")
        spine.set_linewidth(1.2)


def add_warning_polygon(ax, geom, color):
    geoms = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    for g in geoms:
        ax.add_geometries(
            [g],
            crs=ccrs.PlateCarree(),
            facecolor=color,
            edgecolor="#7a1d22",
            linewidth=1.8,
            alpha=0.84,
            zorder=10,
        )


def add_city_labels(ax, extent, geom):
    minx, maxx, miny, maxy = extent[0], extent[1], extent[2], extent[3]
    label_count = 0

    # Labels near the warning polygon get first priority.
    ranked = []
    centroid = geom.centroid
    for name, lat, lon in CITY_POINTS:
        if minx <= lon <= maxx and miny <= lat <= maxy:
            dist = ((lon - centroid.x) ** 2 + (lat - centroid.y) ** 2) ** 0.5
            ranked.append((dist, name, lat, lon))

    for _, name, lat, lon in sorted(ranked)[:10]:
        ax.text(
            lon,
            lat,
            name,
            transform=ccrs.PlateCarree(),
            fontsize=9.5,
            fontweight="bold",
            color="#222222",
            ha="center",
            va="center",
            zorder=20,
            path_effects=[pe.withStroke(linewidth=2.4, foreground="white")],
        )
        label_count += 1

    return label_count


def build_detail_lines(props, event):
    lines = []

    tornado_detection = first_param(props, "tornadoDetection")
    tornado_damage = first_param(props, "tornadoDamageThreat")
    storm_damage = first_param(props, "thunderstormDamageThreat")
    flash_flood_damage = first_param(props, "flashFloodDamageThreat")
    hail = first_param(props, "maxHailSize")
    wind = first_param(props, "maxWindGust")

    if event == "Tornado Warning":
        lines.append(("TORNADO", tornado_detection or tornado_damage or "Radar Indicated"))
        if hail:
            lines.append(("HAIL", f"{hail} in possible"))
    elif event == "Severe Thunderstorm Warning":
        lines.append(("WIND", f"{wind} mph possible" if wind else storm_damage or "Damaging wind possible"))
        if hail:
            lines.append(("HAIL", f"{hail} in possible"))
    elif event == "Flash Flood Warning":
        lines.append(("FLASH FLOOD", flash_flood_damage or "Life-threatening flooding possible"))
    elif event == "Special Marine Warning":
        lines.append(("MARINE", wind or "Hazardous marine conditions"))
    else:
        lines.append((event_main_threat(event), "See official warning text"))

    return lines[:3]


def draw_sidebar(ax, props, event, color):
    ax.set_facecolor("#202c33")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)

    ax.text(
        0.5,
        0.94,
        f"Valid Until\n{fmt_time_local(props.get('expires', ''))}",
        ha="center",
        va="top",
        fontsize=10.5,
        color="#dce7ec",
        linespacing=1.15,
    )

    ax.text(0.07, 0.78, "Threat Information", color="#e36a6f", fontsize=12, fontweight="bold", ha="left")
    ax.plot([0.07, 0.93], [0.755, 0.755], color="#b94b50", lw=1.3)

    y = 0.68
    for label, value in build_detail_lines(props, event):
        ax.text(0.15, y, label, ha="left", va="center", fontsize=12, color="white", fontweight="bold")
        ax.text(0.15, y - 0.045, value, ha="left", va="center", fontsize=9.5, color="#dce7ec")
        # simple official-ish glyph placeholder
        ax.scatter([0.09], [y], s=120, facecolors="white", edgecolors="#dce7ec", linewidths=1.0)
        y -= 0.135

    ax.text(0.07, 0.37, "Potential Exposure", color="#e36a6f", fontsize=12, fontweight="bold", ha="left")
    ax.plot([0.07, 0.93], [0.345, 0.345], color="#b94b50", lw=1.3)

    pop = first_param(props, "population") or "N/A"
    schools = first_param(props, "schools") or "N/A"
    hospitals = first_param(props, "hospitals") or "N/A"
    ax.text(0.15, 0.285, f"Population: {pop}", ha="left", fontsize=9.6, color="#dce7ec")
    ax.text(0.15, 0.245, f"Schools: {schools}", ha="left", fontsize=9.6, color="#dce7ec")
    ax.text(0.15, 0.205, f"Hospitals: {hospitals}", ha="left", fontsize=9.6, color="#dce7ec")
    ax.scatter([0.09], [0.265], s=210, facecolors="none", edgecolors="white", linewidths=2)
    ax.text(0.09, 0.265, "i", ha="center", va="center", fontsize=14, color="white", fontweight="bold")

    area = props.get("areaDesc") or ""
    ax.text(0.07, 0.115, "Area", color="#e36a6f", fontsize=10.5, fontweight="bold", ha="left")
    ax.text(0.07, 0.085, wrapped(area, width=34, max_lines=3), ha="left", va="top", fontsize=8.5, color="#dce7ec")


def draw_warning_image(feature, output_path):
    props = feature.get("properties") or {}
    geom_json = feature.get("geometry")
    if not geom_json:
        return False

    geom = shape(geom_json)
    event = props.get("event", "Warning")
    headline = props.get("headline") or event
    color = event_color(event)
    extent = map_extent_for_geom(geom)

    # 4:3 card close to the official NWS social graphic proportions.
    fig = plt.figure(figsize=(10.0, 7.5), dpi=160)
    fig.patch.set_facecolor("#202c33")

    # Manual axes placement avoids the big whitespace/bbox weirdness.
    ax_header = fig.add_axes([0.025, 0.855, 0.95, 0.115])
    ax_side = fig.add_axes([0.025, 0.055, 0.275, 0.800])
    ax_map = fig.add_axes([0.300, 0.055, 0.675, 0.800], projection=ccrs.PlateCarree())
    ax_inset = fig.add_axes([0.040, 0.070, 0.230, 0.135], projection=ccrs.PlateCarree())

    # Header
    ax_header.set_facecolor(color)
    ax_header.text(
        0.5,
        0.5,
        event,
        ha="center",
        va="center",
        fontsize=28,
        color="white",
        fontweight="bold",
        family="DejaVu Sans",
        alpha=0.98,
    )
    ax_header.set_xticks([])
    ax_header.set_yticks([])
    for s in ax_header.spines.values():
        s.set_visible(False)

    draw_sidebar(ax_side, props, event, color)

    # Main map
    add_base_map(ax_map, extent)
    add_warning_polygon(ax_map, geom, color)
    add_city_labels(ax_map, extent, geom)
    ax_map.text(
        0.985,
        0.018,
        "NWS New Orleans/Baton Rouge",
        transform=ax_map.transAxes,
        ha="right",
        va="bottom",
        fontsize=7.5,
        color="#24415a",
        bbox=dict(facecolor="white", alpha=0.72, edgecolor="none", pad=2),
        zorder=50,
    )

    # Inset regional map
    inset_extent = [-92.7, -88.2, 28.5, 31.8]
    add_base_map(ax_inset, inset_extent, detail_scale="50m")
    add_warning_polygon(ax_inset, geom, color)
    ax_inset.text(-91.8, 30.05, "LA", transform=ccrs.PlateCarree(), fontsize=7, color="#333")
    ax_inset.text(-89.9, 30.55, "MS", transform=ccrs.PlateCarree(), fontsize=7, color="#333")

    # Thin bottom headline, like a discreet caption instead of giant text.
    fig.text(
        0.975,
        0.018,
        wrapped(headline, width=95, max_lines=1),
        ha="right",
        va="bottom",
        fontsize=7.5,
        color="#dce7ec",
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
