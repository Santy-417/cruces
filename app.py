"""
crucesmacros — Visor Streamlit (Fase 1)
Lee el Excel más reciente de OUTPUT_DIR y lo muestra con filtros y colores.
"""

import os
from datetime import datetime
from glob import glob

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR = os.getenv("OUTPUT_DIR", ".")

st.set_page_config(
    page_title="Crucesmacros — Visor",
    layout="wide",
)

# ──────────────────────────────────────────────────────────────────────────────
# Carga del Excel
# ──────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_latest_excel(output_dir: str) -> tuple[dict[str, pd.DataFrame] | None, str | None]:
    pattern = os.path.join(output_dir, "Ingreso_Siebel_*.xlsx")
    files = sorted(glob(pattern), key=os.path.getmtime, reverse=True)
    if not files:
        return None, None
    latest = files[0]
    sheets = pd.read_excel(latest, sheet_name=None, engine="openpyxl")
    return sheets, latest


# ──────────────────────────────────────────────────────────────────────────────
# Colores (pd.Styler)
# ──────────────────────────────────────────────────────────────────────────────

_GREEN  = "background-color: #92D050"
_YELLOW = "background-color: #FFFF00"
_RED    = "background-color: #FF0000; color: white"


def _num(val):
    if pd.isna(val) or str(val).strip() == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _color_status(val):
    if pd.isna(val) or str(val).strip() == "":
        return ""
    s = str(val).strip()
    if s == "operational":
        return _GREEN
    if s == "rangingAutoAdjComplete":
        return _YELLOW
    return _RED


def _color_dw_snr(val):
    v = _num(val)
    if v is None:
        return ""
    return _GREEN if v > 37 else (_YELLOW if v >= 35 else _RED)


def _color_pl_dw(val):
    v = _num(val)
    if v is None:
        return ""
    if -10 <= v <= 12:
        return _GREEN
    if -15 <= v < -10:
        return _YELLOW
    return _RED


def _color_up_snr(val):
    v = _num(val)
    if v is None:
        return ""
    return _GREEN if v > 29 else (_YELLOW if v >= 27 else _RED)


def _color_pl_up(val):
    v = _num(val)
    if v is None:
        return ""
    if 38 <= v <= 47.9:
        return _GREEN
    if 48 <= v <= 50.9:
        return _YELLOW
    return _RED


def _color_ont_status(val):
    if pd.isna(val) or str(val).strip() == "":
        return ""
    return _GREEN if str(val).strip() == "up" else _RED


def _color_tx_power(val):
    v = _num(val)
    if v is None:
        return ""
    return _GREEN if 0.5 <= v <= 5 else _RED


def _color_rx_power(val):
    v = _num(val)
    if v is None:
        return ""
    return _GREEN if -27 <= v <= -10 else _RED


_COL_STYLERS = {
    "Status":               _color_status,
    "Dw SNR":               _color_dw_snr,
    "PL Dw":                _color_pl_dw,
    "Up SNR":               _color_up_snr,
    "PL Up":                _color_pl_up,
    "ONT Status":           _color_ont_status,
    "Last TX Power (dBm)":  _color_tx_power,
    "Last RX Power (dBm)":  _color_rx_power,
}


def apply_styles(df: pd.DataFrame):
    styler = df.style
    for col, fn in _COL_STYLERS.items():
        if col in df.columns:
            styler = styler.map(fn, subset=[col])
    return styler


def _text_filter(df: pd.DataFrame, query: str) -> pd.DataFrame:
    """Filtra filas donde cualquier columna contiene el texto buscado."""
    if not query.strip():
        return df
    q = query.strip().lower()
    mask = df.apply(
        lambda col: col.astype(str).str.lower().str.contains(q, na=False)
    ).any(axis=1)
    return df[mask]


# ──────────────────────────────────────────────────────────────────────────────
# Cargar datos
# ──────────────────────────────────────────────────────────────────────────────

sheets, excel_path = load_latest_excel(OUTPUT_DIR)

if sheets is None:
    st.title("Crucesmacros — Visor")
    st.warning(f"No se encontro ningun archivo Excel en `{OUTPUT_DIR}`.")
    st.info("Ejecuta `python main.py` para generar el reporte primero.")
    st.stop()

df_exporte: pd.DataFrame = sheets.get("EXPORTE",  pd.DataFrame())
df_pnm:     pd.DataFrame = sheets.get("PNM",      pd.DataFrame())
df_axtract: pd.DataFrame = sheets.get("AXTRACT",  pd.DataFrame())

