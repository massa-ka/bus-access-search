from __future__ import annotations

from io import BytesIO
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import pandas as pd
import pydeck as pdk
import requests
import streamlit as st


st.set_page_config(page_title="バスアクセスサーチ", page_icon="🚌", layout="wide")

st.markdown("""
<style>
.block-container {padding-top: 2rem; padding-bottom: 3rem;}
div[data-testid="stMetric"] {background:#f6f8fa;border:1px solid #e6e9ed;border-radius:12px;padding:14px 18px;}
.small-note {color:#667085;font-size:.9rem;}
</style>
""", unsafe_allow_html=True)


@st.cache_data
def load_data():
    base = Path(__file__).parent
    stops = pd.read_csv(base / "stops.csv.gz", dtype={"stop_uid": str})
    routes = pd.read_csv(base / "stop_routes.csv.gz", dtype={"stop_uid": str})
    return stops, routes


def distance_km(lat1, lon1, lat2, lon2):
    lat1, lon1 = radians(lat1), radians(lon1)
    lat2 = lat2.astype(float).map(radians)
    lon2 = lon2.astype(float).map(radians)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = dlat.map(lambda x: sin(x / 2) ** 2) + lat2.map(cos) * cos(lat1) * dlon.map(lambda x: sin(x / 2) ** 2)
    return 6371.0088 * 2 * a.map(lambda x: asin(sqrt(min(1, x))))


@st.cache_data(ttl=86400, show_spinner=False)
def geocode(query):
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query, "format": "json", "limit": 5, "countrycodes": "jp"},
        headers={"User-Agent": "bus-access-search/1.0"}, timeout=15,
    )
    r.raise_for_status()
    return r.json()


def excel_bytes(stops_df, routes_df):
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        stops_df.to_excel(writer, sheet_name="停留所一覧", index=False)
        routes_df.to_excel(writer, sheet_name="路線・行先", index=False)
    return out.getvalue()


st.title("🚌 バスアクセスサーチ")
st.caption("住所または緯度・経度を起点に、周辺の停留所・系統・行先・営業所・便数を検索します。")

stops, route_data = load_data()

with st.sidebar:
    st.header("検索条件")
    mode = st.radio("検索方法", ["住所から検索", "緯度・経度から検索"])
    radius_m = st.selectbox("検索半径", [300, 500, 1000, 1500, 2000], index=1, format_func=lambda x: f"{x:,}m")
    day_label = st.selectbox("曜日", ["平日", "土曜", "休日"])
    day_col = {"平日": "weekday", "土曜": "saturday", "休日": "holiday"}[day_label]
    agency_choices = sorted(stops["agency"].dropna().unique())
    agencies = st.multiselect("バス事業者", agency_choices, default=agency_choices)

    lat = lon = None
    if mode == "住所から検索":
        address = st.text_input("住所", placeholder="例：東京都新宿区西新宿2丁目8-1")
        search = st.button("検索", type="primary", use_container_width=True)
        if search and address:
            try:
                hits = geocode(address)
                if hits:
                    st.session_state["geo_hits"] = hits
                else:
                    st.error("住所を特定できませんでした。番地まで入力するか、緯度・経度検索をお試しください。")
            except Exception:
                st.error("住所検索サービスへ接続できませんでした。時間を置いて再度お試しください。")
        hits = st.session_state.get("geo_hits", [])
        if hits:
            selected = st.selectbox("検索地点の候補", range(len(hits)), format_func=lambda i: hits[i]["display_name"])
            lat, lon = float(hits[selected]["lat"]), float(hits[selected]["lon"])
    else:
        lat = st.number_input("緯度", value=35.6895, format="%.6f")
        lon = st.number_input("経度", value=139.6917, format="%.6f")
        st.button("検索", type="primary", use_container_width=True)

if lat is None or lon is None:
    st.info("左側で住所を入力して「検索」を押してください。")
    st.stop()

candidate = stops[stops["agency"].isin(agencies)].copy()
candidate["distance_m"] = (distance_km(lat, lon, candidate["stop_lat"], candidate["stop_lon"]) * 1000).round().astype(int)
near = candidate[candidate["distance_m"] <= radius_m].sort_values(["distance_m", "agency", "stop_name"])
detail = near.merge(route_data, on=["stop_uid", "agency"], how="left")
detail = detail[detail[day_col].fillna(0) > 0].copy()

metrics = st.columns(4)
metrics[0].metric("停留所", f"{near['stop_uid'].nunique():,}")
metrics[1].metric("事業者", f"{near['agency'].nunique():,}")
metrics[2].metric("系統", f"{detail['route'].nunique():,}")
metrics[3].metric(f"{day_label}便数（停車回数）", f"{int(detail[day_col].sum()):,}")

map_stops = near.copy()
map_stops["tooltip"] = map_stops["stop_name"] + "<br>" + map_stops["agency"] + "<br>検索地点から " + map_stops["distance_m"].astype(str) + "m"
point = pd.DataFrame([{"stop_lat": lat, "stop_lon": lon}])
layers = [
    pdk.Layer("ScatterplotLayer", map_stops, get_position="[stop_lon, stop_lat]", get_radius=45, get_fill_color=[30, 115, 230, 210], pickable=True),
    pdk.Layer("ScatterplotLayer", point, get_position="[stop_lon, stop_lat]", get_radius=75, get_fill_color=[230, 35, 45, 255]),
]
st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=pdk.ViewState(latitude=lat, longitude=lon, zoom=13), tooltip={"html": "{tooltip}"}), use_container_width=True)
st.markdown('<div class="small-note">赤：検索地点　青：停留所　※距離は直線距離です。</div>', unsafe_allow_html=True)

stops_view = near[["agency", "stop_name", "distance_m"]].rename(columns={"agency":"事業者", "stop_name":"停留所", "distance_m":"距離（m）"})
routes_view = detail[["agency", "stop_name", "distance_m", "route", "headsign", "office", day_col]].rename(columns={
    "agency":"事業者", "stop_name":"停留所", "distance_m":"距離（m）", "route":"系統", "headsign":"行先", "office":"営業所", day_col:f"{day_label}便数"
}).sort_values(["距離（m）", "事業者", "停留所", "系統", "行先"])

tab1, tab2 = st.tabs(["停留所一覧", "路線・行先"])
with tab1:
    st.dataframe(stops_view, use_container_width=True, hide_index=True)
with tab2:
    st.dataframe(routes_view, use_container_width=True, hide_index=True)

st.download_button("Excelをダウンロード", excel_bytes(stops_view, routes_view), "bus_access_search.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

with st.expander("収録データについて"):
    st.write("対応事業者：都営バス、横浜市営バス、西武バス、北海道中央バス")
    st.write("便数は各GTFSの曜日設定に基づく停車回数です。臨時の運休・増便等は反映されない場合があります。")
