"""
crucesmacros — Visor Streamlit (Fase 2)
Fase 1: visor con filtros y colores.
Fase 2: botones Generar + progreso en tiempo real + multi-usuario.
"""

import csv
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime
from glob import glob

import pandas as pd
import psutil
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

pd.set_option("styler.render.max_elements", 2_000_000)

OUTPUT_DIR    = os.getenv("OUTPUT_DIR", ".")
_BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
_WIN          = sys.platform == "win32"
_API_LOGIN    = os.getenv("AUTH_API_URL", "")
_LOCK_FILE    = os.path.join(_BASE_DIR, "active_query.json")
_ACTIVITY_LOG = os.path.join(_BASE_DIR, "activity_log.csv")

st.set_page_config(page_title="Crucesmacros — Visor", layout="wide")


# ── Pipeline control ──────────────────────────────────────────────────────────

def _is_running() -> bool:
    """True si el pipeline de esta sesión sigue vivo."""
    pid = st.session_state.get("pipeline_pid")
    if pid is None:
        return False
    if psutil.pid_exists(pid):
        return True
    # Proceso terminó — detectar Excel generado y limpiar estado
    _on_pipeline_finished()
    return False


def _on_pipeline_finished() -> None:
    start_str = st.session_state.get("pipeline_start", "")
    log_file  = st.session_state.get("pipeline_log", "")
    mode      = st.session_state.get("pipeline_mode", "")
    try:
        start_dt = datetime.fromisoformat(start_str)
        files = glob(os.path.join(OUTPUT_DIR, "Ingreso_Siebel_*.xlsx"))
        new_files = [f for f in files
                     if datetime.fromtimestamp(os.path.getmtime(f)) >= start_dt]
        if new_files:
            st.session_state["last_excel_path"] = max(new_files, key=os.path.getmtime)
    except Exception:
        pass
    # Registrar qué datos quedaron en el Excel
    if mode == "enrich_axtract":
        st.session_state["last_has_axtract"] = True
    elif mode == "enrich_pnm":
        st.session_state["last_has_pnm"] = True
    else:   
        skip_ax  = st.session_state.get("pipeline_skip_axtract", False)
        skip_pnm = st.session_state.get("pipeline_skip_pnm",     False)
        st.session_state["last_has_axtract"] = not skip_ax
        st.session_state["last_has_pnm"]     = not skip_pnm
    # Registrar actividad
    _global_lock_clear()
    try:
        start_dt2    = datetime.fromisoformat(start_str)
        duration_s   = (datetime.now() - start_dt2).total_seconds()
        _log_activity(st.session_state.get("username", ""), mode, duration_s)
    except Exception:
        pass
    # Limpiar log temporal
    if log_file and log_file != "crucesmacros.log":
        try:
            os.remove(log_file)
        except (FileNotFoundError, PermissionError):
            pass
    for key in ("pipeline_pid", "pipeline_start", "pipeline_mode",
                "pipeline_hours", "pipeline_log", "pipeline_enrich_file",
                "pipeline_skip_axtract", "pipeline_skip_pnm"):
        st.session_state.pop(key, None)
    _load_excel.clear()


def _kill_pipeline() -> None:
    """Mata el proceso pipeline y limpia session_state sin detectar Excel."""
    pid = st.session_state.get("pipeline_pid")
    if pid is not None:
        try:
            proc = psutil.Process(pid)
            proc.kill()
            proc.wait(timeout=5)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
            pass
    _global_lock_clear()
    log_file = st.session_state.get("pipeline_log", "")
    if log_file and log_file != "crucesmacros.log":
        try:
            os.remove(log_file)
        except (FileNotFoundError, PermissionError):
            pass
    for key in ("pipeline_pid", "pipeline_start", "pipeline_mode",
                "pipeline_hours", "pipeline_log", "pipeline_enrich_file",
                "pipeline_skip_axtract", "pipeline_skip_pnm"):
        st.session_state.pop(key, None)
    _load_excel.clear()


