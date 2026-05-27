import json
import logging
import re

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
]

_AXTRACT_FIELDS = '["cpeid","mode_props","metadata"]'


def _parse_cdata(raw: str) -> list:
    inner = re.sub(r"^<!\[CDATA\[|\]\]>$", "", raw.strip())
    try:
        return json.loads(inner)
    except json.JSONDecodeError:
        return []


def _extract_fields(record: dict) -> dict:
    mp     = record.get("mode_props") or {}
    fttx   = (mp.get("fttx") or {}).get("fttx") or {}
    alarms = mp.get("fttx_alarms") or {}
    meta   = record.get("metadata") or {}
    ont    = meta.get("ont") or {}
    olt    = meta.get("olt") or {}
    topo   = meta.get("topo") or {}

    line_card = olt.get("line_card")
    port      = olt.get("port")
    cmts_up   = f"{line_card}/{port}" if line_card and port else None

    return {
        "AXTRACT_ONT_STATUS":  ont.get("oper_status"),
        "AXTRACT_TX_POWER":    fttx.get("tx_power"),
        "AXTRACT_RX_POWER":    fttx.get("rx_power"),
        "AXTRACT_RX_OLT_POWER": fttx.get("rx_olt_power"),
        "AXTRACT_RANGING":     ont.get("ranging"),
        "AXTRACT_SFP_TYPE":    ont.get("if_sfp"),
        "AXTRACT_FTTX_TIME":   mp.get("fttx_time"),
        "AXTRACT_ALARM_CODE":  alarms.get("code"),
        "AXTRACT_CMTS":        olt.get("id"),
        "AXTRACT_CMTS_UP":     cmts_up,
        "AXTRACT_ARPON":       topo.get("arpon"),
        "AXTRACT_SPLITTER":    topo.get("splitter"),
        "AXTRACT_NAP":         topo.get("nap"),
        "AXTRACT_PUERTO_NAP":  topo.get("puerto_nap"),
        "REFERENCIA":          ont.get("equipment_id"),
    }


_MAC_RAW_RE = re.compile(r'^[0-9A-Fa-f]{12}$')


def _is_gpon_cpeid(val) -> bool:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return False
    s = str(val).strip()
    if not s or ":" in s:
        return False
    # 12 hex chars sin colons = MAC HFC sin formatear → no es ONT serial
    return not _MAC_RAW_RE.match(s)


def query_cpe(session: requests.Session, url: str, cpeid: str, timeout: int = 10) -> dict | None:
    mongo_query = json.dumps({
        "$or": [
            {"cpeid": {"$regex": cpeid}},
            {"metadata.ont.sn_raw": cpeid},
        ]
    })
    body = {
        "args": {
            "store_name": "cpe_store",
            "query": mongo_query,
            "sort": '[["last_update", -1]]',
            "fields": _AXTRACT_FIELDS,
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
        log.warning("Axtract timeout para CPE %s", cpeid)
    except Exception as exc:
        log.warning("Axtract error para CPE %s: %s", cpeid, exc)
    return None


def enrich_from_axtract(
    df: pd.DataFrame,
    url: str,
    user: str,
    password: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = df.copy()
    for col in AXTRACT_COLUMNS:
        df[col] = pd.NA

    session = requests.Session()
    session.auth = HTTPBasicAuth(user, password)

    raw_rows = []
    for idx, row in df.iterrows():
        cpeid = row.get("EQUIPO")
        if not _is_gpon_cpeid(cpeid):
            continue
        record = query_cpe(session, url, str(cpeid).strip())
        if record is None:
            continue
        fields = _extract_fields(record)
        for col, val in fields.items():
            df.at[idx, col] = val
        raw_rows.append({"NRO_DE_INCIDENTE": row.get("NRO_DE_INCIDENTE"), "cpeid_consultado": cpeid, **record})

    df_axtract_raw = pd.DataFrame(raw_rows) if raw_rows else pd.DataFrame()
    return df, df_axtract_raw
