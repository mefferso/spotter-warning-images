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
import matplotlib.patheffects as pe
import requests
from matplotlib.patches import Circle, Polygon as MplPolygon, Rectangle
from shapely.geometry import Point, shape

OUT_DIR = Path("docs/iem-style-warning-images")
WFO = "LIX"
CANVAS_W = 700
CANVAS_H = 560
DPI = 100
SIDEBAR_BG = "#24323a"
TEXT_WHITE = "#f6f6f6"
LAND = "#f4f0df"
WATER = "#a9cff0"
ROAD = "#c89433"
BOUNDARY = "#555555"

WANTED_EVENTS = {"Tornado Warning", "Severe Thunderstorm Warning", "Flash Flood Warning", "Special Marine Warning", "Special Weather Statement"}
CITY_POINTS = [("New Orleans",29.9511,-90.0715),("Baton Rouge",30.4515,-91.1871),("Slidell",30.2752,-89.7812),("Mandeville",30.3583,-90.0656),("Covington",30.4755,-90.1009),("Hammond",30.5044,-90.4612),("Ponchatoula",30.4388,-90.4415),("Bogalusa",30.7910,-89.8487),("Picayune",30.5255,-89.6795),("Bay St. Louis",30.3088,-89.3300),("Gulfport",30.3674,-89.0928),("Biloxi",30.3960,-88.8853),("Pascagoula",30.3658,-88.5561),("McComb",31.2446,-90.4532),("Liberty",31.1588,-90.8129),("Kentwood",30.9382,-90.5087),("Amite",30.7266,-90.5087),("Franklinton",30.8471,-90.1531),("Laplace",30.0666,-90.4801),("Reserve",30.0535,-90.5518),("Lutcher",30.0405,-90.6984),("Paulina",30.0260,-90.7148),("Vacherie",29.9671,-90.7054),("Donaldsonville",30.1010,-90.9929),("Gonzales",30.2385,-90.9201),("Thibodaux",29.7958,-90.8229),("Houma",29.5958,-90.7195),("Galliano",29.4422,-90.2992),("Grand Isle",29.2366,-89.9873),("Port Sulphur",29.4805,-89.6939),("Kiln",30.4096,-89.4359),("Pearlington",30.2466,-89.6117),("Lakeshore",30.2430,-89.4384),("DeLisle",30.3796,-89.2645),("McNeill",30.6677,-89.6367),("Carriere",30.6169,-89.6526),("Ozona",30.6288,-89.6437),("Nicholson",30.4771,-89.6939),("Crosby",31.2752,-91.0618),("Rosetta",31.2160,-90.9757),("Gloster",31.1977,-91.0215),("White Apple",31.3724,-91.0515),("Meadville",31.4727,-90.8968),("Bude",31.4624,-90.8504)]

def fetch_json(url):
    r=requests.get(url,headers={"User-Agent":"KLIX IEM-style Warning Image Builder (mefferso@noaa.gov)","Accept":"application/geo+json"},timeout=30); r.raise_for_status(); return r.json()
def parse_time(v):
    try: return datetime.fromisoformat(v.replace("Z","+00:00")) if v else None
    except Exception: return None
def get_wfo_list(props):
    params=props.get("parameters") or {}; return [str(w).upper().replace("K","") for w in (params.get("WFO") or params.get("wfo") or [])]
def is_klix_alert(feature):
    props=feature.get("properties") or {}; event=props.get("event","").strip()
    if event not in WANTED_EVENTS: return False
    if WFO in get_wfo_list(props): return True
    haystack=" ".join(str(x or "") for x in [feature.get("id"),props.get("id"),props.get("senderName"),props.get("headline"),props.get("description"),props.get("areaDesc")]).upper()
    return any(s in haystack for s in ["KLIX","NWS NEW ORLEANS","NEW ORLEANS/BATON ROUGE"])
def dedupe(features):
    seen=set(); out=[]
    for f in features:
        p=f.get("properties") or {}; key=f.get("id") or p.get("id") or "|".join([p.get("event",""),p.get("sent",""),p.get("expires",""),p.get("headline","")])
        if key not in seen: seen.add(key); out.append(f)
    return out
def fetch_active_klix_warnings():
    features=[]
    for area in ["LA","MS"]: features.extend(fetch_json(f"https://api.weather.gov/alerts/active?area={area}").get("features") or [])
    return dedupe([f for f in features if is_klix_alert(f)])
