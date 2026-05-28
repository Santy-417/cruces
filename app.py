"""
crucesmacros — Visor Streamlit (Fase 2)
Fase 1: visor con filtros y colores.
Fase 2: botones Generar + lock file + progreso en tiempo real.
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from glob import glob

import pandas as pd
import psutil
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR = os.getenv("OUTPUT_DIR", ".")
_BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
LOCK_FILE  = os.path.join(_BASE_DIR, ".pipeline.lock")
LOG_FILE   = os.path.join(_BASE_DIR, "crucesmacros.log")
_WIN       = sys.platform == "win32"

st.set_page_config(page_title="Crucesmacros — Visor", layout="wide")


# ── Pipeline control ──────────────────────────────────────────────────────────

def _is_running():
    """(running: bool, lock_info: dict | None). Stale si PID muerto."""
    if not os.path.exists(LOCK_FILE):
        return False, None
    try:
        with open(LOCK_FILE, encoding="utf-8") as f:
            info = json.load(f)
        return psutil.pid_exists(info.get("pid", -1)), info
    except Exception:
        return False, None


def _clear_lock():
    try:
        os.remove(LOCK_FILE)
    except FileNotFoundError:
        pass


def _launch(mode: str, limit: int) -> None:
    actual_mode = "all" if mode == "custom" else mode
    cmd = [sys.executable, "main.py", "--limit", str(limit), "--mode", actual_mode]
    kwargs: dict = {"cwd": _BASE_DIR, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if _WIN:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, **kwargs)
    with open(LOCK_FILE, "w", encoding="utf-8") as f:
        json.dump({"pid": proc.pid, "start": datetime.now().isoformat(),
                   "mode": mode, "limit": limit}, f)


# ── Progress parsing ──────────────────────────────────────────────────────────

_LOG_PATTERNS = [
    (re.compile(r"Conectando a Oracle"),
     "Conectando a Oracle..."),
    (re.compile(r"Ejecutando query.*modo=(\w+).*limite=(\S+)"),
     lambda m: f"Ejecutando consulta (modo: {m.group(1)}, limite: {m.group(2)})..."),
    (re.compile(r"Oracle respondio"),
     "Leyendo datos de Oracle..."),
    (re.compile(r"(\d+) filas recibidas\.\.\."),
     lambda m: f"Leyendo datos... {int(m.group(1)):,} filas"),
    (re.compile(r"(\d+) filas recibidas en ([\d.]+) segundos"),
     lambda m: f"Oracle: {int(m.group(1)):,} filas en {m.group(2)}s"),
    (re.compile(r"La query no devolvio"),
     "Sin resultados en Oracle."),
    (re.compile(r"Consolidando por incidente"),
     "Consolidando incidentes..."),
    (re.compile(r"Incidentes unicos procesados: (\d+)"),
     lambda m: f"Incidentes unicos: {int(m.group(1)):,}"),
    (re.compile(r"Mapeando al schema"),
     "Preparando esquema de exportacion..."),
    (re.compile(r"Limpiando IDs"),
     "Limpiando IDs de infraestructura..."),
    (re.compile(r"Consultando Axtract"),
     "Cruzando datos GPON (Axtract)..."),
    (re.compile(r"Axtract: (\d+) CPEs"),
     lambda m: f"Axtract completado: {int(m.group(1)):,} CPEs encontrados"),
    (re.compile(r"Faltan vars Axtract"),
     "Axtract omitido."),
    (re.compile(r"Consultando PNM"),
     "Cruzando datos HFC (PNM)..."),
    (re.compile(r"PNM: (\d+) CMs"),
     lambda m: f"PNM completado: {int(m.group(1)):,} CMs encontrados"),
    (re.compile(r"Faltan vars PNM"),
     "PNM omitido."),
    (re.compile(r"Excel generado"),
     "Generando Excel..."),
    (re.compile(r"No se encontraron incidentes"),
     "Sin incidentes activos."),
]

_AXTRACT_HINTS = [
    "Consultando la API por cada equipo GPON. Un momento...",
    "Haciendo cruce de datos con Axtract. Esto puede tardar varios minutos...",
    "Procesando informacion de fibra optica. Por favor espere...",
    "Estoy procesando la informacion de cada ONT. No cierre la ventana...",
]
_PNM_HINTS = [
    "Consultando la API por cada modem HFC. Un momento...",
    "Haciendo cruce de datos con PNM. Esto puede tardar unos minutos...",
    "Procesando informacion de la red coaxial. Por favor espere...",
    "Estoy procesando la informacion de cada CM. No cierre la ventana...",
]

_MILESTONES = [
    "Conectando", "Ejecutando", "Leyendo datos", "Oracle:",
    "Consolidando", "Incidentes", "Preparando", "Limpiando",
    "Axtract", "Axtract completado", "PNM", "PNM completado", "Generando",
]


def _get_progress():
    """Parsea crucesmacros.log y retorna pasos amigables de la ejecucion actual."""
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
            raw = f.readlines()
    except Exception:
        return []
    start = 0
    for i, line in enumerate(raw):
        if "Conectando a Oracle" in line:
            start = i
    steps = []
    for line in raw[start:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 3)
        if len(parts) < 4 or parts[2] not in ("INFO", "WARNING", "ERROR"):
            continue
        msg = parts[3]
        for pat, rep in _LOG_PATTERNS:
            m = pat.search(msg)
            if m:
                steps.append(rep(m) if callable(rep) else rep)
                break
    return steps


# ── Confirmation dialog ───────────────────────────────────────────────────────

@st.dialog("Confirmar generacion de reporte")
def _confirm_dialog():
    mode  = st.session_state.get("confirm_mode",  "all")
    limit = st.session_state.get("confirm_limit", 1000)
    desc = {
        "custom": f"Consulta personalizada — primeras **{limit:,}** filas activas",
        "24h":    "Ultimas 24 horas — incidentes actualizados en las ultimas 24 h",
        "all":    "Consulta general — **todos** los incidentes activos (~15 min)",
    }
    st.markdown(desc.get(mode, mode))
    st.warning("El proceso incluye Oracle + Axtract + PNM. "
               "Nadie mas podra generar mientras este en curso.")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Confirmar", type="primary", use_container_width=True):
            _launch(mode, limit)
            st.session_state.pop("confirm_mode",  None)
            st.session_state.pop("confirm_limit", None)
            st.rerun()
    with c2:
        if st.button("Cancelar", use_container_width=True):
            st.session_state.pop("confirm_mode",  None)
            st.session_state.pop("confirm_limit", None)
            st.rerun()


# ── Excel loader ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def _load_excel(output_dir: str):
    files = sorted(glob(os.path.join(output_dir, "Ingreso_Siebel_*.xlsx")),
                   key=os.path.getmtime, reverse=True)
    if not files:
        return None, None
    latest = files[0]
    return pd.read_excel(latest, sheet_name=None, engine="openpyxl"), latest


# ── Styles ────────────────────────────────────────────────────────────────────

_G = "background-color: #92D050"
_Y = "background-color: #FFFF00"
_R = "background-color: #FF0000; color: white"


def _num(val):
    if pd.isna(val) or str(val).strip() == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _color_status(val):
    s = str(val).strip() if not pd.isna(val) else ""
    return ("" if not s
            else (_G if s == "operational"
                  else (_Y if s == "rangingAutoAdjComplete" else _R)))

def _color_dw_snr(val):
    v = _num(val)
    return "" if v is None else (_G if v > 37 else (_Y if v >= 35 else _R))

def _color_pl_dw(val):
    v = _num(val)
    if v is None: return ""
    return _G if -10 <= v <= 12 else (_Y if -15 <= v < -10 else _R)

def _color_up_snr(val):
    v = _num(val)
    return "" if v is None else (_G if v > 29 else (_Y if v >= 27 else _R))

def _color_pl_up(val):
    v = _num(val)
    if v is None: return ""
    return _G if 38 <= v <= 47.9 else (_Y if 48 <= v <= 50.9 else _R)

def _color_ont_status(val):
    if pd.isna(val) or not str(val).strip(): return ""
    return _G if str(val).strip() == "up" else _R

def _color_tx_power(val):
    v = _num(val)
    return "" if v is None else (_G if 0.5 <= v <= 5 else _R)

def _color_rx_power(val):
    v = _num(val)
    return "" if v is None else (_G if -27 <= v <= -10 else _R)


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


def _apply_styles(df: pd.DataFrame):
    styler = df.style
    for col, fn in _COL_STYLERS.items():
        if col in df.columns:
            styler = styler.map(fn, subset=[col])
    return styler


def _text_filter(df: pd.DataFrame, q: str) -> pd.DataFrame:
    if not q.strip():
        return df
    ql = q.strip().lower()
    mask = df.apply(
        lambda c: c.astype(str).str.lower().str.contains(ql, na=False)
    ).any(axis=1)
    return df[mask]


# ── Estado de la app ──────────────────────────────────────────────────────────

running, lock_info = _is_running()

if not running and lock_info is not None:
    # Lock residual — proceso termino; limpiar y forzar recarga de Excel
    _clear_lock()
    _load_excel.clear()
    lock_info = None

sheets, excel_path = _load_excel(OUTPUT_DIR)

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.subheader("Generar reporte")

    if running:
        info  = lock_info or {}
        mlbl  = {"all": "General", "24h": "Ultimas 24h",
                 "custom": "Personalizada"}.get(info.get("mode", ""), "")
        llbl  = (f"{info.get('limit', 0):,} filas"
                 if info.get("limit", 0) > 0 else "sin limite")
        st.info(f"Pipeline en ejecucion  \n**Modo:** {mlbl}  \n**Limite:** {llbl}")
        st.number_input("Cantidad de filas", value=1000, disabled=True, key="_dlimit")
        st.button("Consulta personalizada", disabled=True, key="_db1")
        st.button("Ultimas 24 horas",       disabled=True, key="_db2")
        st.button("Consulta general",        disabled=True, key="_db3")
    else:
        limit_val = st.number_input(
            "Cantidad de filas",
            min_value=1, max_value=50_000,
            value=1_000, step=500,
            help="Solo aplica al boton 'Consulta personalizada'.",
        )
        if st.button("Consulta personalizada", use_container_width=True):
            st.session_state.confirm_mode  = "custom"
            st.session_state.confirm_limit = int(limit_val)
        if st.button("Ultimas 24 horas", use_container_width=True):
            st.session_state.confirm_mode  = "24h"
            st.session_state.confirm_limit = 0
        if st.button("Consulta general", use_container_width=True):
            st.session_state.confirm_mode  = "all"
            st.session_state.confirm_limit = 0

    st.divider()

    if excel_path:
        mod_dt = datetime.fromtimestamp(os.path.getmtime(excel_path))
        st.caption(f"Archivo: **{os.path.basename(excel_path)}**")
        st.caption(f"Generado: {mod_dt.strftime('%d/%m/%Y %H:%M')}")
        st.divider()
        with open(excel_path, "rb") as fh:
            st.download_button(
                "Descargar Excel", fh,
                file_name=os.path.basename(excel_path),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch",
            )
    else:
        st.caption("Sin archivo generado aun.")

# Dialog fuera del sidebar para que se renderice a nivel de pagina
if not running and st.session_state.get("confirm_mode"):
    _confirm_dialog()

# ── Area principal ────────────────────────────────────────────────────────────

if running:
    info      = lock_info or {}
    start_str = info.get("start", "")
    elapsed   = 0.0
    if start_str:
        try:
            elapsed = (datetime.now() - datetime.fromisoformat(start_str)).total_seconds()
        except Exception:
            pass
    m_el, s_el = divmod(int(elapsed), 60)

    st.title("Crucesmacros — Generando reporte")
    st.caption(f"En ejecucion hace {m_el}m {s_el}s")

    steps   = _get_progress()
    current = steps[-1] if steps else "Iniciando..."

    # Mensaje rotativo durante pasos largos (Axtract ~8 min, PNM ~4 min)
    hint = ""
    if "Axtract" in current and "completado" not in current:
        hint = _AXTRACT_HINTS[int(elapsed / 15) % len(_AXTRACT_HINTS)]
    elif "PNM" in current and "completado" not in current:
        hint = _PNM_HINTS[int(elapsed / 15) % len(_PNM_HINTS)]

    with st.container(border=True):
        st.markdown(f"**{current}**")
        if hint:
            st.info(hint)
        if len(steps) > 1:
            st.divider()
            for s in steps[:-1]:
                st.markdown(f"- {s}")
        st.divider()
        st.caption("No cierre esta ventana. El proceso continua en segundo plano.")

    prog = min(
        sum(1 for s in steps if any(k in s for k in _MILESTONES)) / len(_MILESTONES),
        0.97,
    )
    st.progress(prog, text=f"{int(prog * 100)}% completado")

    time.sleep(3)
    st.rerun()

else:
    # ── Visor normal ──────────────────────────────────────────────────────────

    if sheets is None:
        st.title("Crucesmacros — Visor")
        st.warning(f"No se encontro ningun archivo Excel en `{OUTPUT_DIR}`.")
        st.info("Usa el panel izquierdo para generar el reporte.")
        st.stop()

    df_exporte = sheets.get("EXPORTE", pd.DataFrame())
    df_pnm     = sheets.get("PNM",     pd.DataFrame())
    df_axtract = sheets.get("AXTRACT", pd.DataFrame())

    st.title("Crucesmacros — Incidentes Siebel")

    total     = len(df_exporte)
    hfc_mask  = (df_exporte["TECNOLOGIA"].str.contains("COAXIAL", case=False, na=False)
                 if "TECNOLOGIA" in df_exporte.columns
                 else pd.Series(False, index=df_exporte.index))
    gpon_mask = (df_exporte["TECNOLOGIA"].str.contains("GPON|FIBRA|FTTH", case=False, na=False)
                 if "TECNOLOGIA" in df_exporte.columns
                 else pd.Series(False, index=df_exporte.index))
    hfc_total  = int(hfc_mask.sum())
    gpon_total = int(gpon_mask.sum())
    pnm_ok     = (int((df_exporte.loc[hfc_mask, "Status"].notna() &
                        df_exporte.loc[hfc_mask, "Status"].astype(str).str.strip().ne("")).sum())
                  if "Status" in df_exporte.columns and hfc_total > 0 else 0)
    axtract_ok = (int((df_exporte.loc[gpon_mask, "ONT Status"].notna() &
                        df_exporte.loc[gpon_mask, "ONT Status"].astype(str).str.strip().ne("")).sum())
                  if "ONT Status" in df_exporte.columns and gpon_total > 0 else 0)

    c1, c2, c3 = st.columns(3)
    c1.metric("Total incidentes", total)
    c2.metric("GPON con Axtract", f"{axtract_ok} / {gpon_total}")
    c3.metric("HFC con PNM",      f"{pnm_ok} / {hfc_total}")

    st.divider()

    tab_e, tab_p, tab_a = st.tabs(["EXPORTE", "PNM raw", "AXTRACT raw"])

    with tab_e:
        with st.expander("Filtros", expanded=True):
            sel_tec = []
            if "TECNOLOGIA" in df_exporte.columns:
                opts = sorted(df_exporte["TECNOLOGIA"].dropna().unique().tolist())
                sel_tec = st.multiselect("Tecnologia", opts, default=opts, key="tec_exporte")
        df = df_exporte.copy()
        if sel_tec and "TECNOLOGIA" in df.columns:
            df = df[df["TECNOLOGIA"].isin(sel_tec)]
        if df.empty:
            st.info("Sin resultados.")
        else:
            st.dataframe(_apply_styles(df.reset_index(drop=True)),
                         hide_index=True, height=600, width="stretch")

    with tab_p:
        if df_pnm.empty:
            st.info("No hay hoja PNM en el Excel.")
        else:
            with st.expander("Filtros", expanded=False):
                pq = st.text_input("Buscar", placeholder="Texto en cualquier columna...",
                                   key="search_pnm")
            st.dataframe(_text_filter(df_pnm, pq), hide_index=True, height=600, width="stretch")

    with tab_a:
        if df_axtract.empty:
            st.info("No hay hoja AXTRACT en el Excel.")
        else:
            with st.expander("Filtros", expanded=False):
                aq = st.text_input("Buscar", placeholder="Texto en cualquier columna...",
                                   key="search_axtract")
            st.dataframe(_text_filter(df_axtract, aq), hide_index=True, height=600, width="stretch")
