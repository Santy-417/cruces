import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from glob import glob

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_INTERVALS = {
    "Manual":   0,
    "30 min":   30,
    "1 hora":   60,
    "2 horas":  120,
    "4 horas":  240,
}

st.set_page_config(page_title="crucesmacros", layout="wide")

_GREEN  = "background-color: #92D050"
_YELLOW = "background-color: #FFFF00"
_RED    = "background-color: #FF0000; color: white"
_NONE   = ""


# ── Color rules ────────────────────────────────────────────────────────────────

def _color_status(val):
    if pd.isna(val) or str(val).strip() == "":
        return _NONE
    s = str(val)
    if s == "operational":
        return _GREEN
    if s == "rangingAutoAdjComplete":
        return _YELLOW
    return _RED


def _color_numeric(val, green_fn, yellow_fn=None):
    if pd.isna(val) or str(val).strip() == "":
        return _NONE
    try:
        v = float(val)
    except (ValueError, TypeError):
        return _NONE
    if green_fn(v):
        return _GREEN
    if yellow_fn and yellow_fn(v):
        return _YELLOW
    return _RED


def _color_ont_status(val):
    if pd.isna(val) or str(val).strip() == "":
        return _NONE
    return _GREEN if str(val) == "up" else _RED


_COL_RULES = {
    "Status":     lambda v: _color_status(v),
    "Dw SNR":     lambda v: _color_numeric(v, lambda x: x > 37,           lambda x: 35 <= x <= 37),
    "PL Dw":      lambda v: _color_numeric(v, lambda x: -10 <= x <= 12,   lambda x: -15 <= x < -10),
    "Up SNR":     lambda v: _color_numeric(v, lambda x: x > 29,           lambda x: 27 <= x <= 29),
    "PL Up":      lambda v: _color_numeric(v, lambda x: 38 <= x <= 47.9,  lambda x: 48 <= x <= 50.9),
    "ONT Status": lambda v: _color_ont_status(v),
    "TX_ONT":     lambda v: _color_numeric(v, lambda x: 0.5 <= x <= 5),
    "RX_ONT":     lambda v: _color_numeric(v, lambda x: -27 <= x <= -10),
}


def _style_df(df: pd.DataFrame):
    styler = df.style
    for col, fn in _COL_RULES.items():
        if col in df.columns:
            styler = styler.map(fn, subset=[col])
    return styler


# ── Data loading ───────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def _load(path: str) -> dict[str, pd.DataFrame]:
    return pd.read_excel(path, sheet_name=None)


def _find_latest_excel() -> str | None:
    output_dir = os.getenv("OUTPUT_DIR", ".")
    pattern = os.path.join(output_dir, "Ingreso_Siebel_*.xlsx")
    files = sorted(glob(pattern), key=os.path.getmtime, reverse=True)
    return files[0] if files else None


# ── Pipeline state ─────────────────────────────────────────────────────────────

def _init_state():
    if "proc"         not in st.session_state: st.session_state.proc         = None
    if "last_run"     not in st.session_state: st.session_state.last_run     = None
    if "next_run"     not in st.session_state: st.session_state.next_run     = None
    if "interval_min" not in st.session_state: st.session_state.interval_min = 0
    if "run_error"    not in st.session_state: st.session_state.run_error    = False

_init_state()

