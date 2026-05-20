import json
import logging
import re

import pandas as pd
import requests
from requests.auth import HTTPBasicAuth

log = logging.getLogger(__name__)

PNM_COLUMNS = [
    "PNM_R",   # NQI11 — score de salud compuesto
    "PNM_S",   # DS SNR promedio
    "PNM_T",   # DS SNR mínimo
    "PNM_U",   # DS RX power promedio
    "PNM_V",   # DS health ("good"/"marginal"/"bad")
    "PNM_W",   # US TX power promedio
    "PNM_X",   # US TX power health
    "PNM_Y",   # Preeq health
    "PNM_AH",  # Reg status
]

_PNM_FIELDS = '["cpeid","mode_props","metadata","last_update"]'


def _parse_cdata(raw: str) -> list:
    inner = re.sub(r"^<!\[CDATA\[|\]\]>$", "", raw.strip())
    try:
        return json.loads(inner)
    except json.JSONDecodeError:
        return []


def _extract_fields(record: dict) -> dict:
    mp     = record.get("mode_props") or {}
    meta   = record.get("metadata") or {}
    cm     = mp.get("cm") or {}
    ds     = cm.get("ds") or {}
    cm_us  = cm.get("cm_us") or {}
    common = cm.get("common") or {}
    preeq_info = ((mp.get("preeq") or {}).get("cm_us") or {}).get("preeq") or {}

    return {
        "PNM_R":  common.get("nqi11"),
        "PNM_S":  ds.get("snr_avg"),
        "PNM_T":  ds.get("snr_min"),
        "PNM_U":  ds.get("rx_power_avg"),
        "PNM_V":  ds.get("health"),
        "PNM_W":  cm_us.get("tx_power_avg"),
        "PNM_X":  cm_us.get("tx_power_health"),
        "PNM_Y":  preeq_info.get("preeq_health"),
        "PNM_AH": meta.get("reg_status"),
    }


def _is_hfc_mac(val) -> bool:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return False
    s = str(val).strip()
    return bool(s) and ":" in s


def query_cm(session: requests.Session, url: str, mac: str, timeout: int = 10) -> dict | None:
    mac_raw = mac.replace(":", "").replace("-", "")
    body = {
        "args": {
            "store_name": "cm_store",
            "query": json.dumps({
                "$or": [
                    {"cpeid": {"$regex": mac, "$options": "i"}},
                    {"cpeid": {"$regex": mac_raw, "$options": "i"}},
                ]
            }),
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
    for idx, row in df.iterrows():
        mac = row.get("MAC_CPE")
        if not _is_hfc_mac(mac):
            continue
        record = query_cm(session, url, str(mac).strip())
        if record is None:
            continue
        fields = _extract_fields(record)
        for col, val in fields.items():
            df.at[idx, col] = val
        raw_rows.append({"NRO_DE_INCIDENTE": row.get("NRO_DE_INCIDENTE"), "mac_consultada": mac, **record})

    df_pnm_raw = pd.DataFrame(raw_rows) if raw_rows else pd.DataFrame()
    return df, df_pnm_raw
