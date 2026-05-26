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
    "CONTEO_ARPON",           # M
    "CONTEO_AMPLIFICADOR",    # N
    "CONTEO_SPLITTER",        # O
    "ID_NODO_LIMPIO",         # P
    "ID_AMPLIFICADOR_LIMPIO", # Q
    "ID_TAP_LIMPIO",          # R
    "ID_NODO",                # S
    "ID_AMPLIFICADOR",        # T
    "ID_TAP",                 # U
    "MAC_CPE",                # V
    *_PNM_R_Y,                # U-AB (PNM, vacío)
    "NRO_TIQUETE_TT_2",       # AC (duplicado de K)
    "ID_LEGADO",              # AD
    "NODO_AMP",               # AE
    "TBD_AF",                 # AF
    "TBD_AG",                 # AG
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

    # Setear IDs GPON temprano para poder calcular contadores
    out["ID_ARPON"]    = _get(df, "ID_ARPON")
    out["ID_SPLITTER"] = _get(df, "ID_SPLITTER")

    # CONTEO_NODOS: cuántos incidentes comparten el mismo ID_NODO
    nodo_counts = out.groupby("ID_NODO", dropna=True)["NRO_DE_INCIDENTE"].transform("count")
    out["CONTEO_NODOS"] = nodo_counts.where(out["ID_NODO"].notna(), 0)

    # CONTEO_ARPON / CONTEO_SPLITTER: análogos para GPON (0 para HFC)
    arpon_counts = out.groupby("ID_ARPON", dropna=True)["NRO_DE_INCIDENTE"].transform("count")
    out["CONTEO_ARPON"] = arpon_counts.where(out["ID_ARPON"].notna(), 0)

    splitter_counts = out.groupby("ID_SPLITTER", dropna=True)["NRO_DE_INCIDENTE"].transform("count")
    out["CONTEO_SPLITTER"] = splitter_counts.where(out["ID_SPLITTER"].notna(), 0)

    out["MAC_CPE"] = _coalesce(df, "MAC_CPE", "MAC_Equipo_CABLE MODEM", "MAC_Equipo_CABLEM").apply(_format_mac)

    # CONTEO_AMPLIFICADOR: cuántos incidentes comparten el mismo ID_AMPLIFICADOR
    amp_counts = out.groupby("ID_AMPLIFICADOR", dropna=True)["NRO_DE_INCIDENTE"].transform("count")
    out["CONTEO_AMPLIFICADOR"] = amp_counts.where(out["ID_AMPLIFICADOR"].notna(), 0)

    for col in _PNM_R_Y:
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
    # Propaga MACs HFC (con colons) a MAC_CPE cuando MAC_CPE está vacío
    out["MAC_CPE"] = out["MAC_CPE"].combine_first(
        out["EQUIPO"].where(out["EQUIPO"].str.contains(":", na=False))
    )
    out["REFERENCIA"] = pd.NA

    out["DESCRIPCION"] = _get(df, "DESCRIPCIÓN")
    out["TECNOLOGIA"]  = _get(df, "TECNOLOGÍA")

    out["ID_OLT"] = _get(df, "ID_OLT")
    # ID_ARPON e ID_SPLITTER ya están seteados arriba (para los contadores)
    out["ID_NAP"] = _get(df, "ID_NAP")

    return out[EXPORTE_COLUMNS]