def _start_pipeline():
    st.session_state.proc = subprocess.Popen(
        [sys.executable, "main.py", "--limit", "1000"],
        cwd=BASE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    st.session_state.run_error = False

def _schedule_next():
    mins = st.session_state.interval_min
    st.session_state.next_run = (datetime.now() + timedelta(minutes=mins)) if mins > 0 else None

# ── Poll pipeline process ───────────────────────────────────────────────────────

proc = st.session_state.proc
if proc is not None:
    code = proc.poll()
    if code is None:
        time.sleep(1)
        st.rerun()
    else:
        st.session_state.run_error = (code != 0)
        st.session_state.last_run  = datetime.now()
        st.session_state.proc      = None
        _schedule_next()
        st.cache_data.clear()
        st.rerun()

# Auto-trigger scheduled run
now = datetime.now()
nxt = st.session_state.next_run
if st.session_state.proc is None and nxt is not None and now >= nxt:
    _start_pipeline()
    st.rerun()

# ── App ────────────────────────────────────────────────────────────────────────

path = _find_latest_excel()

if path is None:
    st.error("No se encontró ningún archivo `Ingreso_Siebel_*.xlsx`. Corré primero `python main.py`.")
    st.stop()

sheets      = _load(path)
df_exporte  = sheets.get("EXPORTE",  pd.DataFrame())
df_pnm      = sheets.get("PNM",      pd.DataFrame())
df_axtract  = sheets.get("AXTRACT",  pd.DataFrame())

st.title("crucesmacros — Incidentes Siebel")
st.caption(f"Fuente: `{os.path.basename(path)}`")

# ── Sidebar filters ────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Reporte")

    col_sel, col_btn = st.columns([2, 1])
    with col_sel:
        sel_interval = st.selectbox(
            "Intervalo",
            list(_INTERVALS.keys()),
            index=list(_INTERVALS.keys()).index(
                next((k for k, v in _INTERVALS.items() if v == st.session_state.interval_min), "Manual")
            ),
            label_visibility="collapsed",
        )
        st.session_state.interval_min = _INTERVALS[sel_interval]

    with col_btn:
        running = st.session_state.proc is not None
        if st.button(
            ":material/refresh: Generar",
            disabled=running,
            use_container_width=True,
        ):
            _start_pipeline()
            st.rerun()

    last = st.session_state.last_run
    nxt  = st.session_state.next_run
    err  = st.session_state.run_error

    if running:
        st.info(":material/hourglass_top: Pipeline en ejecución...")
    elif err:
        st.error(":material/error: La última ejecución falló.")
    elif last:
        st.caption(f"Última: {last.strftime('%d/%m %H:%M')}")
        if nxt:
            st.caption(f"Próxima: {nxt.strftime('%d/%m %H:%M')}")

    st.divider()
    st.header("Filtros")

    sel_tec    = []
    sel_ciudad = []
    sel_sub    = []

    if "TECNOLOGIA" in df_exporte.columns:
        opts_tec = sorted(df_exporte["TECNOLOGIA"].dropna().unique().tolist())
        sel_tec = st.multiselect("Tecnología", opts_tec, default=opts_tec)

    if "CIUDAD" in df_exporte.columns:
        opts_ciudad = sorted(df_exporte["CIUDAD"].dropna().unique().tolist())
        sel_ciudad = st.multiselect("Ciudad", opts_ciudad, default=opts_ciudad)

    if "SUB_ESTADO" in df_exporte.columns:
        opts_sub = sorted(df_exporte["SUB_ESTADO"].dropna().unique().tolist())
        sel_sub = st.multiselect("Sub-estado", opts_sub, default=opts_sub)

    only_pnm = st.checkbox("Solo con PNM respondido")

    st.divider()

    with open(path, "rb") as fh:
        st.download_button(
            label=":material/download: Descargar Excel",
            data=fh,
            file_name=os.path.basename(path),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

# ── Apply filters ──────────────────────────────────────────────────────────────

df = df_exporte.copy()

if "TECNOLOGIA" in df.columns and sel_tec:
    df = df[df["TECNOLOGIA"].isin(sel_tec)]
if "CIUDAD" in df.columns and sel_ciudad:
    df = df[df["CIUDAD"].isin(sel_ciudad)]
if "SUB_ESTADO" in df.columns and sel_sub:
    df = df[df["SUB_ESTADO"].isin(sel_sub)]
if only_pnm and "Status" in df.columns:
    df = df[df["Status"].notna() & (df["Status"].astype(str).str.strip() != "")]

# ── Metrics ────────────────────────────────────────────────────────────────────

total = len(df)

hfc_mask  = df["TECNOLOGIA"].str.contains("COAXIAL", case=False, na=False) if "TECNOLOGIA" in df.columns else pd.Series(False, index=df.index)
gpon_mask = df["TECNOLOGIA"].str.contains("GPON|FIBRA", case=False, na=False) if "TECNOLOGIA" in df.columns else pd.Series(False, index=df.index)

hfc_total  = int(hfc_mask.sum())
gpon_total = int(gpon_mask.sum())

pnm_ok = int(
    (df.loc[hfc_mask, "Status"].notna() & (df.loc[hfc_mask, "Status"].astype(str).str.strip() != "")).sum()
) if "Status" in df.columns and hfc_total > 0 else 0

axtract_ok = int(
    (df.loc[gpon_mask, "ONT Status"].notna() & (df.loc[gpon_mask, "ONT Status"].astype(str).str.strip() != "")).sum()
) if "ONT Status" in df.columns and gpon_total > 0 else 0

nodo_top = "—"
nodo_col = next((c for c in ["ID_NODO_LIMPIO", "ID_NODO"] if c in df.columns), None)
if nodo_col:
    counts = df[nodo_col].value_counts()
    if not counts.empty:
        nodo_top = f"{counts.index[0]} ({counts.iloc[0]})"

with st.container(horizontal=True):
    st.metric("Incidentes", total, border=True)
    st.metric("HFC con PNM", f"{pnm_ok} / {hfc_total}", border=True)
    st.metric("GPON con Axtract", f"{axtract_ok} / {gpon_total}", border=True)
    st.metric("Nodo top", nodo_top, border=True)

st.divider()

# ── Tabs ───────────────────────────────────────────────────────────────────────

tab_exporte, tab_pnm, tab_axtract = st.tabs(["EXPORTE", "PNM raw", "Axtract raw"])

with tab_exporte:
    if df.empty:
        st.info("Sin resultados con los filtros actuales.")
    else:
        st.dataframe(_style_df(df), hide_index=True, height=600)

with tab_pnm:
    if df_pnm.empty:
        st.info("No hay hoja PNM en el Excel (sin CMs HFC o se usó --skip-pnm).")
    else:
        st.dataframe(df_pnm, hide_index=True, height=600)

with tab_axtract:
    if df_axtract.empty:
        st.info("No hay hoja AXTRACT en el Excel (sin ONTs GPON o se usó --skip-axtract).")
    else:
        st.dataframe(df_axtract, hide_index=True, height=600)
