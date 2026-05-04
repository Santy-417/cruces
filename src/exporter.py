import os
from datetime import datetime

import pandas as pd
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from src.mapper import EXPORTE_COLUMNS

_PNM_COLS = {
    "PNM_R", "PNM_S", "PNM_T", "PNM_U", "PNM_V", "PNM_W", "PNM_X", "PNM_Y", "PNM_AH",
}

# Anchos específicos del VBA (por letra de columna Excel, basados en posición fija del schema EXPORTE)
_LETTER_WIDTHS = {
    "D": 8,
    "E": 9.43,
    "F": 7.86,
    "G": 13.14,
    "J": 8.43,
    "P": 22.86,
}

_SORT_COLS = ["ID_NODO", "ID_AMPLIFICADOR", "ID_TAP"]

_HEADER_FILL = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
_PNM_FILL = PatternFill(start_color="BFBFBF", end_color="BFBFBF", fill_type="solid")
_RED_FILL = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")


def _col_letter_for(col_name: str) -> str:
    idx = EXPORTE_COLUMNS.index(col_name)
    return get_column_letter(idx + 1)


def _format_exporte_sheet(ws, df: pd.DataFrame) -> None:
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    last_row = len(df) + 1
    header_font = Font(bold=True)

    for ci, col_name in enumerate(df.columns, start=1):
        letter = get_column_letter(ci)
        header_cell = ws.cell(row=1, column=ci)
        header_cell.font = header_font
        header_cell.alignment = Alignment(horizontal="center")
        header_cell.fill = _PNM_FILL if col_name in _PNM_COLS else _HEADER_FILL

        # Ancho de columna
        if letter in _LETTER_WIDTHS:
            ws.column_dimensions[letter].width = _LETTER_WIDTHS[letter]
        else:
            max_len = max(
                (len(str(cell.value)) if cell.value is not None else 0)
                for cell in ws[letter]
            )
            ws.column_dimensions[letter].width = min(max_len + 2, 50)

    # Formato de fecha en columna B (FECHA_DE_APERTURA)
    b_idx = EXPORTE_COLUMNS.index("FECHA_DE_APERTURA") + 1
    date_fmt = "DD/MM/YYYY HH:MM"
    for row in ws.iter_rows(min_row=2, max_row=last_row, min_col=b_idx, max_col=b_idx):
        for cell in row:
            cell.number_format = date_fmt

    # Conditional formatting: columna K roja si NRO_TIQUETE_TT ≠ "" y ≠ 0
    k_letter = _col_letter_for("NRO_TIQUETE_TT")
    ws.conditional_formatting.add(
        f"{k_letter}2:{k_letter}{last_row}",
        FormulaRule(formula=[f'AND({k_letter}2<>"",{k_letter}2<>0)'], fill=_RED_FILL),
    )


def export_to_excel(
    df_exporte: pd.DataFrame,
    df_raw: pd.DataFrame,
    output_dir: str,
    username: str = "",
) -> str:
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    user_suffix = f"_{username}" if username else ""
    filename = f"Ingreso_Siebel_{timestamp}{user_suffix}.xlsx"
    filepath = os.path.join(output_dir, filename)

    sort_cols = [c for c in _SORT_COLS if c in df_exporte.columns]
    df_sorted = df_exporte.sort_values(sort_cols, na_position="last").reset_index(drop=True)

    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        df_sorted.to_excel(writer, sheet_name="EXPORTE", index=False)
        df_raw.to_excel(writer, sheet_name="RAW", index=False)

        _format_exporte_sheet(writer.sheets["EXPORTE"], df_sorted)

        ws_raw = writer.sheets["RAW"]
        ws_raw.freeze_panes = "A2"
        for col_cells in ws_raw.columns:
            max_len = max(
                (len(str(c.value)) if c.value is not None else 0) for c in col_cells
            )
            ws_raw.column_dimensions[col_cells[0].column_letter].width = min(max_len + 2, 50)

    return os.path.abspath(filepath)