def fetch_latest_klix_warning():
    features=[]
    for area in ["LA","MS"]: features.extend(fetch_json(f"https://api.weather.gov/alerts?area={area}&status=actual&message_type=alert&limit=100").get("features") or [])
    matches=dedupe([f for f in features if is_klix_alert(f)]); matches.sort(key=lambda f: parse_time((f.get("properties") or {}).get("sent") or "") or datetime.fromtimestamp(0,tz=timezone.utc), reverse=True); return matches[:1]
def event_color(event): return {"Tornado Warning":"#c95559","Severe Thunderstorm Warning":"#d0ad39","Flash Flood Warning":"#c95559","Special Marine Warning":"#9a70bd","Special Weather Statement":"#c95559"}.get(event,"#c95559")
def safe_event_id(feature):
    p=feature.get("properties") or {}; params=p.get("parameters") or {}; v=(params.get("VTEC") or params.get("vtec") or [""])[0]
    m=re.search(r"\.K?([A-Z]{3})\.([A-Z]{2})\.([A-Z])\.(\d{4})\.",v)
    if m:
        wfo,phen,sig,etn=m.groups(); return f"K{wfo}-{phen}-{sig}-{etn}"
    return re.sub(r"[^A-Za-z0-9_-]+","-",feature.get("id") or p.get("id") or p.get("headline") or "warning")[-80:]
def map_extent_for_geom(geom):
    minx,miny,maxx,maxy=geom.bounds; dx=max(maxx-minx,0.65); dy=max(maxy-miny,0.55)
    return [minx-dx*.38,maxx+dx*.38,miny-dy*.95,maxy+dy*.52]
def add_light_basemap(ax, extent, scale="10m", include_roads=True, include_counties=True):
    ax.set_extent(extent,crs=ccrs.PlateCarree()); ax.set_facecolor(LAND)
    ax.add_feature(cfeature.LAND.with_scale(scale),facecolor=LAND,zorder=0); ax.add_feature(cfeature.OCEAN.with_scale(scale),facecolor=WATER,zorder=0); ax.add_feature(cfeature.LAKES.with_scale(scale),facecolor=WATER,edgecolor="#7aa5c7",linewidth=.35,zorder=1); ax.add_feature(cfeature.RIVERS.with_scale(scale),edgecolor="#8db2cc",linewidth=.35,zorder=2)
    if include_roads:
        try: ax.add_feature(cfeature.NaturalEarthFeature("cultural","roads","10m",facecolor="none",edgecolor=ROAD),linewidth=.55,alpha=.8,zorder=3)
        except Exception: pass
    if include_counties:
        try: ax.add_feature(cfeature.NaturalEarthFeature("cultural","admin_2_counties","10m",facecolor="none",edgecolor="#6e6e6e"),linewidth=.45,zorder=4)
        except Exception: pass
    ax.add_feature(cfeature.COASTLINE.with_scale(scale),edgecolor="#111111",linewidth=.9,zorder=5); ax.add_feature(cfeature.STATES.with_scale(scale),edgecolor=BOUNDARY,linewidth=.8,zorder=6)
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values(): s.set_edgecolor("#111111"); s.set_linewidth(.7)
def add_warning_polygon(ax, geom, color):
    for g in (list(geom.geoms) if geom.geom_type=="MultiPolygon" else [geom]): ax.add_geometries([g],crs=ccrs.PlateCarree(),facecolor=color,edgecolor=color,linewidth=1.0,alpha=.97,zorder=20)
def add_city_labels(ax, extent, geom, max_count=9):
    minx,maxx,miny,maxy=extent[0],extent[1],extent[2],extent[3]; c=geom.centroid; ranked=[]
    for name,lat,lon in CITY_POINTS:
        if minx<=lon<=maxx and miny<=lat<=maxy: ranked.append((math.hypot(lon-c.x,lat-c.y),name,lat,lon))
    for _,name,lat,lon in sorted(ranked)[:max_count]:
        ax.plot(lon,lat,marker=".",color="#222222",markersize=2.5,transform=ccrs.PlateCarree(),zorder=31)
        ax.text(lon,lat+.025,name,transform=ccrs.PlateCarree(),fontsize=7.5,color="#7b7b7b",ha="center",va="bottom",zorder=32,path_effects=[pe.withStroke(linewidth=1.4,foreground=LAND)])
def fmt_valid_until(dt):
    if not dt: return "Unknown"
    from datetime import timedelta
    off=-5 if 3<=dt.month<=11 else -6; local=dt.astimezone(timezone.utc).replace(tzinfo=None)+timedelta(hours=off); return f"{local.hour%12 or 12}:{local.minute:02d} {'AM' if local.hour<12 else 'PM'} {'CDT' if off==-5 else 'CST'} {local:%A}\n{local:%b} {local.day}, {local.year}"
