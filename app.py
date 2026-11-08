# -*- coding: utf-8 -*-
"""
PChome 商品價格查詢與視覺化 (Streamlit)
合併 CODE01(爬蟲) + CODE02(分析/繪圖)

執行方式:
    pip install -r requirements.txt
    streamlit run app.py

說明:
- 圖表使用 Plotly,由瀏覽器負責渲染文字,因此不需要安裝/下載任何中文字型
  (不再需要 !wget 下載 TaipeiSansTCBeta-Regular.ttf)。
"""
import re
import time

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

API_URL = "https://ecshweb.pchome.com.tw/search/v3.3/all/results"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
SORT_OPTIONS = {
    "熱銷排序": "sale/dc",
    "價格由低到高": "prc/ac",
    "價格由高到低": "prc/dc",
    "相關度": "rnk/dc",
}

st.set_page_config(page_title="PChome 價格查詢", page_icon="🛒", layout="wide")


# ----------------------------------------------------------------------
# CODE01:爬蟲
# ----------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def fetch_page(keyword: str, page: int, sort: str) -> dict:
    """抓取單一頁的搜尋結果(JSON)。用 params 讓 requests 自動處理中文編碼。"""
    resp = requests.get(
        API_URL,
        params={"q": keyword, "page": page, "sort": sort},
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def crawl(keyword: str, pages: int, sort: str, delay: float) -> pd.DataFrame:
    """依序抓取 1~pages 頁並合併成一個 DataFrame。"""
    frames = []
    progress = st.progress(0.0, text="開始抓取…")
    for i in range(1, pages + 1):
        progress.progress((i - 1) / pages, text=f"抓取第 {i}/{pages} 頁…")
        data = fetch_page(keyword, i, sort)
        prods = data.get("prods") or []
        if not prods:  # 已沒有更多資料
            st.info(f"第 {i} 頁沒有資料,提前結束。")
            break
        frames.append(pd.DataFrame(prods))

        total_page = data.get("totalPage")
        if total_page and i >= int(total_page):
            if i < pages:
                st.info(f"此關鍵字共 {total_page} 頁,已全部抓完。")
            break
        if i < pages:
            time.sleep(delay)  # 避免請求過快
    progress.progress(1.0, text="抓取完成")
    progress.empty()

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ----------------------------------------------------------------------
# CODE02:資料整理 / 圖表
# ----------------------------------------------------------------------
def clean(raw: pd.DataFrame) -> pd.DataFrame:
    """只保留 name / price,並轉成數值。"""
    cols = [c for c in ("name", "price") if c in raw.columns]
    df = raw[cols].copy()
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna(subset=["price"]).reset_index(drop=True)
    df.index = df.index + 1
    df.index.name = "序號"
    return df


def guess_brand(name: str) -> str:
    """從商品名稱粗略抓品牌:優先取【品牌】,否則取第一個詞。"""
    m = re.search(r"【(.+?)】", str(name))
    if m:
        return m.group(1).strip()[:12] or "其他"
    first = re.split(r"[\s/｜|]", str(name).strip())[0]
    return first[:12] or "其他"


def add_price_range(df: pd.DataFrame, bins: int) -> pd.DataFrame:
    df = df.copy()
    if df["price"].nunique() < 2:
        df["價格區間"] = "單一價格"
        df["_order"] = 0
        return df
    cut = pd.cut(df["price"], bins=bins, precision=0, include_lowest=True)
    df["價格區間"] = cut.astype(str)
    # 依區間大小排序用
    df["_order"] = cut.cat.codes
    return df


def line_chart(df: pd.DataFrame, top_n: int):
    sub = df.head(top_n)
    avg = df["price"].mean()
    fig = px.line(
        sub.reset_index(),
        x="序號",
        y="price",
        markers=True,
        hover_data={"name": True, "price": ":,.0f", "序號": True},
        labels={"price": "價格", "序號": "商品序號"},
        title=f"價格折線圖(前 {len(sub)} 筆)",
    )
    fig.add_hline(
        y=avg,
        line_dash="dash",
        line_color="red",
        annotation_text=f"平均價 {avg:,.0f}",
        annotation_position="top left",
    )
    fig.update_layout(height=520)
    return fig


def pie_chart(df: pd.DataFrame, bins: int):
    d = add_price_range(df, bins)
    counts = (
        d.groupby(["_order", "價格區間"]).size().reset_index(name="數量").sort_values("_order")
    )
    fig = px.pie(
        counts,
        names="價格區間",
        values="數量",
        hole=0.3,
        title="價格區間分布圓餅圖",
    )
    fig.update_traces(textinfo="label+percent+value", sort=False)
    fig.update_layout(height=560)
    return fig


def sunburst_chart(df: pd.DataFrame, bins: int):
    d = add_price_range(df, bins)
    d["品牌"] = d["name"].map(guess_brand)
    # 只保留出現較多的品牌,其餘歸入「其他」,避免圖太雜
    top = d["品牌"].value_counts().head(12).index
    d["品牌"] = d["品牌"].where(d["品牌"].isin(top), "其他")
    g = d.groupby(["價格區間", "品牌"]).agg(數量=("price", "size"), 平均價=("price", "mean")).reset_index()
    fig = px.sunburst(
        g,
        path=["價格區間", "品牌"],
        values="數量",
        color="平均價",
        color_continuous_scale="Blues",
        title="價格區間 → 品牌 旭日圖",
    )
    fig.update_layout(height=620)
    return fig


# ----------------------------------------------------------------------
# 介面
# ----------------------------------------------------------------------
st.title("🛒 PChome 商品價格查詢")
st.caption("輸入商品關鍵字與頁數,抓取 PChome 24h 搜尋結果並視覺化。")

with st.sidebar:
    st.header("查詢條件")
    with st.form("query_form"):
        keyword = st.text_input("商品關鍵字", value="電風扇")
        pages = st.number_input("抓取頁數", min_value=1, max_value=20, value=1, step=1)
        sort_label = st.selectbox("排序方式", list(SORT_OPTIONS.keys()))
        delay = st.slider("每頁間隔秒數", 0.0, 10.0, 1.0, 0.5,
                          help="原本程式為 10 秒;太短可能被網站限制。")
        submitted = st.form_submit_button("🔍 查詢", use_container_width=True)

if submitted:
    if not keyword.strip():
        st.warning("請輸入商品關鍵字")
    else:
        try:
            raw = crawl(keyword.strip(), int(pages), SORT_OPTIONS[sort_label], delay)
        except requests.RequestException as e:
            st.error(f"連線失敗:{e}")
            raw = pd.DataFrame()
        except ValueError as e:
            st.error(f"解析回應失敗(可能被網站擋下):{e}")
            raw = pd.DataFrame()

        if raw.empty:
            st.session_state.pop("result", None)
            st.warning("查無資料,請換個關鍵字試試。")
        else:
            st.session_state["result"] = {
                "keyword": keyword.strip(),
                "raw": raw,
                "df": clean(raw),
            }

result = st.session_state.get("result")
if not result:
    st.info("請在左側輸入條件後按「查詢」。")
    st.stop()

df: pd.DataFrame = result["df"]
raw: pd.DataFrame = result["raw"]
if df.empty:
    st.warning("資料中沒有可用的價格欄位。")
    st.stop()

st.subheader(f"「{result['keyword']}」查詢結果")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("商品數", f"{len(df):,}")
c2.metric("平均價", f"{df['price'].mean():,.0f}")
c3.metric("最高價", f"{df['price'].max():,.0f}")
c4.metric("最低價", f"{df['price'].min():,.0f}")
c5.metric("缺值數(原始)", int(raw[["name", "price"]].isnull().sum().sum())
          if {"name", "price"} <= set(raw.columns) else 0)

chart_type = st.radio("圖表類型", ["折線圖", "圓餅圖", "旭日圖"], horizontal=True)

if chart_type == "折線圖":
    top_n = st.slider("顯示前幾筆", 5, max(5, len(df)), min(70, len(df)))
    st.plotly_chart(line_chart(df, top_n), use_container_width=True)
elif chart_type == "圓餅圖":
    bins = st.slider("價格區間數", 2, 8, 5)
    st.plotly_chart(pie_chart(df, bins), use_container_width=True)
else:
    bins = st.slider("價格區間數", 2, 8, 4)
    st.plotly_chart(sunburst_chart(df, bins), use_container_width=True)
    st.caption("品牌為依商品名稱【】或第一個詞推測,僅供參考。")

with st.expander("查看資料表 / 下載"):
    st.dataframe(df, use_container_width=True)
    st.download_button(
        "⬇️ 下載 CSV(完整原始欄位)",
        data=raw.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"PCHOME_{result['keyword']}.csv",
        mime="text/csv",
    )
