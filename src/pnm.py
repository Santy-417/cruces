import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
from requests.auth import HTTPBasicAuth

log = logging.getLogger(__name__)

PNM_COLUMNS = [
    "PNM_R",  # reg_status string
    "PNM_S",  # CMTS US SNR min (dB)
    "PNM_T",  # DS RX Power min (dBmV)
    "PNM_U",  # DS SNR min (dB)
    "PNM_V",  # US TX Power max (dBmV)
    "PNM_W",  # MAC Domain Interface
    "PNM_X",  # CMTS id
    "PNM_Y",  # Raw Alias
]

_PNM_FIELDS = '["cpeid","mode_props","metadata"]'

_BATCH_SIZE = 50
_MAX_WORKERS = 5
_MAX_CONSECUTIVE_ERRORS = 3


def _parse_cdata(raw: str) -> list:
    inner = re.sub(r"^<!\[CDATA\[|\]\]>$", "", raw.strip())
    try:
        return json.loads(inner)
    except json.JSONDecodeError:
        return []


def _extract_fields(record: dict) -> dict:
    mp        = record.get("mode_props") or {}
    meta      = record.get("metadata") or {}
    cm        = mp.get("cm") or {}
    ds        = cm.get("ds") or {}
    cm_us     = cm.get("cm_us") or {}
    cmts_us   = (mp.get("cmts") or {}).get("cmts_us") or {}
    cmts_meta = meta.get("cmts") or {}

    return {
        "PNM_R": meta.get("reg_status"),
        "PNM_S": ds.get("snr_min"),
        "PNM_T": ds.get("rx_power_min"),
        "PNM_U": cmts_us.get("snr_min"),
        "PNM_V": cm_us.get("tx_power_max"),
        "PNM_W": meta.get("mac_domain_pretty"),
        "PNM_X": cmts_meta.get("id"),
        "PNM_Y": cmts_meta.get("raw_alias"),
    }


def _is_hfc_mac(val) -> bool:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return False
    s = str(val).strip()
    return bool(s) and ":" in s


def query_batch(
    session: requests.Session, url: str, macs: list, timeout: int = 30
) -> dict | None:
    body = {
        "args": {
            "store_name": "cm_store",
            "query": json.dumps({"$or": [{"cpeid": mac} for mac in macs]}),
            "sort": '[["last_update", -1]]',
            "fields": _PNM_FIELDS,
            "limit": len(macs),
            "format": "json",
            "target": "",
        }
    }
    try:
        resp = session.post(url, json=body, timeout=timeout)
        resp.raise_for_status()
        data = resp.json().get("return", {}).get("data", "")
        records = _parse_cdata(data)
        result = {}
        for r in records:
            cpeid = r.get("cpeid")
            if cpeid and cpeid not in result:
                result[cpeid] = r
        return result
    except requests.exceptions.Timeout:
        log.warning("PNM timeout para lote de %d MACs", len(macs))
    except requests.exceptions.ConnectionError as exc:
        log.warning("PNM conexión perdida en lote: %s", exc)
        raise
    except Exception as exc:
        log.warning("PNM error en lote: %s", exc)
    return None


def _run_batch(batch: list, url: str, user: str, password: str) -> tuple[dict, list, bool]:
    """Ejecuta un lote en su propio hilo con su propia session.
    Retorna (local_fields, local_rows, is_error)."""
    local_fields: dict = {}
    local_rows: list = []
    session = requests.Session()
    session.auth = HTTPBasicAuth(user, password)
    macs = [mac for _, mac, _ in batch]
    try:
        result_map = query_batch(session, url, macs)
    except requests.exceptions.ConnectionError:
        result_map = None
    if result_map is None:
        for _, mac, _ in batch:
            local_fields[mac] = {col: pd.NA for col in PNM_COLUMNS}
            local_fields[mac]["PNM_R"] = "Sin respuesta"
        return local_fields, local_rows, True
    for _, mac, nro in batch:
        record = result_map.get(mac)
        if record is None:
            continue
        local_fields[mac] = _extract_fields(record)
        local_rows.append({"NRO_DE_INCIDENTE": nro, "mac_consultada": mac, **record})
    return local_fields, local_rows, False


def enrich_from_pnm(
    df: pd.DataFrame,
    url: str,
    user: str,
    password: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = df.copy()

    hfc_rows = []
    for idx, row in df.iterrows():
        tecnologia = str(row.get("TECNOLOGIA", "") or "").upper()
        mac = row.get("MAC_CPE")
        if "COAXIAL" in tecnologia and _is_hfc_mac(mac):
            hfc_rows.append((idx, str(mac).strip(), row.get("NRO_DE_INCIDENTE")))

    log.info("PNM: %d MACs HFC a consultar en lotes de %d (workers=%d)",
             len(hfc_rows), _BATCH_SIZE, _MAX_WORKERS)

    batches = [hfc_rows[i:i + _BATCH_SIZE] for i in range(0, len(hfc_rows), _BATCH_SIZE)]
    all_fields: dict = {}
    raw_rows: list = []
    consecutive_errors = 0

    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(batches) or 1)) as executor:
        futures = {executor.submit(_run_batch, b, url, user, password): b for b in batches}
        for future in as_completed(futures):
            local_fields, local_rows, is_error = future.result()
            if is_error:
                consecutive_errors += 1
                if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                    log.warning("PNM: demasiados errores — cancelando lotes restantes")
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
            else:
                consecutive_errors = 0
            all_fields.update(local_fields)
            raw_rows.extend(local_rows)

    if all_fields:
        df_results = pd.DataFrame.from_records(
            [{"_MAC": k, **v} for k, v in all_fields.items()]
        )
        drop_cols = [c for c in PNM_COLUMNS if c in df.columns]
        df = df.drop(columns=drop_cols)
        df = df.merge(df_results, left_on="MAC_CPE", right_on="_MAC", how="left")
        df = df.drop(columns=["_MAC"])
    else:
        for col in PNM_COLUMNS:
            if col not in df.columns:
                df[col] = pd.NA

    df_pnm_raw = pd.DataFrame(raw_rows) if raw_rows else pd.DataFrame()
    return df, df_pnm_raw
