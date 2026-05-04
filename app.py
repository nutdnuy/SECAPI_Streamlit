"""SEC Fund NAV Downloader — Streamlit app (multi-fund + CSV-backed dropdowns)."""
from __future__ import annotations

import io
import re
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
ALL_AMC = "🌐 ทุก บลจ."
INCEPTION_FALLBACK = date(2000, 1, 1)


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


def _strip_class_suffix(name: str) -> str:
    """Strip share-class suffix from fund abbr or name.

    Handles three patterns:
    1. Parentheses:    K-GA-A(A), K-GA-A(D)              → K-GA-A
    2. Trailing caps:  K-GB1YAB, K-GB1YAC, K-GB1YAD      → K-GB1Y
    3. Thai suffix:    "...1 ปี AB" / "...1 ปี AD"       → "...1 ปี"
    """
    if not isinstance(name, str):
        return ""
    s = name.strip()
    s = re.sub(r"\s*\([^)]+\)\s*$", "", s)        # (A), (D), (ACC) ...
    s = re.sub(r"\s+[A-Z]{1,3}\s*$", "", s)        # " AB", " AD", " ACC" (Thai/spaced)
    s = re.sub(r"(?<=[a-z0-9])[A-Z]{1,3}\s*$", "", s)  # K-GB1YAB → K-GB1Y
    return s.strip()


def _group_key(row) -> str:
    """Build a dedup key from Thai name → English name → abbr (whichever first non-empty)."""
    for col in ("proj_name_th", "proj_name_en", "proj_abbr_name"):
        val = row.get(col)
        if isinstance(val, str) and val.strip():
            return _strip_class_suffix(val).lower()
    return str(row.get("proj_id", ""))


def _proj_id_inception(proj_id: str) -> date:
    """Parse start year from proj_id like 'M0746_2550' → 2007-01-01."""
    m = re.search(r"_(\d{4})$", str(proj_id))
    if not m:
        return INCEPTION_FALLBACK
    year_be = int(m.group(1))
    year_ce = year_be - 543 if year_be > 2400 else year_be
    try:
        return date(year_ce, 1, 1)
    except ValueError:
        return INCEPTION_FALLBACK


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
funds_df = load_funds().copy()

amc_label_col = "name_th" if "name_th" in amc_df.columns else amc_df.columns[0]
amc_df = amc_df.sort_values(amc_label_col).reset_index(drop=True)
amc_options = {ALL_AMC: None}
amc_options.update({row[amc_label_col]: row["unique_id"] for _, row in amc_df.iterrows()})

# --- 1️⃣ AMC selector (full width) ---
amc_choice = st.selectbox(f"1️⃣ เลือก บลจ ({len(amc_df)} แห่ง)", list(amc_options.keys()))
unique_id = amc_options[amc_choice]

# --- 2️⃣ Fund selector (full width, deduped by base abbr) ---
if unique_id is None:
    sub = funds_df.copy()
else:
    sub = funds_df[funds_df["amc_unique_id"] == unique_id].copy()

if sub.empty:
    st.info("ไม่พบกองทุน")
    st.stop()

show_all_classes = st.checkbox(
    "แสดงทุก share class (ไม่ยุบรวม)",
    value=False,
    help="ปกติจะรวม share class ของกองเดียวกันให้เหลือ 1 แถว · ติ๊กถ้าอยากเลือก class แยกเอง",
)

sub = sub.copy()
sub["_group_key"] = sub.apply(_group_key, axis=1)
sub = sub.sort_values(["_group_key", "proj_abbr_name"]).reset_index(drop=True)

if show_all_classes:
    display_funds = sub
else:
    display_funds = sub.groupby("_group_key", as_index=False).first()
display_funds = display_funds.sort_values("proj_abbr_name").reset_index(drop=True)

fund_options: dict[str, str] = {}
for _, r in display_funds.iterrows():
    name = r["proj_name_th"] or r["proj_name_en"] or ""
    label = f"{r['proj_abbr_name']} — {name}"
    fund_options[label] = r["proj_id"]

fund_labels = list(fund_options.keys())

selected_labels = st.multiselect(
    f"2️⃣ เลือกกองทุน · พิมพ์ชื่อย่อหรือชื่อเต็มเพื่อค้นหา · มี {len(fund_options):,} กอง",
    fund_labels,
    default=fund_labels[:1] if (fund_labels and "fund_multi" not in st.session_state) else None,
    key="fund_multi",
    placeholder="🔍 พิมพ์ชื่อกอง เช่น K-GA, กรุงศรี, S&P500, infrastructure...",
)
selected_proj_ids = [fund_options[lbl] for lbl in selected_labels if lbl in fund_options]

if not selected_proj_ids:
    st.info("เลือกกองทุนอย่างน้อย 1 กอง")
    st.stop()

st.caption(f"เลือก **{len(selected_proj_ids)} กอง** · {', '.join(selected_proj_ids)}")
st.divider()

tab_nav, tab_perf = st.tabs(["📈 NAV รายวัน", "📊 Performance"])

with tab_nav:
    today = date.today()

    PRESETS = {
        "6 เดือน": 180,
        "1 ปี": 365,
        "3 ปี": 365 * 3,
        "5 ปี": 365 * 5,
        "10 ปี": 365 * 10,
    }
    preset_choice = st.radio(
        "📅 ช่วงเวลาด่วน",
        list(PRESETS.keys()) + ["กำหนดเอง"],
        index=3,  # default: 5 ปี
        horizontal=True,
        key="date_preset",
    )

    if preset_choice in PRESETS:
        default_start = today - timedelta(days=PRESETS[preset_choice])
        custom = False
    else:
        default_start = today - timedelta(days=365)
        custom = True

    d1, d2, d3 = st.columns([1, 1, 1])
    with d1:
        start = st.date_input(
            "เริ่ม",
            value=default_start,
            max_value=today,
            key=f"nav_start_{preset_choice}",
            disabled=not custom,
        )
    with d2:
        end = st.date_input("ถึง", value=today, max_value=today, key="nav_end")
    with d3:
        st.write("")
        st.write("")
        fetch_nav = st.button("⬇️ โหลด NAV", type="primary", use_container_width=True)

    actual_start = default_start if not custom else start

    if fetch_nav:
        if actual_start > end:
            st.error("วันที่เริ่มต้องไม่หลังวันที่สิ้นสุด")
        else:
            days_count = (end - actual_start).days
            if days_count > 365 * 5:
                st.warning(
                    f"ช่วงนี้ยาว {days_count:,} วัน (~{days_count // 365} ปี) — อาจใช้เวลาดึงนาน"
                )
            prog = st.progress(0.0, text="เริ่มดึง NAV...")
            try:
                nav_df = fetch_nav_multi(selected_proj_ids, actual_start, end, progress=prog)
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
                fname = f"NAV_{len(selected_proj_ids)}funds_{actual_start}_{end}.csv"
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