def _launch(mode: str, hours: int = 0,
            skip_axtract: bool = False, skip_pnm: bool = False) -> None:
    log_id   = uuid.uuid4().hex[:8]
    log_file = os.path.join(_BASE_DIR, f"crucesmacros_{log_id}.log")
    _username = st.session_state.get("username", "")
    cmd = [sys.executable, "main.py", "--mode", mode, "--limit", "0", "--log-file", log_file]
    if hours > 0:
        cmd += ["--hours", str(hours)]
    if _username:
        cmd += ["--user", _username]
    if skip_axtract:
        cmd.append("--skip-axtract")
    if skip_pnm:
        cmd.append("--skip-pnm")
    kwargs: dict = {"cwd": _BASE_DIR,
                    "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if _WIN:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, **kwargs)
    _global_lock_write(proc.pid, _username, mode)
    st.session_state.update({
        "pipeline_pid":           proc.pid,
        "pipeline_start":         datetime.now().isoformat(),
        "pipeline_mode":          mode,
        "pipeline_hours":         hours,
        "pipeline_log":           log_file,
        "pipeline_skip_axtract":  skip_axtract,
        "pipeline_skip_pnm":      skip_pnm,
    })


def _launch_enrich(which: str, file_path: str) -> None:
    log_id   = uuid.uuid4().hex[:8]
    log_file = os.path.join(_BASE_DIR, f"crucesmacros_{log_id}.log")
    cmd = [sys.executable, "main.py",
           "--enrich", which, "--file", file_path,
           "--log-file", log_file]
    kwargs: dict = {"cwd": _BASE_DIR,
                    "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if _WIN:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, **kwargs)
    st.session_state.update({
        "pipeline_pid":          proc.pid,
        "pipeline_start":        datetime.now().isoformat(),
        "pipeline_mode":         f"enrich_{which}",
        "pipeline_log":          log_file,
        "pipeline_enrich_file":  file_path,
    })


# ── Auth ─────────────────────────────────────────────────────────────────────

def _authenticate(username: str, password: str) -> bool:
    if not _API_LOGIN:
        return False
    try:
        r = requests.post(
            _API_LOGIN,
            json={"username": username, "password": password},
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def _login_page() -> None:
    st.title("Crucesmacros")
    st.markdown("Ingresa tus credenciales corporativas para continuar.")
    with st.form("login_form"):
        username = st.text_input("Usuario")
        password = st.text_input("Contraseña", type="password")
        submitted = st.form_submit_button("Ingresar", use_container_width=True, type="primary")
    if submitted:
        if not username or not password:
            st.error("Ingresa usuario y contraseña.")
        elif _authenticate(username, password):
            st.session_state["logged_in"] = True
            st.session_state["username"]  = username
            st.rerun()
        else:
            st.error("Credenciales incorrectas o sin acceso.")


# ── Global lock (bloqueo entre sesiones) ──────────────────────────────────────

def _global_lock_read() -> "dict | None":
    try:
        with open(_LOCK_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not psutil.pid_exists(data.get("pid", -1)):
            _global_lock_clear()
            return None
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _global_lock_write(pid: int, username: str, mode: str) -> None:
    try:
        with open(_LOCK_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "pid":        pid,
                "username":   username,
                "mode":       mode,
                "started_at": datetime.now().isoformat(),
            }, f)
    except OSError:
        pass


def _global_lock_clear() -> None:
    try:
        os.remove(_LOCK_FILE)
    except (FileNotFoundError, OSError):
        pass


# ── Activity log ──────────────────────────────────────────────────────────────

def _log_activity(username: str, mode: str, duration_s: float) -> None:
    row = {
        "fecha":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "usuario":     username,
        "modo":        mode,
        "duracion_s":  round(duration_s, 1),
    }
    file_exists = os.path.exists(_ACTIVITY_LOG)
    try:
        with open(_ACTIVITY_LOG, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
    except OSError:
        pass


# ── Progress parsing ──────────────────────────────────────────────────────────

_LOG_PATTERNS = [
    (re.compile(r"Conectando a Oracle"),
     "Conectando a Oracle..."),
    (re.compile(r"Cargando Excel"),
     "Cargando Excel existente..."),
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
_ORACLE_HINTS = [
    "Consultando la base de datos Oracle. Mantener la VPN activa...",
    "Oracle esta procesando la consulta. Esto puede tardar varios minutos...",
    "Espere, la base de datos esta leyendo los incidentes...",
    "En modo 24h la consulta puede tardar hasta 10 min. No cierre la ventana...",
]

def _make_pipeline_steps(skip_axtract: bool = False, skip_pnm: bool = False):
    """Genera lista de pasos y bounds de progreso según servicios seleccionados."""
    if not skip_axtract:
        after_consolidate = ["Cruzando datos GPON", "Cruzando datos HFC", "Generando"]
    elif not skip_pnm:
        after_consolidate = ["Cruzando datos HFC", "Generando"]
    else:
        after_consolidate = ["Generando"]

    steps = [
        ("Conectar a Oracle",
         ["Conectando"],
         ["Ejecutando", "Oracle:", "Consolidando", "Cruzando", "Generando"]),
        ("Ejecutar y leer datos Oracle",
         ["Ejecutando", "Leyendo"],
         ["Consolidando", "Cruzando", "Generando"]),
        ("Consolidar y preparar datos",
         ["Consolidando", "Incidentes", "Preparando", "Limpiando"],
         after_consolidate),
    ]
    if not skip_axtract:
        steps.append((
            "Cruzar datos GPON (Axtract)",
            ["Cruzando datos GPON"],
            ["Axtract completado", "Axtract omitido", "Cruzando datos HFC", "Generando"],
        ))
    if not skip_pnm:
        steps.append((
            "Cruzar datos HFC (PNM)",
            ["Cruzando datos HFC"],
            ["PNM completado", "PNM omitido", "Generando"],
        ))
    steps.append(("Generar Excel", ["Generando"], []))

    if not skip_axtract and not skip_pnm:
        bounds = [(0.0, 0.10), (0.10, 0.28), (0.28, 0.46),
                  (0.46, 0.70), (0.70, 0.90), (0.90, 0.97)]
    elif not skip_axtract or not skip_pnm:
        bounds = [(0.0, 0.10), (0.10, 0.28), (0.28, 0.46),
                  (0.46, 0.90), (0.90, 0.97)]
    else:
        bounds = [(0.0, 0.10), (0.10, 0.28), (0.28, 0.70), (0.70, 0.97)]

    return steps, bounds


# Pasos para enriquecimiento (Axtract o PNM)
_ENRICH_STEPS = {
    "axtract": [
        ("Cargar Excel existente",
         ["Cargando Excel"],
         ["Cruzando datos GPON", "Consultando Axtract"]),
        ("Consultar Axtract (GPON)",
         ["Cruzando datos GPON", "Consultando Axtract"],
         ["Axtract completado", "Generando"]),
        ("Actualizar Excel",
         ["Generando"],
         []),
    ],
    "pnm": [
        ("Cargar Excel existente",
         ["Cargando Excel"],
         ["Cruzando datos HFC", "Consultando PNM"]),
        ("Consultar PNM (HFC)",
         ["Cruzando datos HFC", "Consultando PNM"],
         ["PNM completado", "Generando"]),
        ("Actualizar Excel",
         ["Generando"],
         []),
    ],
}
_ENRICH_BOUNDS = [(0.0, 0.15), (0.15, 0.90), (0.90, 0.97)]


def _calc_progress(steps: list[str], steps_def, bounds) -> float:
    all_text = " ".join(steps)
    prog = 0.02
    for i, (_, cur_markers, done_markers) in enumerate(steps_def):
        lo, hi = bounds[i]
        if any(d in all_text for d in done_markers):
            prog = hi
        elif any(c in all_text for c in cur_markers):
            prog = max(prog, (lo + hi) / 2)
    return min(prog, 0.97)


def _render_stepper(steps: list[str], steps_def) -> None:
    all_text = " ".join(steps)
    lines = []
    for label, cur_markers, done_markers in steps_def:
        is_done    = any(d in all_text for d in done_markers)
        is_current = not is_done and any(c in all_text for c in cur_markers)
        if is_done:
            icon, color, weight = "&#10003;", "#2E7D32", "600"
        elif is_current:
            icon, color, weight = "&#9654;", "#1565C0", "700"
        else:
            icon, color, weight = "&#9675;", "#9E9E9E", "400"
        lines.append(
            f'<div style="margin:5px 0;color:{color};font-size:1rem;font-weight:{weight};">'
            f'{icon}&nbsp;&nbsp;{label}</div>'
        )
    st.markdown("\n".join(lines), unsafe_allow_html=True)


def _get_progress() -> list[str]:
    """Parsea el log de esta sesión y retorna pasos amigables."""
    log_file = st.session_state.get("pipeline_log", "")
    if not log_file or not os.path.exists(log_file):
        return []
    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 500_000))
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


# ── Confirmation dialogs ──────────────────────────────────────────────────────

@st.dialog("Confirmar generacion de reporte")
def _confirm_dialog():
    mode  = st.session_state.get("confirm_mode", "all")
    hours = int(st.session_state.get("confirm_hours", 10)) if mode == "custom" else 0
    desc = {
        "custom": f"Consulta personalizada — ultimas **{hours}** horas",
        "10h":    "Ultimas 10 horas — incidentes actualizados en las ultimas 10 h",
        "24h":    "Ultimas 24 horas — incidentes actualizados en las ultimas 24 h",
        "all":    "Consulta general — **todos** los incidentes activos (~15 min)",
    }
    st.markdown(desc.get(mode, mode))
    st.divider()
    st.markdown("**Enriquecimiento de datos** *(puede hacerse despues si lo omites ahora)*")
    inc_axtract = st.checkbox("Incluir Axtract — GPON (~8 min)", value=True, key="dlg_axtract")
    inc_pnm     = st.checkbox("Incluir PNM — HFC (~4 min)",     value=True, key="dlg_pnm")
    st.divider()
    st.error("Mientras se genera el reporte no podras realizar ninguna accion en esta sesion.")
    _already = st.session_state.get("pipeline_pid") is not None
    c1, c2 = st.columns(2)
    if not inc_axtract and not inc_pnm:
        st.error("Debes seleccionar al menos una tecnologia (GPON o HFC) para generar el reporte.")
    with c1:
        if st.button("Confirmar", type="primary", use_container_width=True,
                     disabled=_already or (not inc_axtract and not inc_pnm)):
            _launch(mode, hours=hours,
                    skip_axtract=not inc_axtract,
                    skip_pnm=not inc_pnm)
            st.session_state.pop("confirm_mode",  None)
            st.session_state.pop("confirm_hours", None)
            st.rerun(scope="app")
    with c2:
        if st.button("Cancelar", use_container_width=True, disabled=_already):
            st.session_state.pop("confirm_mode",  None)
            st.session_state.pop("confirm_hours", None)
            st.rerun(scope="app")


@st.dialog("Confirmar enriquecimiento")
def _confirm_enrich_dialog():
    which = st.session_state.get("enrich_mode", "")
    fpath = st.session_state.get("enrich_file", "")
    if which == "axtract":
        label = "GPON con Axtract (~8 min)"
    else:
        label = "HFC con PNM (~4 min)"
    st.markdown(f"**{label}**")
    st.markdown(f"Archivo: `{os.path.basename(fpath)}`")
    st.warning("El Excel existente sera actualizado con los datos nuevos.")
    _already = st.session_state.get("pipeline_pid") is not None
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Confirmar", type="primary", use_container_width=True, disabled=_already):
            _launch_enrich(which, fpath)
            st.session_state.pop("enrich_mode", None)
            st.session_state.pop("enrich_file", None)
            st.rerun(scope="app")
    with c2:
        if st.button("Cancelar", use_container_width=True, disabled=_already):
            st.session_state.pop("enrich_mode", None)
            st.session_state.pop("enrich_file", None)
            st.rerun(scope="app")


# ── Excel loader ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def _load_excel(output_dir: str):
    files = sorted(glob(os.path.join(output_dir, "Ingreso_Siebel_*.xlsx")),
                   key=os.path.getmtime, reverse=True)
    if not files:
        return None, None
    for path in files:
        try:
            return pd.read_excel(path, sheet_name=None, engine="openpyxl"), path
        except Exception:
            continue
    return None, None


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
    float_cols = df.select_dtypes(include="float").columns.tolist()
    if float_cols:
        styler = styler.format({col: "{:.3f}" for col in float_cols}, na_rep="")
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


# ── Guard de sesión ───────────────────────────────────────────────────────────

if not st.session_state.get("logged_in"):
    _login_page()
    st.stop()

# ── Estado de la sesión ───────────────────────────────────────────────────────

running     = _is_running()
global_lock = _global_lock_read()
_other_running = (
    global_lock is not None
    and global_lock.get("pid") != st.session_state.get("pipeline_pid")
)

# Si ya hay pipeline activo, limpiar cualquier estado de diálogo pendiente
# para evitar que el diálogo reaparezca en el siguiente rerun
if running:
    st.session_state.pop("confirm_mode",  None)
    st.session_state.pop("confirm_limit", None)
    st.session_state.pop("enrich_mode",   None)
    st.session_state.pop("enrich_file",   None)

sheets, excel_path = _load_excel(OUTPUT_DIR)

# Excel target para enriquecimiento: priorizar el generado en esta sesión
_session_excel = st.session_state.get("last_excel_path")
_target_excel  = (_session_excel
                  if _session_excel and os.path.exists(_session_excel)
                  else excel_path)

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    _logged_user = st.session_state.get("username", "")
    st.caption(f"Usuario: **{_logged_user}**")
    st.subheader("Generar reporte")

    if running:
        info  = st.session_state
        mode  = info.get("pipeline_mode", "")
        _hrs  = info.get("pipeline_hours", 0)
        mlbl  = {"all": "General", "10h": "Ultimas 10h", "24h": "Ultimas 24h",
                 "custom": f"Personalizada ({_hrs}h)" if _hrs else "Personalizada",
                 "enrich_axtract": "Enriq. GPON", "enrich_pnm": "Enriq. HFC"}.get(mode, mode)
        llbl  = f"{_hrs}h" if mode == "custom" and _hrs else ""
        msg   = f"**Modo:** {mlbl}"
        if llbl:
            msg += f"  \n**Limite:** {llbl}"
        if mode not in ("enrich_axtract", "enrich_pnm"):
            skip_ax  = info.get("pipeline_skip_axtract", False)
            skip_pnm = info.get("pipeline_skip_pnm",     False)
            if not skip_ax and not skip_pnm:
                datos_lbl = "GPON + HFC"
            elif not skip_ax:
                datos_lbl = "Solo GPON (Axtract)"
            elif not skip_pnm:
                datos_lbl = "Solo HFC (PNM)"
            else:
                datos_lbl = "Sin enriquecimiento"
            msg += f"  \n**Datos:** {datos_lbl}"
        st.info(f"Pipeline en ejecucion  \n{msg}")
        if st.button("Detener cruce", type="primary", use_container_width=True, key="_stop_sidebar"):
            _kill_pipeline()
            st.rerun(scope="app")
        st.number_input("Horas", value=10, disabled=True, key="_dlimit")
        st.button("Consulta personalizada", disabled=True, key="_db1")
        st.button("Ultimas 10 horas",       disabled=True, key="_db2")
        st.button("Ultimas 24 horas",       disabled=True, key="_db3")
        st.button("Consulta general",        disabled=True, key="_db4")
    elif _other_running:
        _lock_user = global_lock.get("username", "Otro usuario")
        _lock_mode = {"all": "General", "10h": "10h", "24h": "24h",
                      "custom": "Personalizada"}.get(global_lock.get("mode", ""), "—")
        st.warning(f"**{_lock_user}** esta ejecutando una consulta ({_lock_mode}).  \nPor favor espera.")
        st.number_input("Horas", value=10, disabled=True, key="_dlimit")
        st.button("Consulta personalizada", disabled=True, key="_db1")
        st.button("Ultimas 10 horas",       disabled=True, key="_db2")
        st.button("Ultimas 24 horas",       disabled=True, key="_db3")
        st.button("Consulta general",       disabled=True, key="_db4")
    else:
        st.number_input(
            "Horas",
            min_value=1, max_value=24,
            value=10, step=1,
            key="sidebar_hours",
            help="Solo aplica al boton 'Consulta personalizada'.",
        )
        if st.button("Consulta personalizada", use_container_width=True, key="btn_custom"):
            st.session_state.confirm_mode  = "custom"
            st.session_state.confirm_hours = int(st.session_state.sidebar_hours)
        if st.button("Ultimas 10 horas", use_container_width=True, key="btn_10h"):
            st.session_state.confirm_mode  = "10h"
            st.session_state.confirm_limit = 0
        if st.button("Ultimas 24 horas", use_container_width=True, key="btn_24h"):
            st.session_state.confirm_mode  = "24h"
            st.session_state.confirm_limit = 0
        if st.button("Consulta general", use_container_width=True, key="btn_all"):
            st.session_state.confirm_mode  = "all"
            st.session_state.confirm_limit = 0

    st.divider()

    if excel_path:
        mod_dt = datetime.fromtimestamp(os.path.getmtime(excel_path))
        st.caption(f"Archivo: **{os.path.basename(excel_path)}**")
        st.caption(f"Generado: {mod_dt.strftime('%d/%m/%Y %H:%M')}")
        _meta_df = sheets.get("META", pd.DataFrame()) if sheets else pd.DataFrame()
        _meta = {}
        if not _meta_df.empty and "clave" in _meta_df.columns:
            _meta = _meta_df.set_index("clave")["valor"].to_dict()
        _modo_lbl = {"all": "General", "10h": "10h", "24h": "24h",
                     "custom": "Personalizada"}.get(_meta.get("modo", ""), _meta.get("modo", "—") or "—")
        _user_lbl = _meta.get("usuario") or "—"
        st.caption(f"Consulta: **{_modo_lbl}**")
        st.caption(f"Usuario: **{_user_lbl}**")
        if not running:
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

    # Botones de enriquecimiento — solo si hay Excel, no carga, y el servicio fue omitido
    if _target_excel and os.path.exists(_target_excel) and not running:
        show_gpon = not st.session_state.get("last_has_axtract", False)
        show_hfc  = not st.session_state.get("last_has_pnm",     False)
        if show_gpon or show_hfc:
            st.divider()
            st.caption("Enriquecer reporte:")
            if show_gpon and show_hfc:
                col_a, col_p = st.columns(2)
                with col_a:
                    if st.button("GPON\n(Axtract)", use_container_width=True, key="btn_enrich_axtract"):
                        st.session_state["enrich_mode"] = "axtract"
                        st.session_state["enrich_file"] = _target_excel
                with col_p:
                    if st.button("HFC\n(PNM)", use_container_width=True, key="btn_enrich_pnm"):
                        st.session_state["enrich_mode"] = "pnm"
                        st.session_state["enrich_file"] = _target_excel
            elif show_gpon:
                if st.button("Enriquecer GPON (Axtract)", use_container_width=True, key="btn_enrich_axtract"):
                    st.session_state["enrich_mode"] = "axtract"
                    st.session_state["enrich_file"] = _target_excel
            else:
                if st.button("Enriquecer HFC (PNM)", use_container_width=True, key="btn_enrich_pnm"):
                    st.session_state["enrich_mode"] = "pnm"
                    st.session_state["enrich_file"] = _target_excel

    st.divider()
    if st.button("Cerrar sesion", use_container_width=True, key="btn_logout"):
        st.session_state.clear()
        st.rerun()


# Dialogs fuera del sidebar para que se rendericen a nivel de pagina
if not running and st.session_state.get("confirm_mode"):
    _confirm_dialog()
elif not running and st.session_state.get("enrich_mode"):
    _confirm_enrich_dialog()


# ── Area principal ────────────────────────────────────────────────────────────

if running:
    pipeline_mode = st.session_state.get("pipeline_mode", "")

    # Título estático — solo se re-renderiza en full rerun
    if pipeline_mode == "enrich_axtract":
        st.title("Crucesmacros — Enriqueciendo GPON (Axtract)")
        steps_def = _ENRICH_STEPS["axtract"]
        bounds    = _ENRICH_BOUNDS
    elif pipeline_mode == "enrich_pnm":
        st.title("Crucesmacros — Enriqueciendo HFC (PNM)")
        steps_def = _ENRICH_STEPS["pnm"]
        bounds    = _ENRICH_BOUNDS
    else:
        st.title("Crucesmacros — Generando reporte")
        _skip_ax  = st.session_state.get("pipeline_skip_axtract", False)
        _skip_pnm = st.session_state.get("pipeline_skip_pnm",     False)
        steps_def, bounds = _make_pipeline_steps(_skip_ax, _skip_pnm)

    # Área de progreso: se auto-refresca cada 3 s SIN tocar el sidebar
    @st.fragment(run_every=3)
    def _progress_area():
        pid = st.session_state.get("pipeline_pid")
        if pid is None or not psutil.pid_exists(pid):
            _on_pipeline_finished()
            st.rerun(scope="app")
            return

        if st.button("Detener cruce", type="primary", key="_stop_frag"):
            _kill_pipeline()
            st.rerun(scope="app")
            return

        start_str = st.session_state.get("pipeline_start", "")
        elapsed = 0.0
        if start_str:
            try:
                elapsed = (datetime.now() - datetime.fromisoformat(start_str)).total_seconds()
            except Exception:
                pass
        m_el, s_el = divmod(int(elapsed), 60)
        st.caption(f"En ejecucion hace {m_el}m {s_el}s")

        try:
            steps   = _get_progress()
            current = steps[-1] if steps else "Iniciando..."

            prog = _calc_progress(steps, steps_def, bounds)
            st.progress(prog, text=f"{int(prog * 100)}% completado")

            _is_done = any(w in current for w in ("completado", "omitido", "Sin incidentes"))
            if _is_done:
                st.success(current)
            else:
                st.info(current)

            hint = ""
            if "Ejecutando" in current:
                hint = _ORACLE_HINTS[int(elapsed / 15) % len(_ORACLE_HINTS)]
            elif "Axtract" in current and "completado" not in current:
                hint = _AXTRACT_HINTS[int(elapsed / 15) % len(_AXTRACT_HINTS)]
            elif "PNM" in current and "completado" not in current:
                hint = _PNM_HINTS[int(elapsed / 15) % len(_PNM_HINTS)]
            if hint:
                st.markdown(
                    f'<p style="color:#888;font-size:0.95rem;margin:2px 0 12px 0;">{hint}</p>',
                    unsafe_allow_html=True,
                )

            st.divider()
            _render_stepper(steps, steps_def)

        except Exception as e:
            st.warning(f"Error al leer progreso: {e}")

    _progress_area()

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
