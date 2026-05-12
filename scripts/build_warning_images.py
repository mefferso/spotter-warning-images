#!/usr/bin/env python3

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.pyplot as plt
import requests
from shapely.geometry import shape
from shapely.ops import unary_union


OUT_DIR = Path("docs/warning-images")
WFO = "LIX"

WANTED_EVENTS = {
    "Tornado Warning": ("TO", "W"),
    "Severe Thunderstorm Warning": ("SV", "W"),
    "Flash Flood Warning": ("FF", "W"),
    "Special Marine Warning": ("MA", "W"),
    "Special Weather Statement": ("WW", "Y"),
}


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

    # Best case: api.weather.gov gives WFO in parameters.
    wfos = get_wfo_list(props)
    if WFO in wfos:
        return True

    # Backup: many alert IDs/headlines/descriptions include KLIX or NWS New Orleans.
    haystack = " ".join([
        str(feature.get("id") or ""),
        str(props.get("id") or ""),
        str(props.get("senderName") or ""),
        str(props.get("headline") or ""),
        str(props.get("description") or ""),
        str(props.get("instruction") or ""),
        str(props.get("areaDesc") or ""),
    ]).upper()

    if "KLIX" in haystack:
        return True

    if "NWS NEW ORLEANS" in haystack:
        return True

    if "NEW ORLEANS/BATON ROUGE" in haystack:
        return True

    return False


def fetch_active_klix_warnings():
    features = []
    for area in ["LA", "MS"]:
        url = f"https://api.weather.gov/alerts/active?area={area}"
        gj = fetch_json(url)
        features.extend(gj.get("features") or [])

    return dedupe([f for f in features if is_klix_alert(f)])


def fetch_latest_klix_warning():
    features = []
    for area in ["LA", "MS"]:
        url = f"https://api.weather.gov/alerts?area={area}&status=actual&message_type=alert&limit=100"
        gj = fetch_json(url)
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
        key = f.get("id") or props.get("id") or "|".join(
            [
                props.get("event", ""),
                props.get("sent", ""),
                props.get("expires", ""),
                props.get("headline", ""),
            ]
        )

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
        return "#d4555a"
    if event == "Severe Thunderstorm Warning":
        return "#f1c232"
    if event == "Flash Flood Warning":
        return "#3f7f3f"
    if event == "Special Marine Warning":
        return "#b56bd6"
    return "#d4555a"


def event_icon(event):
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

    vtec = ""
    vtecs = params.get("VTEC") or params.get("vtec") or []
    if vtecs:
        vtec = vtecs[0]

    # Example: /O.NEW.KLIX.TO.W.0012.260511T2320Z-260512T0200Z/
    m = re.search(r"\.K?([A-Z]{3})\.([A-Z]{2})\.([A-Z])\.(\d{4})\.", vtec)
    if m:
        wfo, phen, sig, etn = m.groups()
        return f"K{wfo}-{phen}-{sig}-{etn}"

    raw = feature.get("id") or props.get("id") or props.get("headline") or "warning"
    raw = re.sub(r"[^A-Za-z0-9_-]+", "-", raw)
    return raw[-80:]


def fmt_time(value):
    dt = parse_time(value)
    if dt.year < 2000:
        return "N/A"
    return dt.strftime("%a %b %-d, %Y %-I:%M %p UTC")


