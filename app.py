"""SEC Fund NAV Downloader — Streamlit app (multi-fund + CSV-backed dropdowns)."""
from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

FACTSHEET_KEY = "1bd81fb34be943e0b720df937f1d30e6"  # Fund Factsheet API
NAV_KEY = "2588c72bf4604d1ba8559f815b75b1be"  # Fund Daily Info
MAX_WORKERS = 12
BASE = "https://api.sec.or.th"
TIMEOUT = 30
HERE = Path(__file__).parent
AMC_CSV = HERE / "amc.csv"
FUNDS_CSV = HERE / "funds.csv"


def _get_json(url: str, key: str):
    r = requests.get(url, headers={"Ocp-Apim-Subscription-Key": key}, timeout=TIMEOUT)
    if r.status_code in (401, 403):
        raise RuntimeError(f"{r.status_code} — API key ใช้ไม่ได้กับ endpoint นี้")
    if r.status_code == 204:
        return None
    r.raise_for_status()
    return r.json()


@st.cache_data(show_spinner=False)
def load_amc() -> pd.DataFrame:
    return pd.read_csv(AMC_CSV)


@st.cache_data(show_spinner=False)
def load_funds() -> pd.DataFrame:
    return pd.read_csv(FUNDS_CSV)


def _fetch_one_nav(proj_id: str, d: date):
    url = f"{BASE}/FundDailyInfo/{proj_id}/dailynav/{d.strftime('%Y-%m-%d')}"
    try:
        data = _get_json(url, NAV_KEY)
        if not data:
            return []
        rows = data if isinstance(data, list) else [data]
        for row in rows:
            row.setdefault("proj_id", proj_id)
        return rows
    except Exception:
        return []


def fetch_nav_multi(proj_ids: list[str], start: date, end: date, progress=None) -> pd.DataFrame:
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    days = [d for d in days if d.weekday() < 5]
    tasks = [(pid, d) for pid in proj_ids for d in days]

    rows: list[dict] = []
    done = 0
    total = len(tasks)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_one_nav, pid, d): (pid, d) for pid, d in tasks}
        for fut in as_completed(futures):
            rows.extend(fut.result())
            done += 1
            if progress is not None and done % 5 == 0:
                progress.progress(done / total, text=f"ดึง NAV {done}/{total}")
    if progress is not None:
        progress.progress(1.0, text=f"เสร็จ {total}/{total}")

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "nav_date" in df.columns:
        df["nav_date"] = pd.to_datetime(df["nav_date"]).dt.date
    sort_cols = [c for c in ("proj_id", "class_abbr_name", "nav_date") if c in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols).reset_index(drop=True)
    return df


def fetch_performance(proj_id: str) -> pd.DataFrame:
    data = _get_json(f"{BASE}/FundFactsheet/fund/{proj_id}/performance", FACTSHEET_KEY)
    return pd.DataFrame(data) if data else pd.DataFrame()


st.set_page_config(page_title="SEC Fund NAV Downloader", page_icon="📈", layout="wide")
st.title("📈 SEC Fund NAV Downloader")
st.caption("ดึงข้อมูล NAV กองทุนรวมจาก SEC OpenAPI · เลือกได้หลายกอง")

if not AMC_CSV.exists() or not FUNDS_CSV.exists():
    st.error("ไม่พบ amc.csv หรือ funds.csv — รัน `python fetch_funds.py` ก่อน")
    st.stop()

amc_df = load_amc()
funds_df = load_funds()

amc_label_col = "name_th" if "name_th" in amc_df.columns else amc_df.columns[0]
amc_df = amc_df.sort_values(amc_label_col).reset_index(drop=True)
amc_options = {row[amc_label_col]: row["unique_id"] for _, row in amc_df.iterrows()}

c1, c2 = st.columns([1, 2])
with c1:
    amc_choice = st.selectbox(f"1️⃣ เลือก บลจ ({len(amc_options)} แห่ง)", list(amc_options.keys()))
    unique_id = amc_options[amc_choice]