# ──────────────────────────────────────────────────────────────────────────────
# Sidebar — solo info y descarga
# ──────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    mod_dt = datetime.fromtimestamp(os.path.getmtime(excel_path))
    st.caption(f"Archivo: **{os.path.basename(excel_path)}**")
    st.caption(f"Generado: {mod_dt.strftime('%d/%m/%Y %H:%M')}")

    st.divider()

    with open(excel_path, "rb") as fh:
        st.download_button(
            label="Descargar Excel",
            data=fh,
            file_name=os.path.basename(excel_path),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )

# ──────────────────────────────────────────────────────────────────────────────
# Titulo y metricas globales (sin filtro todavia)
# ──────────────────────────────────────────────────────────────────────────────

st.title("Crucesmacros — Incidentes Siebel")

total = len(df_exporte)

hfc_mask  = df_exporte["TECNOLOGIA"].str.contains("COAXIAL", case=False, na=False) if "TECNOLOGIA" in df_exporte.columns else pd.Series(False, index=df_exporte.index)
gpon_mask = df_exporte["TECNOLOGIA"].str.contains("GPON|FIBRA|FTTH", case=False, na=False) if "TECNOLOGIA" in df_exporte.columns else pd.Series(False, index=df_exporte.index)

hfc_total  = int(hfc_mask.sum())
gpon_total = int(gpon_mask.sum())

pnm_ok = 0
if "Status" in df_exporte.columns and hfc_total > 0:
    pnm_ok = int((df_exporte.loc[hfc_mask, "Status"].notna() & (df_exporte.loc[hfc_mask, "Status"].astype(str).str.strip() != "")).sum())

axtract_ok = 0
if "ONT Status" in df_exporte.columns and gpon_total > 0:
    axtract_ok = int((df_exporte.loc[gpon_mask, "ONT Status"].notna() & (df_exporte.loc[gpon_mask, "ONT Status"].astype(str).str.strip() != "")).sum())

c1, c2, c3 = st.columns(3)
c1.metric("Total incidentes", total)
c2.metric("GPON con Axtract", f"{axtract_ok} / {gpon_total}")
c3.metric("HFC con PNM",      f"{pnm_ok} / {hfc_total}")

st.divider()

# ──────────────────────────────────────────────────────────────────────────────
# Tabs
# ──────────────────────────────────────────────────────────────────────────────

tab_exporte, tab_pnm, tab_axtract = st.tabs(["EXPORTE", "PNM raw", "AXTRACT raw"])

# ── Tab EXPORTE ───────────────────────────────────────────────────────────────
with tab_exporte:
    # Filtros propios de esta tab
    with st.expander("Filtros", expanded=True):
        sel_tec = []
        if "TECNOLOGIA" in df_exporte.columns:
            opts_tec = sorted(df_exporte["TECNOLOGIA"].dropna().unique().tolist())
            sel_tec = st.multiselect("Tecnologia", opts_tec, default=opts_tec, key="tec_exporte")

    # Aplicar filtro
    df = df_exporte.copy()
    if sel_tec and "TECNOLOGIA" in df.columns:
        df = df[df["TECNOLOGIA"].isin(sel_tec)]

    if df.empty:
        st.info("Sin resultados para los filtros seleccionados.")
    else:
        st.dataframe(apply_styles(df.reset_index(drop=True)), hide_index=True, height=600, width="stretch")

# ── Tab PNM raw ───────────────────────────────────────────────────────────────
with tab_pnm:
    if df_pnm.empty:
        st.info("No hay hoja PNM en el Excel (sin CMs HFC o se uso --skip-pnm).")
    else:
        with st.expander("Filtros", expanded=False):
            pnm_query = st.text_input("Buscar", placeholder="Texto en cualquier columna...", key="search_pnm")

        st.dataframe(_text_filter(df_pnm, pnm_query), hide_index=True, height=600, width="stretch")

# ── Tab AXTRACT raw ───────────────────────────────────────────────────────────
with tab_axtract:
    if df_axtract.empty:
        st.info("No hay hoja AXTRACT en el Excel (sin ONTs GPON o se uso --skip-axtract).")
    else:
        with st.expander("Filtros", expanded=False):
            ax_query = st.text_input("Buscar", placeholder="Texto en cualquier columna...", key="search_axtract")

        st.dataframe(_text_filter(df_axtract, ax_query), hide_index=True, height=600, width="stretch")