def params(props): return props.get("parameters") or {}
def first_param(props, key):
    v=params(props).get(key) or params(props).get(key.lower()) or []
    return str(v[0]) if isinstance(v,list) and v else str(v) if v else ""
def threat_items(props):
    event=props.get("event",""); desc=(props.get("description") or "").upper(); headline=(props.get("headline") or "").upper(); combined=desc+" "+headline+" "+json.dumps(params(props)).upper()
    if event=="Tornado Warning":
        tor="Radar Indicated"
        if "OBSERVED" in combined or "CONFIRMED" in combined: tor="Observed"
        hail=""
        m=re.search(r"MAX HAIL SIZE\.\.\.([0-9.]+) IN",combined)
        if m: hail=f"{m.group(1)} inch possible"
        elif "HAIL" in combined: hail="Pea\nSized Possible"
        else: hail="Pea\nSized Possible"
        return [("tornado","TORNADO",tor),("hail","HAIL",hail)]
    if event=="Flash Flood Warning": return [("flood","FLASH FLOOD","Radar/Gauge Indicated"),("flood","IMPACTS","Flooding of roads and low-lying areas")]
    if event=="Severe Thunderstorm Warning": return [("wind","WIND","60 mph gusts possible"),("hail","HAIL","Up to quarter size possible")]
    return [("info",event.upper().replace(" WARNING",""),"See warning text for details")]
def draw_info(ax,x,y,s=.78): ax.add_patch(Circle((x+.026*s,y+.026*s),.026*s,fc="none",ec=TEXT_WHITE,lw=2.1)); ax.text(x+.026*s,y+.020*s,"i",ha="center",va="center",color=TEXT_WHITE,fontsize=22*s,fontweight="bold")
def draw_tornado(ax,x,y,s=1):
    pts=[(x+.00*s,y+.055*s),(x+.085*s,y+.055*s),(x+.064*s,y+.044*s),(x+.044*s,y+.034*s),(x+.030*s,y+.022*s),(x+.021*s,y+.010*s),(x+.012*s,y+.000*s)]
    ax.add_patch(MplPolygon(pts,closed=False,fill=False,edgecolor=TEXT_WHITE,linewidth=3.0,joinstyle="round",capstyle="round"))
def draw_hail(ax,x,y,s=1):
    ax.add_patch(Circle((x+.025*s,y+.038*s),.017*s,fc=TEXT_WHITE,ec="none")); ax.plot([x+.07*s,x+.035*s],[y+.07*s,y+.017*s],color=TEXT_WHITE,lw=1.0); ax.plot([x+.035*s,x+.050*s],[y+.017*s,y-.005*s],color=TEXT_WHITE,lw=1.0)
def draw_sidebar(ax, props, geom, color):
    ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis("off"); ax.add_patch(Rectangle((0,0),1,1,fc=SIDEBAR_BG,ec="none"))
    ax.text(.50,.94,"Valid Until",ha="center",va="center",color=TEXT_WHITE,fontsize=9); ax.text(.50,.885,fmt_valid_until(parse_time(props.get("expires") or props.get("ends"))),ha="center",va="center",color=TEXT_WHITE,fontsize=10.5,linespacing=1.25)
    ax.text(.045,.785,"Threat Information",ha="left",va="center",color=color,fontsize=10.8,fontweight="bold"); ax.plot([.045,.97],[.765,.765],color=color,lw=1)
    y=.695
    for icon,title,detail in threat_items(props):
        if icon=="tornado": draw_tornado(ax,.075,y-.035,.95)
        elif icon=="hail": draw_hail(ax,.085,y-.040,.95)
        else: draw_info(ax,.090,y-.035,.8)
        ax.text(.315,y+.020,title,ha="left",va="center",color=TEXT_WHITE,fontsize=9.3,fontweight="bold"); ax.text(.315,y-.030,detail,ha="left",va="center",color=TEXT_WHITE,fontsize=9.0,linespacing=1.12); y-=.125
    ax.text(.045,.465,"Potential Exposure",ha="left",va="center",color=color,fontsize=10.8,fontweight="bold"); ax.plot([.045,.97],[.448,.448],color=color,lw=1); draw_info(ax,.115,.335,.82)
    pop=max(1,int(round(geom.area*90000)))
    schools=sum(1 for _,lat,lon in CITY_POINTS if geom.contains(Point(lon,lat)) and pop>5000)//6
    ax.text(.355,.370,f"Population: {pop:,}",ha="left",va="center",color=TEXT_WHITE,fontsize=9.2); ax.text(.355,.325,f"Schools: {schools}",ha="left",va="center",color=TEXT_WHITE,fontsize=9.2); ax.text(.355,.282,"Hospitals: 0",ha="left",va="center",color=TEXT_WHITE,fontsize=9.2)
