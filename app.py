import io
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd
import pydeck as pdk
import streamlit as st


ROOT = Path(__file__).resolve().parent
DATA = ROOT
DAY_LABELS = {
    "平日（月～金） / Weekday": "weekday",
    "土曜 / Saturday": "saturday",
    "休日（日曜・祝日） / Holiday": "holiday",
}
DAY_NAMES = {"weekday": "平日", "saturday": "土曜", "holiday": "休日"}


st.set_page_config(page_title="バスアクセスサーチ", page_icon="🚌", layout="wide")


@st.cache_data
def load_data():
    stops = pd.read_csv(DATA / "stops.csv.gz", dtype={"stop_id": str})
    stop_routes = pd.read_csv(DATA / "stop_routes.csv.gz", dtype={"stop_id": str, "route_id": str})
    return stops, stop_routes


def geocode(query):
    url = "https://msearch.gsi.go.jp/address-search/AddressSearch?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={"User-Agent": "BusAccessSearch-Prototype/0.1"})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def distances_m(lat, lon, stop_lats, stop_lons):
    # Haversine formula: straight-line distance on Earth.
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = stop_lats.map(math.radians)
    lon2 = stop_lons.map(math.radians)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = (dlat / 2).map(math.sin) ** 2 + math.cos(lat1) * lat2.map(math.cos) * (dlon / 2).map(math.sin) ** 2
    return 6371000 * 2 * a.map(math.sqrt).map(math.asin)


def search_nearby(lat, lon, radius, day_col, stops, stop_routes):
    nearby = stops.copy()
    nearby["距離(m)"] = distances_m(lat, lon, nearby["stop_lat"], nearby["stop_lon"]).round().astype(int)
    nearby = nearby.loc[nearby["距離(m)"] <= radius].copy()
    if nearby.empty:
        return nearby, pd.DataFrame(), pd.DataFrame()

    detail = nearby.merge(stop_routes, on="stop_id", how="left")
    detail = detail.loc[detail[day_col].fillna(0) > 0].copy()
    detail["事業者"] = detail["agency_name"].fillna("不明")
    detail["営業所"] = detail["office_name"].fillna("不明")
    detail["系統"] = detail["route_short_name"].fillna(detail["route_id"])
    detail["行先"] = detail["trip_headsign"].fillna("—")

    # Same named stop may have separate poles/directions. Present one row per name and route.
    route_rows = detail.groupby(["stop_name", "事業者", "営業所", "系統", "行先"], as_index=False).agg(
        **{"距離(m)": ("距離(m)", "min"), "便数": (day_col, "sum")}
    ).sort_values(["距離(m)", "stop_name", "系統", "行先"])
    summary = route_rows.groupby("stop_name", as_index=False).agg(
        **{
            "距離(m)": ("距離(m)", "min"),
            "事業者": ("事業者", lambda x: "、".join(sorted(set(x)))),
            "営業所": ("営業所", lambda x: "、".join(sorted(set(x)))),
            "系統数": ("系統", "nunique"),
            "便数": ("便数", "sum"),
        }
    ).sort_values("距離(m)")
    return nearby, summary, route_rows


def excel_bytes(summary, routes):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="停留所一覧", index=False)
        routes.to_excel(writer, sheet_name="路線・行先", index=False)
    return output.getvalue()


st.title("🚌 バスアクセスサーチ")
st.caption("住所または緯度・経度を起点に、指定した直線距離内の都営バス停留所と系統を検索します。")
stops, stop_routes = load_data()

with st.sidebar:
    st.header("検索条件")
    mode = st.radio("地点の指定方法", ["住所検索", "緯度・経度"], horizontal=True)
    radius = st.selectbox("検索半径", [300, 500, 1000], index=1, format_func=lambda x: f"{x:,}m")
    day_label = st.selectbox("曜日", list(DAY_LABELS), index=0)
    st.caption("文字が別の語に変わる場合は、ブラウザの自動翻訳をオフにしてください。")

if "point" not in st.session_state:
    st.session_state.point = None

