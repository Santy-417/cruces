import json
import logging
import re

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
        "PNM_S": cmts_us.get("snr_min"),
        "PNM_T": ds.get("rx_power_min"),
        "PNM_U": ds.get("snr_min"),
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


def enrich_from_pnm(
    df: pd.DataFrame,
    url: str,
    user: str,
    password: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = df.copy()
    for col in PNM_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA

    session = requests.Session()
    session.auth = HTTPBasicAuth(user, password)

    hfc_rows = []
    for idx, row in df.iterrows():
        tecnologia = str(row.get("TECNOLOGIA", "") or "").upper()
        mac = row.get("MAC_CPE")
        if "COAXIAL" in tecnologia and _is_hfc_mac(mac):
            hfc_rows.append((idx, str(mac).strip(), row.get("NRO_DE_INCIDENTE")))

    log.info("PNM: %d MACs HFC a consultar en lotes de %d", len(hfc_rows), _BATCH_SIZE)

    raw_rows = []
    consecutive_errors = 0
    for i in range(0, len(hfc_rows), _BATCH_SIZE):
        if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
            log.warning("PNM: %d errores de lote consecutivos — deteniendo", _MAX_CONSECUTIVE_ERRORS)
            break
        batch = hfc_rows[i:i + _BATCH_SIZE]
        macs = [mac for _, mac, _ in batch]
        try:
            result_map = query_batch(session, url, macs)
        except requests.exceptions.ConnectionError:
            session = requests.Session()
            session.auth = HTTPBasicAuth(user, password)
            result_map = None
        if result_map is None:
            consecutive_errors += 1
            for idx, mac, _ in batch:
                df.at[idx, "PNM_R"] = "Sin respuesta"
            continue
        consecutive_errors = 0
        for idx, mac, nro in batch:
            record = result_map.get(mac)
            if record is None:
                continue
            fields = _extract_fields(record)
            for col, val in fields.items():
                df.at[idx, col] = val
            raw_rows.append({"NRO_DE_INCIDENTE": nro, "mac_consultada": mac, **record})

    df_pnm_raw = pd.DataFrame(raw_rows) if raw_rows else pd.DataFrame()
    return df, df_pnm_raw