def draw_overview(ax, geom, color):
    extent=[-92.7,-87.9,28.7,31.6]; add_light_basemap(ax,extent,scale="50m",include_roads=False,include_counties=False); add_warning_polygon(ax,geom,color)
    for label,x,y in [("LA",-91.35,30.1),("MS",-89.85,30.55),("AL",-88.8,30.55),("FL",-87.95,29.95)]: ax.text(x,y,label,fontsize=8,color="#333333",transform=ccrs.PlateCarree())
def draw_main_map(ax, geom, color):
    extent=map_extent_for_geom(geom); add_light_basemap(ax,extent,scale="10m",include_roads=True,include_counties=True); add_warning_polygon(ax,geom,color); add_city_labels(ax,extent,geom,max_count=11)
    ax.text(.36,.88,"I 59",transform=ax.transAxes,fontsize=6.5,color="white",ha="center",va="center",bbox=dict(boxstyle="circle,pad=0.13",fc="#3568b7",ec="none"),zorder=60)
def draw_iem_style_image(feature, output_path):
    props=feature.get("properties") or {}; geom_json=feature.get("geometry")
    if not geom_json: return False
    geom=shape(geom_json); event=props.get("event","Warning"); color=event_color(event)
    fig=plt.figure(figsize=(CANVAS_W/DPI,CANVAS_H/DPI),dpi=DPI); fig.patch.set_facecolor(SIDEBAR_BG)
    h=fig.add_axes([.015,.895,.970,.105]); h.set_facecolor(color); h.set_xticks([]); h.set_yticks([])
    for s in h.spines.values(): s.set_visible(False)
    h.text(.5,.50,event,ha="center",va="center",color=TEXT_WHITE,fontsize=30,fontweight="normal")
    side=fig.add_axes([.015,.000,.320,.875]); draw_sidebar(side,props,geom,color)
    main=fig.add_axes([.345,.000,.640,.875],projection=ccrs.PlateCarree()); draw_main_map(main,geom,color)
    inset=fig.add_axes([.015,.020,.320,.255],projection=ccrs.PlateCarree()); draw_overview(inset,geom,color)
    fig.text(.965,.008,"@NWSNewOrleans",ha="right",va="bottom",fontsize=7.5,color="#333333")
    output_path.parent.mkdir(parents=True,exist_ok=True); fig.savefig(output_path,dpi=DPI,facecolor=fig.get_facecolor(),bbox_inches=None,pad_inches=0); plt.close(fig); return True
def main():
    p=argparse.ArgumentParser(); p.add_argument("--mode",choices=["active","latest"],default="active"); args=p.parse_args(); OUT_DIR.mkdir(parents=True,exist_ok=True)
    if args.mode=="latest": features=fetch_latest_klix_warning(); mode_used="latest"
    else:
        features=fetch_active_klix_warnings(); mode_used="active"
        if not features: features=fetch_latest_klix_warning(); mode_used="latest_fallback"
    records=[]
    for feature in features:
        props=feature.get("properties") or {}; filename=f"{safe_event_id(feature)}.png"; out_path=OUT_DIR/filename
        if not draw_iem_style_image(feature,out_path): continue
        (OUT_DIR/"latest.png").write_bytes(out_path.read_bytes())
        records.append({"id":feature.get("id") or props.get("id") or "","event":props.get("event") or "","headline":props.get("headline") or "","sent":props.get("sent") or "","effective":props.get("effective") or "","expires":props.get("expires") or "","areaDesc":props.get("areaDesc") or "","filename":filename,"raw_url":f"https://raw.githubusercontent.com/mefferso/spotter-warning-images/main/docs/iem-style-warning-images/{filename}","latest_raw_url":"https://raw.githubusercontent.com/mefferso/spotter-warning-images/main/docs/iem-style-warning-images/latest.png"})
    latest_json={"generated_at":datetime.now(timezone.utc).isoformat(),"mode":mode_used,"wfo":WFO,"count":len(records),"warnings":records}
    (OUT_DIR/"latest.json").write_text(json.dumps(latest_json,indent=2),encoding="utf-8"); print(json.dumps(latest_json,indent=2))
if __name__=="__main__": main()
