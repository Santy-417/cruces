import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
from requests.auth import HTTPBasicAuth

log = logging.getLogger(__name__)

AXTRACT_COLUMNS = [
    "AXTRACT_ONT_STATUS",
    "AXTRACT_TX_POWER",
    "AXTRACT_RX_POWER",
    "AXTRACT_RX_OLT_POWER",
    "AXTRACT_RANGING",
    "AXTRACT_SFP_TYPE",
    "AXTRACT_FTTX_TIME",
    "AXTRACT_ALARM_CODE",
    "AXTRACT_CMTS",
    "AXTRACT_CMTS_UP",
    "AXTRACT_ARPON",
    "AXTRACT_SPLITTER",
    "AXTRACT_NAP",
    "AXTRACT_PUERTO_NAP",
    "AXTRACT_TRANSCEIVER_TEMP",
]

_AXTRACT_FIELDS = '["cpeid","mode_props","metadata"]'
_BATCH_SIZE = 50
_MAX_WORKERS = 5


def _parse_cdata(raw: str) -> list:
    inner = re.sub(r"^<!\[CDATA\[|\]\]>$", "", raw.strip())
    try:
        return json.loads(inner)
    except json.JSONDecodeError:
        return []


def _extract_fields(record: dict) -> dict:
    mp            = record.get("mode_props") or {}
    fttx          = (mp.get("fttx") or {}).get("fttx") or {}
    meta          = record.get("metadata") or {}
    ont           = meta.get("ont") or {}
    olt           = meta.get("olt") or {}
    topo          = meta.get("topo") or {}
    fttx_olt_info = (mp.get("fttx_olt") or {}).get("device_info") or {}

    line_card = olt.get("line_card")
    port      = olt.get("port")
    cmts_up   = f"{line_card}/{port}" if line_card and port else None

    fttx_time = mp.get("fttx_time")
    fttx_time_clean = (fttx_time.split(".")[0]
                       if isinstance(fttx_time, str) and "." in fttx_time
                       else fttx_time)

    return {
        "AXTRACT_ONT_STATUS":       ont.get("oper_status"),
        "AXTRACT_TX_POWER":         fttx.get("tx_power"),
        "AXTRACT_RX_POWER":         fttx.get("rx_power"),
        "AXTRACT_RX_OLT_POWER":     fttx.get("rx_olt_power"),
        "AXTRACT_RANGING":          ont.get("ranging"),
        "AXTRACT_SFP_TYPE":         ont.get("if_sfp"),
        "AXTRACT_FTTX_TIME":        fttx_time_clean,
        "AXTRACT_ALARM_CODE":       fttx_olt_info.get("last_down_cause"),
        "AXTRACT_CMTS":             olt.get("id"),
        "AXTRACT_CMTS_UP":          cmts_up,
        "AXTRACT_ARPON":            topo.get("arpon"),
        "AXTRACT_SPLITTER":         topo.get("splitter"),
        "AXTRACT_NAP":              topo.get("nap"),
        "AXTRACT_PUERTO_NAP":       topo.get("puerto_nap"),
        "AXTRACT_TRANSCEIVER_TEMP": fttx.get("transceiver_temperature"),
        "REFERENCIA":               ont.get("equipment_id"),
    }


_MAC_RAW_RE = re.compile(r'^[0-9A-Fa-f]{12}$')


def _is_gpon_cpeid(val) -> bool:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return False
    s = str(val).strip()
    if not s or ":" in s:
        return False
    return not _MAC_RAW_RE.match(s)


def _match_serial(record: dict, serial_set: set) -> str | None:
    """Busca qué serial de entrada corresponde a un record devuelto por la API."""
    sn_raw = ((record.get("metadata") or {}).get("ont") or {}).get("sn_raw", "")
    if sn_raw in serial_set:
        return sn_raw
    cpeid = record.get("cpeid", "")
    for s in serial_set:
        if s and (s in cpeid or cpeid in s):
            return s
    return None


