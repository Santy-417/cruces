import pandas as pd

DEFAULT_RULES = {
    "ID_NODO": {
        "output_col": "ID_NODO_LIMPIO",
        "replacements": [
            ("TIGO-", ""), ("TGO-", ""), ("COL-", ""),
            ("_", "-"),
        ],
        "strip": True,
        "upper": True,
        "strip_city_prefix": True,
    },
    "ID_AMPLIFICADOR": {
        "output_col": "ID_AMPLIFICADOR_LIMPIO",
        "replacements": [
            ("TIGO-", ""), ("TGO-", ""), ("COL-", ""),
            ("_", "-"),
        ],
        "strip": True,
        "upper": True,
        "strip_city_prefix": True,
    },
    "ID_TAP": {
        "output_col": "ID_TAP_LIMPIO",
        "replacements": [
            ("TIGO-", ""), ("TGO-", ""), ("COL-", ""),
            ("_", "-"),
        ],
        "strip": True,
        "upper": True,
        "strip_city_prefix": True,
    },
}


def _apply_rule(series: pd.Series, rule: dict) -> pd.Series:
    not_null_mask = series.notna()
    result = series.astype(str).copy()
    if rule.get("strip"):
        result = result.str.strip()
    if rule.get("upper"):
        result = result.str.upper()
    for old, new in rule.get("replacements", []):
        result = result.str.replace(old, new, regex=False)
    if rule.get("strip_city_prefix"):
        result = result.str.replace(r'^[A-Z]+-', '', regex=True)
    return result.where(not_null_mask, pd.NA)


def clean_ids(df: pd.DataFrame, rules: dict = None) -> pd.DataFrame:
    active_rules = rules if rules is not None else DEFAULT_RULES
    df = df.copy()
    for src_col, rule in active_rules.items():
        output_col = rule["output_col"]
        cleaned = _apply_rule(df[src_col], rule) if src_col in df.columns else pd.Series(pd.NA, index=df.index, dtype=object)
        if output_col in df.columns:
            df[output_col] = df[output_col].combine_first(cleaned)
        else:
            df[output_col] = cleaned
    return df