if mode == "住所検索":
    query = st.text_input("住所", placeholder="例：東京都新宿区西新宿2丁目8-1")
    if st.button("住所を検索", type="primary") and query:
        # A new address search invalidates the previously confirmed point/result.
        st.session_state.point = None
        st.session_state.candidates = []
        try:
            candidates = geocode(query)
            if candidates:
                st.session_state.candidates = candidates
            else:
                st.warning("候補が見つかりませんでした。住所を詳しく入力してください。")
        except Exception as exc:
            st.error(f"住所検索に接続できませんでした。緯度・経度入力をお試しください。（{exc}）")
    candidates = st.session_state.get("candidates", [])
    if candidates:
        labels = [x["properties"].get("title", "候補") for x in candidates]
        selected = st.selectbox("地点候補", range(len(labels)), format_func=lambda i: labels[i])
        if st.button("この地点でバス停を検索"):
            lon, lat = candidates[selected]["geometry"]["coordinates"]
            st.session_state.point = (float(lat), float(lon), labels[selected])
else:
    c1, c2 = st.columns(2)
    lat = c1.number_input("緯度", value=35.689501, format="%.6f")
    lon = c2.number_input("経度", value=139.691722, format="%.6f")
    if st.button("この地点でバス停を検索", type="primary"):
        st.session_state.point = (lat, lon, f"緯度 {lat:.6f}, 経度 {lon:.6f}")

if st.session_state.point:
    lat, lon, point_label = st.session_state.point
    day_col = DAY_LABELS[day_label]
    day_name = DAY_NAMES[day_col]
    nearby, summary, route_rows = search_nearby(lat, lon, radius, day_col, stops, stop_routes)
    st.subheader(point_label)
    if summary.empty:
        st.info(f"半径{radius:,}m以内に、{day_name}に運行する都営バス停留所が見つかりませんでした。")
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("停留所", f"{summary['stop_name'].nunique():,}か所")
        m2.metric("事業者", f"{route_rows['事業者'].nunique():,}社")
        m3.metric("系統", f"{route_rows['系統'].nunique():,}系統")
        m4.metric(f"{day_name}便数（停車回数）", f"{route_rows['便数'].sum():,.0f}便")

        map_points = nearby[["stop_lat", "stop_lon", "stop_name"]].rename(
            columns={"stop_lat": "lat", "stop_lon": "lon"}
        ).drop_duplicates()
        map_points["種類"] = "バス停"
        origin = pd.DataFrame([{"lat": lat, "lon": lon, "stop_name": "検索地点", "種類": "検索地点"}])
        stop_layer = pdk.Layer(
            "ScatterplotLayer", map_points, get_position="[lon, lat]",
            get_radius=7, radius_min_pixels=7, radius_max_pixels=7,
            get_fill_color=[30, 115, 230, 210],
            get_line_color=[255, 255, 255, 230], stroked=True, line_width_min_pixels=1,
            pickable=True,
        )
        origin_layer = pdk.Layer(
            "ScatterplotLayer", origin, get_position="[lon, lat]",
            get_radius=11, radius_min_pixels=11, radius_max_pixels=11,
            get_fill_color=[230, 35, 45, 255],
            get_line_color=[255, 255, 255, 255], stroked=True, line_width_min_pixels=3,
            pickable=True,
        )
        st.pydeck_chart(pdk.Deck(
            map_style="light",
            initial_view_state=pdk.ViewState(latitude=lat, longitude=lon, zoom=14.2, pitch=0),
            layers=[stop_layer, origin_layer],
            tooltip={"html": "<b>{種類}</b><br>{stop_name}"},
        ), use_container_width=True)

        tab1, tab2 = st.tabs(["停留所一覧", "路線・行先"])
        with tab1:
            st.dataframe(summary.rename(columns={"stop_name": "停留所名"}), hide_index=True, use_container_width=True)
        with tab2:
            st.dataframe(route_rows.rename(columns={"stop_name": "停留所名"}), hide_index=True, use_container_width=True)
        st.download_button(
            "Excelをダウンロード", excel_bytes(summary.rename(columns={"stop_name": "停留所名"}), route_rows.rename(columns={"stop_name": "停留所名"})),
            file_name="bus_access_search.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

st.divider()
st.caption("試作版：住所検索専用です。距離は直線距離です。便数はGTFSの標準的な曜日設定から集計し、臨時の運休・増便は反映していません。住所検索には国土地理院の検索機能を使用します。")
