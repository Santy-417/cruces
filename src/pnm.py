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

_MAX_CONSECUTIVE_ERRORS = 10


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
        "PNM_W": meta.get("mac_domain_pretty"), # AB: MAC Domain Interface (forma corta)
        "PNM_X": cmts_meta.get("id"),         # AC: CMTS id
        "PNM_Y": cmts_meta.get("raw_alias"),  # AD: raw_alias
    }


def _is_hfc_mac(val) -> bool:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return False
    s = str(val).strip()
    return bool(s) and ":" in s


def query_cm(session: requests.Session, url: str, mac: str, timeout: int = 10) -> dict | None:
    body = {
        "args": {
            "store_name": "cm_store",
            "query": json.dumps({"cpeid": mac}),
            "sort": '[["last_update", -1]]',
            "fields": _PNM_FIELDS,
            "limit": 1,
            "format": "json",
            "target": "",
        }
    }
    try:
        resp = session.post(url, json=body, timeout=timeout)
        resp.raise_for_status()
        data = resp.json().get("return", {}).get("data", "")
        records = _parse_cdata(data)
        return records[0] if records else None
    except requests.exceptions.Timeout:
        log.warning("PNM timeout para CM %s", mac)
    except requests.exceptions.ConnectionError as exc:
        log.warning("PNM conexión perdida para CM %s: %s", mac, exc)
        raise
    except Exception as exc:
        log.warning("PNM error para CM %s: %s", mac, exc)
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

    raw_rows = []
    consecutive_errors = 0
    for idx, row in df.iterrows():
        if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
            log.warning(
                "PNM: %d errores consecutivos — deteniendo consultas HFC",
                _MAX_CONSECUTIVE_ERRORS,
            )
            break
        mac = row.get("MAC_CPE")
        tecnologia = str(row.get("TECNOLOGIA", "") or "").upper()
        if "COAXIAL" not in tecnologia or not _is_hfc_mac(mac):
            continue
        try:
            record = query_cm(session, url, str(mac).strip())
        except requests.exceptions.ConnectionError:
            session = requests.Session()
            session.auth = HTTPBasicAuth(user, password)
            record = None
        if record is None:
            consecutive_errors += 1
            df.at[idx, "PNM_R"] = "Sin respuesta"
            continue
        consecutive_errors = 0
        fields = _extract_fields(record)
        for col, val in fields.items():
            df.at[idx, col] = val
        raw_rows.append({"NRO_DE_INCIDENTE": row.get("NRO_DE_INCIDENTE"), "mac_consultada": mac, **record})

    df_pnm_raw = pd.DataFrame(raw_rows) if raw_rows else pd.DataFrame()
    return df, df_pnm_raw
