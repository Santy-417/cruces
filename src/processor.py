import pandas as pd

INCIDENT_KEY = "NRO_DE_INCIDENTE"

# Columnas que varían entre filas del mismo incidente (se excluyen de columnas estáticas)
VARIABLE_COLUMNS = [
    "TIPO_INFRAESTRUCTURA",
    "ID_INFRAESTRUCTURA",
    "ID_UNE",
    "UMBRAL_FALLA",
    "DAÑOS_ABIERTOS_POR_ELEMENTO",
    "ID_EQUIPO",
    "TIPO_EQUIPO",
    "ESTADO_EQUIPO",
    "MAC_EQUIPO",
    "MAC2_EQUIPO",
    "MARCA_EQUIPO",
    "MODELO_EQUIPO",
    "FILA",
]

INFRA_TYPES = [
    # GPON
    "OLT", "ARPON", "SPLITTER", "NAP", "HILO", "TARJETA", "PUERTOFISICO",
    # HFC
    "CDI", "NODO_OPTICO_ELECTRICO", "NOE", "AMPLIFICADOR", "AMPLIFICAD",
    "DERIVADOR", "TAP", "ODF",
]

EQUIPO_FIELDS = {
    "ID_EQUIPO": "ID",
    "MAC_EQUIPO": "MAC",
    "MAC2_EQUIPO": "MAC2",
    "MARCA_EQUIPO": "MARCA",
    "MODELO_EQUIPO": "MODELO",
    "ESTADO_EQUIPO": "ESTADO",
}


def _compute_pivot_key(row: pd.Series) -> str:
    tipo = str(row.get("TIPO_INFRAESTRUCTURA", "")).strip()
    if tipo == "Equipo":
        subtipo = str(row.get("TIPO_EQUIPO", "DESCONOCIDO")).strip()
        return f"Equipo_{subtipo}" if subtipo else "Equipo_DESCONOCIDO"
    return tipo


def consolidate(df: pd.DataFrame) -> pd.DataFrame:
    """
    Consolida múltiples filas por incidente en una sola fila.
    Usa TIPO_INFRAESTRUCTURA como discriminador del pivot.
    """
    if df.empty:
        return df

    df = df.copy()
    df["PIVOT_KEY"] = df.apply(_compute_pivot_key, axis=1)

    # Columnas estáticas: las que son iguales en todas las filas del mismo incidente
    static_cols = [
        col for col in df.columns
        if col not in VARIABLE_COLUMNS and col != "PIVOT_KEY"
    ]

    static_df = (
        df.drop_duplicates(subset=[INCIDENT_KEY])[static_cols]
        .set_index(INCIDENT_KEY)
    )

    # Pivot de infraestructura (OLT, ARPON, SPLITTER, NAP, HILO, TARJETA, PUERTOFISICO)
    infra_df = df[df["TIPO_INFRAESTRUCTURA"].isin(INFRA_TYPES)].copy()
    if not infra_df.empty:
        for src_col, col_prefix in [
            ("ID_INFRAESTRUCTURA", "ID"),
            ("ID_UNE", "UNE"),
            ("UMBRAL_FALLA", "UMBRAL"),
            ("DAÑOS_ABIERTOS_POR_ELEMENTO", "DAÑOS"),
        ]:
            if src_col not in infra_df.columns:
                continue
            piv = infra_df.pivot_table(
                index=INCIDENT_KEY,
                columns="PIVOT_KEY",
                values=src_col,
                aggfunc="first",
            )
            piv.columns = [f"{col_prefix}_{col}" for col in piv.columns]
            static_df = static_df.join(piv, how="left")

    # Pivot de equipos (CPE y STBOX)
    equipo_df = df[df["TIPO_INFRAESTRUCTURA"] == "Equipo"].copy()
    if not equipo_df.empty:
        for src_col, col_prefix in EQUIPO_FIELDS.items():
            if src_col not in equipo_df.columns:
                continue
            piv = equipo_df.pivot_table(
                index=INCIDENT_KEY,
                columns="PIVOT_KEY",
                values=src_col,
                aggfunc="first",
            )
            piv.columns = [f"{col_prefix}_{col}" for col in piv.columns]
            static_df = static_df.join(piv, how="left")

    result = static_df.reset_index()

    # Renombrar columnas de equipo a nombres legibles
    _rename = {}
    for col in result.columns:
        for suffix in ("_CPE", "_STBOX", "_MTA"):
            if col.endswith(f"_Equipo{suffix}"):
                _rename[col] = col.replace(f"_Equipo{suffix}", suffix)
                break
    result = result.rename(columns=_rename)

    expected = df[INCIDENT_KEY].nunique()
    assert len(result) == expected, (
        f"Error en pivot: se esperaban {expected} incidentes, se obtuvieron {len(result)}."
    )

    return result