def draw_warning_image(feature, output_path):
    props = feature.get("properties") or {}
    geom_json = feature.get("geometry")

    if not geom_json:
        return False

    geom = shape(geom_json)
    event = props.get("event", "Warning")
    headline = props.get("headline") or event
    area = props.get("areaDesc") or "N/A"
    expires = fmt_time(props.get("expires", ""))

    color = event_color(event)

    minx, miny, maxx, maxy = geom.bounds
    dx = max(maxx - minx, 0.25)
    dy = max(maxy - miny, 0.25)

    pad_x = dx * 0.65
    pad_y = dy * 0.65

    extent = [
        minx - pad_x,
        maxx + pad_x,
        miny - pad_y,
        maxy + pad_y,
    ]

    fig = plt.figure(figsize=(10, 7.5), dpi=160)
    fig.patch.set_facecolor("#ffffff")

    gs = fig.add_gridspec(
        2,
        2,
        height_ratios=[0.13, 0.87],
        width_ratios=[0.34, 0.66],
        hspace=0,
        wspace=0,
    )

    ax_header = fig.add_subplot(gs[0, :])
    ax_side = fig.add_subplot(gs[1, 0])
    ax_map = fig.add_subplot(gs[1, 1], projection=ccrs.PlateCarree())

    ax_header.set_facecolor(color)
    ax_header.text(
        0.5,
        0.5,
        event,
        ha="center",
        va="center",
        fontsize=27,
        color="white",
        fontweight="bold",
    )
    ax_header.set_xticks([])
    ax_header.set_yticks([])
    for s in ax_header.spines.values():
        s.set_visible(False)

    ax_side.set_facecolor("#26343b")
    ax_side.set_xlim(0, 1)
    ax_side.set_ylim(0, 1)
    ax_side.set_xticks([])
    ax_side.set_yticks([])
    for s in ax_side.spines.values():
        s.set_visible(False)

    ax_side.text(
        0.5,
        0.93,
        f"Valid Until\n{expires}",
        ha="center",
        va="top",
        fontsize=10,
        color="#e2edf2",
    )

    ax_side.text(
        0.08,
        0.78,
        "Threat Information",
        ha="left",
        va="center",
        fontsize=11,
        color="#ff7378",
        fontweight="bold",
    )
    ax_side.plot([0.08, 0.92], [0.75, 0.75], color="#ff7378", lw=1)

    ax_side.text(
        0.12,
        0.66,
        event_icon(event),
        ha="left",
        va="center",
        fontsize=17,
        color="white",
        fontweight="bold",
    )

    detail_lines = []

    tornado_detection = first_param(props, "tornadoDetection")
    tornado_damage = first_param(props, "tornadoDamageThreat")
    storm_damage = first_param(props, "thunderstormDamageThreat")
    flash_flood_damage = first_param(props, "flashFloodDamageThreat")
    hail = first_param(props, "maxHailSize")
    wind = first_param(props, "maxWindGust")

    if tornado_detection:
        detail_lines.append(f"Tornado: {tornado_detection}")
    if tornado_damage:
        detail_lines.append(f"Damage: {tornado_damage}")
    if storm_damage:
        detail_lines.append(f"Damage: {storm_damage}")
    if flash_flood_damage:
        detail_lines.append(f"Flood Threat: {flash_flood_damage}")
    if wind:
        detail_lines.append(f"Wind: {wind} mph")
    if hail:
        detail_lines.append(f"Hail: {hail} in")

    if not detail_lines:
        detail_lines.append("See official warning text")

    y = 0.58
    for line in detail_lines[:6]:
        ax_side.text(
            0.12,
            y,
            line,
            ha="left",
            va="center",
            fontsize=10,
            color="#dce7ec",
        )
        y -= 0.055

    ax_side.text(
        0.08,
        0.28,
        "Area",
        ha="left",
        va="center",
        fontsize=11,
        color="#ff7378",
        fontweight="bold",
    )
    ax_side.plot([0.08, 0.92], [0.25, 0.25], color="#ff7378", lw=1)

    wrapped_area = area[:130] + ("..." if len(area) > 130 else "")
    ax_side.text(
        0.08,
        0.20,
        wrapped_area,
        ha="left",
        va="top",
        fontsize=9,
        color="#e2edf2",
        wrap=True,
    )

    ax_map.set_extent(extent, crs=ccrs.PlateCarree())
    ax_map.set_facecolor("#f7f3df")

    ax_map.add_feature(cfeature.LAND.with_scale("10m"), facecolor="#f7f3df")
    ax_map.add_feature(cfeature.OCEAN.with_scale("10m"), facecolor="#cfe8f3")
    ax_map.add_feature(cfeature.LAKES.with_scale("10m"), facecolor="#cfe8f3", edgecolor="#9ab6c5", linewidth=0.4)
    ax_map.add_feature(cfeature.RIVERS.with_scale("10m"), edgecolor="#9ab6c5", linewidth=0.5)
    ax_map.add_feature(cfeature.COASTLINE.with_scale("10m"), edgecolor="#777777", linewidth=0.7)
    ax_map.add_feature(cfeature.BORDERS.with_scale("10m"), edgecolor="#777777", linewidth=0.7)
    ax_map.add_feature(cfeature.STATES.with_scale("10m"), edgecolor="#777777", linewidth=0.8)

    geoms = [geom]
    if geom.geom_type == "MultiPolygon":
      geoms = list(geom.geoms)

    for g in geoms:
        ax_map.add_geometries(
            [g],
            crs=ccrs.PlateCarree(),
            facecolor=color,
            edgecolor="#7a1d22",
            linewidth=1.3,
            alpha=0.82,
        )

    ax_map.text(
        0.98,
        0.02,
        "NWS New Orleans/Baton Rouge",
        transform=ax_map.transAxes,
        ha="right",
        va="bottom",
        fontsize=7,
        color="#24415a",
        bbox=dict(facecolor="white", alpha=0.65, edgecolor="none", pad=2),
    )

    ax_map.set_xticks([])
    ax_map.set_yticks([])

    fig.text(
        0.5,
        0.015,
        headline,
        ha="center",
        va="bottom",
        fontsize=8,
        color="#333333",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0.08)
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

        # Important: manual testing fallback
        if not features:
            features = fetch_latest_klix_warning()
            mode_used = "latest_fallback"

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

        records.append(
            {
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
            }
        )

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
