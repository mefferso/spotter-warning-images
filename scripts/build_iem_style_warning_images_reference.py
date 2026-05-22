#!/usr/bin/env python3
"""Reference-layer aware runner for IEM-style warning images.

This imports the existing renderer and overrides only the pieces that need
cached GIS layers: roads, place labels, and exposure counts.
"""

from __future__ import annotations

import cartopy.crs as ccrs
import cartopy.feature as cfeature
from matplotlib.patches import Rectangle

import build_iem_style_warning_images as base
from exposure import calculate_exposure
from map_layers import add_cached_roads, add_place_labels, get_place_labels

_ORIG_BASEMAP = base.add_light_basemap
_ORIG_CITY_LABELS = base.add_city_labels


def add_light_basemap(ax, extent, scale="10m", include_roads=True, include_counties=True):
    """Use cached TIGER roads when available; fall back to Natural Earth."""
    _ORIG_BASEMAP(
        ax,
        extent,
        scale=scale,
        include_roads=False,
        include_counties=include_counties,
    )

    if not include_roads:
        return

    if add_cached_roads(ax, extent, road_color=base.ROAD):
        return

    try:
        roads = cfeature.NaturalEarthFeature(
            "cultural", "roads", "10m", facecolor="none", edgecolor=base.ROAD
        )
        ax.add_feature(roads, linewidth=0.55, alpha=0.8, zorder=3)
    except Exception:
        pass


def add_city_labels(ax, extent, geom, max_count=11):
    """Use cached TIGER place points when available; fall back to hardcoded list."""
    labels = get_place_labels(extent, geom, max_count=max_count)
    if labels:
        add_place_labels(ax, labels, land_color=base.LAND)
        return
    _ORIG_CITY_LABELS(ax, extent, geom, max_count=max_count)


def draw_sidebar(ax, props, geom, color):
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.add_patch(Rectangle((0, 0), 1, 1, fc=base.SIDEBAR_BG, ec="none"))

    ax.text(
        0.50,
        0.94,
        "Valid Until",
        ha="center",
        va="center",
        color=base.TEXT_WHITE,
        fontsize=9,
    )
    ax.text(
        0.50,
        0.885,
        base.fmt_valid_until(base.parse_time(props.get("expires") or props.get("ends"))),
        ha="center",
        va="center",
        color=base.TEXT_WHITE,
        fontsize=10.5,
        linespacing=1.25,
    )

    ax.text(
        0.045,
        0.785,
        "Threat Information",
        ha="left",
        va="center",
        color=color,
        fontsize=10.8,
        fontweight="bold",
    )
    ax.plot([0.045, 0.97], [0.765, 0.765], color=color, lw=1)

    y = 0.695
    for icon, title, detail in base.threat_items(props):
        if icon == "tornado":
            base.draw_tornado(ax, 0.048, y - 0.035, 1.05)
        elif icon == "hail":
            base.draw_hail(ax, 0.058, y - 0.045, 0.90)
        else:
            base.draw_info(ax, 0.090, y - 0.035, 0.8)

        ax.text(
            0.315,
            y + 0.020,
            title,
            ha="left",
            va="center",
            color=base.TEXT_WHITE,
            fontsize=9.3,
            fontweight="bold",
        )
        ax.text(
            0.315,
            y - 0.030,
            detail,
            ha="left",
            va="center",
            color=base.TEXT_WHITE,
            fontsize=9.0,
            linespacing=1.12,
        )
        y -= 0.125

    ax.text(
        0.045,
        0.465,
        "Potential Exposure",
        ha="left",
        va="center",
        color=color,
        fontsize=10.8,
        fontweight="bold",
    )
    ax.plot([0.045, 0.97], [0.448, 0.448], color=color, lw=1)
    base.draw_info(ax, 0.115, 0.335, 0.82)

    exposure = calculate_exposure(geom)
    source_note = "" if exposure.source != "fallback_missing_reference_layers" else "*"

    ax.text(
        0.355,
        0.370,
        f"Population: {exposure.population:,}{source_note}",
        ha="left",
        va="center",
        color=base.TEXT_WHITE,
        fontsize=9.2,
    )
    ax.text(
        0.355,
        0.325,
        f"Schools: {exposure.schools}{source_note}",
        ha="left",
        va="center",
        color=base.TEXT_WHITE,
        fontsize=9.2,
    )
    ax.text(
        0.355,
        0.282,
        f"Hospitals: {exposure.hospitals}{source_note}",
        ha="left",
        va="center",
        color=base.TEXT_WHITE,
        fontsize=9.2,
    )


base.add_light_basemap = add_light_basemap
base.add_city_labels = add_city_labels
base.draw_sidebar = draw_sidebar

if __name__ == "__main__":
    base.main()