def query_batch_axtract(
    session: requests.Session, url: str, serials: list, timeout: int = 30
) -> dict:
    """Consulta hasta _BATCH_SIZE seriales ONT en una sola request.
    Retorna {serial_input → record}."""
    or_clauses = []
    for s in serials:
        or_clauses.append({"cpeid": {"$regex": s}})
        or_clauses.append({"metadata.ont.sn_raw": s})
    body = {
        "args": {
            "store_name": "cpe_store",
            "query": json.dumps({"$or": or_clauses}),
            "sort": '[["last_update", -1]]',
            "fields": _AXTRACT_FIELDS,
            "limit": len(serials) * 2,
            "format": "json",
            "target": "",
        }
    }
    serial_set = set(serials)
    try:
        resp = session.post(url, json=body, timeout=timeout)
        resp.raise_for_status()
        data = resp.json().get("return", {}).get("data", "")
        records = _parse_cdata(data)
        result = {}
        for record in records:
            key = _match_serial(record, serial_set)
            if key and key not in result:
                result[key] = record
        return result
    except requests.exceptions.Timeout:
        log.warning("Axtract timeout para lote de %d seriales", len(serials))
    except Exception as exc:
        log.warning("Axtract error en lote: %s", exc)
    return {}


def _run_batch_axtract(
    batch_serials: list, url: str, user: str, password: str
) -> tuple[dict, list]:
    """Ejecuta un lote Axtract en su propio hilo con su propia session.
    Retorna (local_results, local_rows)."""
    local_results: dict = {}
    local_rows: list = []
    session = requests.Session()
    session.auth = HTTPBasicAuth(user, password)
    record_map = query_batch_axtract(session, url, batch_serials)
    for serial, record in record_map.items():
        if serial not in local_results:
            local_results[serial] = _extract_fields(record)
            local_rows.append({"cpeid_consultado": serial, **record})
    return local_results, local_rows


def enrich_from_axtract(
    df: pd.DataFrame,
    url: str,
    user: str,
    password: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = df.copy()

    gpon_mask = df["EQUIPO"].apply(_is_gpon_cpeid)
    serials = (
        df.loc[gpon_mask, "EQUIPO"]
        .dropna()
        .astype(str)
        .str.strip()
        .unique()
        .tolist()
    )

    log.info("Axtract: %d seriales GPON a consultar en lotes de %d (workers=%d)",
             len(serials), _BATCH_SIZE, _MAX_WORKERS)

    serial_to_nro = (
        df.loc[gpon_mask, ["EQUIPO", "NRO_DE_INCIDENTE"]]
        .drop_duplicates("EQUIPO")
        .set_index("EQUIPO")["NRO_DE_INCIDENTE"]
        .to_dict()
    )

    batches = [serials[i:i + _BATCH_SIZE] for i in range(0, len(serials), _BATCH_SIZE)]
    results: dict = {}
    raw_rows: list = []

    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(batches) or 1)) as executor:
        futures = {
            executor.submit(_run_batch_axtract, b, url, user, password): b
            for b in batches
        }
        for future in as_completed(futures):
            local_results, local_rows = future.result()
            results.update(local_results)
            for row in local_rows:
                raw_rows.append({
                    "NRO_DE_INCIDENTE": serial_to_nro.get(row["cpeid_consultado"]),
                    **row,
                })

    if results:
        df_results = pd.DataFrame.from_records(
            [{"_EQUIPO": k, **v} for k, v in results.items()]
        )
        drop_cols = [c for c in list(AXTRACT_COLUMNS) + ["REFERENCIA"] if c in df.columns]
        df = df.drop(columns=drop_cols)
        df = df.merge(df_results, left_on="EQUIPO", right_on="_EQUIPO", how="left")
        df = df.drop(columns=["_EQUIPO"])
    else:
        for col in AXTRACT_COLUMNS:
            df[col] = pd.NA
        if "REFERENCIA" not in df.columns:
            df["REFERENCIA"] = pd.NA

    df_axtract_raw = pd.DataFrame(raw_rows) if raw_rows else pd.DataFrame()
    return df, df_axtract_raw