with c2:
    sub = funds_df[funds_df["amc_unique_id"] == unique_id].copy()
    sub = sub.sort_values("proj_abbr_name").reset_index(drop=True)
    if sub.empty:
        st.info("ไม่พบกองทุนของ บลจ นี้")
        st.stop()

    fund_options = {
        f"{r['proj_abbr_name']} — {r['proj_name_th'] or r['proj_name_en'] or ''}": r["proj_id"]
        for _, r in sub.iterrows()
    }
    fund_labels = list(fund_options.keys())
    selected_labels = st.multiselect(
        f"2️⃣ เลือกกองทุน (เลือกได้หลายกอง · มีทั้งหมด {len(fund_options)} กอง)",
        fund_labels,
        default=fund_labels[:1],
    )
    selected_proj_ids = [fund_options[lbl] for lbl in selected_labels]

if not selected_proj_ids:
    st.info("เลือกกองทุนอย่างน้อย 1 กอง")
    st.stop()

st.caption(f"เลือก **{len(selected_proj_ids)} กอง** · {', '.join(selected_proj_ids)}")
st.divider()

tab_nav, tab_perf = st.tabs(["📈 NAV รายวัน", "📊 Performance"])

with tab_nav:
    today = date.today()
    default_start = today - timedelta(days=180)
    d1, d2, d3 = st.columns([1, 1, 1])
    with d1:
        start = st.date_input("เริ่ม", value=default_start, max_value=today, key="nav_start")
    with d2:
        end = st.date_input("ถึง", value=today, max_value=today, key="nav_end")
    with d3:
        st.write("")
        st.write("")
        fetch_nav = st.button("⬇️ โหลด NAV", type="primary", use_container_width=True)

    if fetch_nav:
        if start > end:
            st.error("วันที่เริ่มต้องไม่หลังวันที่สิ้นสุด")
        else:
            prog = st.progress(0.0, text="เริ่มดึง NAV...")
            try:
                nav_df = fetch_nav_multi(selected_proj_ids, start, end, progress=prog)
            except Exception as exc:
                st.error(str(exc))
                nav_df = pd.DataFrame()
            prog.empty()

            if nav_df.empty:
                st.warning("ไม่พบข้อมูล NAV ในช่วงเวลานี้")
            else:
                st.success(f"พบข้อมูล {len(nav_df):,} แถว · {nav_df['proj_id'].nunique()} กอง")

                if {"nav_date", "last_val", "proj_id"}.issubset(nav_df.columns):
                    label_col = "class_abbr_name" if "class_abbr_name" in nav_df.columns else "proj_id"
                    nav_df["_series"] = nav_df["proj_id"].astype(str) + " · " + nav_df[label_col].astype(str)
                    chart_df = nav_df.pivot_table(
                        index="nav_date", columns="_series", values="last_val", aggfunc="first"
                    )
                    st.line_chart(chart_df, height=360)
                    nav_df = nav_df.drop(columns=["_series"])

                st.dataframe(nav_df, use_container_width=True, height=420)

                buf = io.StringIO()
                nav_df.to_csv(buf, index=False)
                fname = f"NAV_{len(selected_proj_ids)}funds_{start}_{end}.csv"
                st.download_button(
                    "💾 ดาวน์โหลด CSV",
                    data=buf.getvalue(),
                    file_name=fname,
                    mime="text/csv",
                    use_container_width=True,
                )

with tab_perf:
    st.caption("ตัวเลขผลการดำเนินงาน + ความผันผวน")
    if st.button("📊 โหลด Performance", type="primary"):
        prog = st.progress(0.0, text="กำลังดึงข้อมูล...")
        all_perf = []
        for i, pid in enumerate(selected_proj_ids):
            try:
                df = fetch_performance(pid)
                if not df.empty:
                    df["proj_id"] = pid
                    all_perf.append(df)
            except Exception as exc:
                st.warning(f"{pid}: {exc}")
            prog.progress((i + 1) / len(selected_proj_ids), text=f"{i+1}/{len(selected_proj_ids)}")
        prog.empty()

        if not all_perf:
            st.warning("ไม่พบข้อมูล performance")
        else:
            perf_df = pd.concat(all_perf, ignore_index=True)
            st.success(f"พบข้อมูล {len(perf_df):,} แถว · {perf_df['proj_id'].nunique()} กอง")
            st.dataframe(perf_df, use_container_width=True, height=480)

            buf = io.StringIO()
            perf_df.to_csv(buf, index=False)
            st.download_button(
                "💾 ดาวน์โหลด CSV",
                data=buf.getvalue(),
                file_name=f"PERF_{len(selected_proj_ids)}funds.csv",
                mime="text/csv",
                use_container_width=True,
            )
