import pandas as pd

_PNM_R_Y = ["PNM_R", "PNM_S", "PNM_T", "PNM_U", "PNM_V", "PNM_W", "PNM_X", "PNM_Y"]

EXPORTE_COLUMNS = [
    "NRO_DE_INCIDENTE",       # A
    "FECHA_DE_APERTURA",      # B
    "GRUPO",                  # C
    "SUB_ESTADO",             # D
    "NATURALEZA",             # E
    "PRODUCTO",               # F
    "DIRECCION",              # G
    "BARRIO",                 # H
    "CIUDAD",                 # I
    "DEPARTAMENTO",           # J
    "NRO_TIQUETE_TT",         # K
    "CONTEO_NODOS",           # L
    "ID_NODO_LIMPIO",         # M
    "ID_AMPLIFICADOR_LIMPIO", # N
    "ID_TAP_LIMPIO",          # O
    "ID_NODO",                # P
    "ID_AMPLIFICADOR",        # Q
    "CONTEO_MAC",             # R
    "ID_TAP",                 # S
    "MAC_CPE",                # T
    *_PNM_R_Y,                # U-AB (PNM, vacío)
    "NRO_TIQUETE_TT_2",       # AC (duplicado de K)
    "ID_LEGADO",              # AD
    "NODO_AMP",               # AE
    "TBD_AF",                 # AF
    "TBD_AG",                 # AG
    "PNM_AH",                 # AH (PNM, vacío)
    "ID_CDI",                 # AI
    "EQUIPO",                 # AJ
    "REFERENCIA",             # AK
    "DESCRIPCION",            # AL
    "TECNOLOGIA",             # AM
    "ID_OLT",                 # AN
    "ID_ARPON",               # AO
    "ID_SPLITTER",            # AP
    "ID_NAP",                 # AQ
]


def _coalesce(df: pd.DataFrame, *cols: str) -> pd.Series:
    result = pd.Series(pd.NA, index=df.index, dtype=object)
    for col in cols:
        if col in df.columns:
            result = result.combine_first(df[col])
    return result


def _get(df: pd.DataFrame, col: str) -> pd.Series:
    if col in df.columns:
        return df[col].copy()
    return pd.Series(pd.NA, index=df.index, dtype=object)


def _format_mac(val: object) -> object:
    if pd.isna(val) or not str(val).strip():
        return val
    raw = str(val).replace(":", "").replace("-", "").upper()
    if len(raw) == 12:
        return ":".join(raw[i:i + 2] for i in range(0, 12, 2))
    return val


def to_exporte_schema(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)

    for col in [
        "NRO_DE_INCIDENTE", "FECHA_DE_APERTURA", "GRUPO", "SUB_ESTADO",
        "NATURALEZA", "PRODUCTO", "DIRECCION", "BARRIO", "CIUDAD",
        "DEPARTAMENTO", "NRO_TIQUETE_TT", "ID_LEGADO",
    ]:
        out[col] = _get(df, col)

    out["ID_NODO"] = _coalesce(df, "ID_NODO_OPTICO_ELECTRICO", "ID_NOE")
    out["ID_AMPLIFICADOR"] = _coalesce(df, "ID_AMPLIFICADOR", "ID_AMPLIFICAD")
    out["ID_TAP"] = _coalesce(df, "ID_TAP", "ID_DERIVADOR")

    # CONTEO_NODOS: cuántos incidentes comparten el mismo ID_NODO (equivale a COUNTIF)
    nodo_counts = out.groupby("ID_NODO", dropna=True)["NRO_DE_INCIDENTE"].transform("count")
    out["CONTEO_NODOS"] = nodo_counts.where(out["ID_NODO"].notna(), 0)

    out["MAC_CPE"] = _get(df, "MAC_CPE").apply(_format_mac)

    mac_counts = out.groupby("MAC_CPE", dropna=True)["NRO_DE_INCIDENTE"].transform("count")
    out["CONTEO_MAC"] = mac_counts.where(out["MAC_CPE"].notna(), 0)

    for col in _PNM_R_Y + ["PNM_AH"]:
        out[col] = pd.NA

    # text_cleaner rellena ID_NODO_LIMPIO como fallback
    out["ID_NODO_LIMPIO"] = pd.NA
    # ID_UNE pivotado provee la forma corta directa desde Oracle; text_cleaner como fallback
    out["ID_AMPLIFICADOR_LIMPIO"] = _coalesce(df, "UNE_AMPLIFICAD", "UNE_AMPLIFICADOR")
    out["ID_TAP_LIMPIO"] = _coalesce(df, "UNE_TAP", "UNE_DERIVADOR")

    # NODO_AMP se calcula en main.py después de clean_ids
    out["NODO_AMP"] = ""

    out["NRO_TIQUETE_TT_2"] = _get(df, "NRO_TIQUETE_TT")
    out["TBD_AF"] = pd.NA
    out["TBD_AG"] = pd.NA

    out["ID_CDI"] = _coalesce(df, "ID_CDI")
    out["EQUIPO"] = _coalesce(df, "ID_CPE", "MAC_MTA", "MAC_Equipo_CABLEM", "MAC_Equipo_CABLE MODEM").apply(_format_mac)
    out["REFERENCIA"] = pd.NA

    out["DESCRIPCION"] = _get(df, "DESCRIPCIÓN")
    out["TECNOLOGIA"]  = _get(df, "TECNOLOGÍA")

    for col in ["ID_OLT", "ID_ARPON", "ID_SPLITTER", "ID_NAP"]:
        out[col] = _get(df, col)

    return out[EXPORTE_COLUMNS]
