import argparse
import logging
import os
import sys

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

REQUIRED_VARS = ["DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT", "DB_SERVICE_NAME", "ORACLE_CLIENT_PATH"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("crucesmacros.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


def _validate_env():
    missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
    if missing:
        log.error("Faltan variables de entorno en .env: %s", ", ".join(missing))
        log.error("Copiar .env.example como .env y completar los valores.")
        sys.exit(1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="crucesmacros — Exportador de incidentes Siebel")
    parser.add_argument("--test-connection", action="store_true", help="Probar conexión sin ejecutar query")
    parser.add_argument("--limit", type=int, default=10, help="Máximo de incidentes a traer (default: 10)")
    parser.add_argument("--output", default=os.getenv("OUTPUT_DIR", "."), help="Directorio de salida del Excel")
    parser.add_argument("--skip-clean", action="store_true", help="Saltear limpieza de IDs (text_cleaner)")
    parser.add_argument("--skip-axtract", action="store_true", help="Saltear consulta a Axtract")
    parser.add_argument("--skip-pnm", action="store_true", help="Saltear consulta a PNM")
    return parser.parse_args()


def test_connection():
    from src.connection import get_connection

    log.info("Probando conexión a Oracle...")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.callTimeout = 10000
        cursor.execute("SELECT 1 FROM DUAL")
        row = cursor.fetchone()
        cursor.close()
    log.info("Conexión exitosa. SELECT 1 FROM DUAL: %s", row[0])


def fetch_raw(limit: int = 10) -> pd.DataFrame:
    from src.connection import get_connection
    from src.queries import INCIDENTS_QUERY

    query = INCIDENTS_QUERY.replace("AND ROWNUM <= 1000", f"AND ROWNUM <= {limit}")
    query = query.replace("WHERE fila <= 10", f"WHERE fila <= {limit}")

    log.info("Conectando a Oracle...")
    rows = []
    column_names = []

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.callTimeout = max(120_000, limit * 200)
        log.info("Ejecutando query (puede tardar hasta 2 minutos en la primera ejecución)...")
        cursor.execute(query)
        column_names = [desc[0] for desc in cursor.description]
        batch = cursor.fetchmany(numRows=50)
        while batch:
            rows.extend(batch)
            batch = cursor.fetchmany(numRows=50)
        cursor.close()

    log.info("Filas crudas recibidas: %d", len(rows))
    return pd.DataFrame(rows, columns=column_names)


def run(args: argparse.Namespace):
    from src.processor import consolidate
    from src.mapper import to_exporte_schema
    from src.text_cleaner import clean_ids
    from src.exporter import export_to_excel

    df_raw = fetch_raw(limit=args.limit)

    if df_raw.empty:
        log.info("No se encontraron incidentes activos. No se genera Excel.")
        return

    log.info("Consolidando por incidente...")
    df_consolidated = consolidate(df_raw)
    log.info("Incidentes únicos procesados: %d", len(df_consolidated))

    if "TECNOLOGÍA" in df_consolidated.columns:
        for tech, n in df_consolidated["TECNOLOGÍA"].value_counts().items():
            log.info("  %s: %d incidentes", tech, n)

    log.info("Mapeando al schema EXPORTE...")
    df_exporte = to_exporte_schema(df_consolidated)

    if not args.skip_clean:
        log.info("Limpiando IDs de infraestructura...")
        df_exporte = clean_ids(df_exporte)
        df_exporte["NODO_AMP"] = (
            df_exporte["ID_NODO_LIMPIO"].fillna("").astype(str) +
            df_exporte["ID_AMPLIFICADOR_LIMPIO"].fillna("").astype(str)
        )

    _AXTRACT_VARS = ["AXTRACT_URL", "AXTRACT_USER", "AXTRACT_PASSWORD"]
    df_axtract_raw = pd.DataFrame()
    if not args.skip_axtract:
        from src.axtract import enrich_from_axtract
        missing_axtract = [v for v in _AXTRACT_VARS if not os.getenv(v)]
        if missing_axtract:
            log.warning("Faltan vars Axtract (%s) — saltando enriquecimiento", ", ".join(missing_axtract))
        else:
            log.info("Consultando Axtract por CPEs GPON...")
            df_exporte, df_axtract_raw = enrich_from_axtract(
                df_exporte,
                os.getenv("AXTRACT_URL"),
                os.getenv("AXTRACT_USER"),
                os.getenv("AXTRACT_PASSWORD"),
            )
            log.info("Axtract: %d CPEs encontrados", len(df_axtract_raw))

    _PNM_VARS = ["PNM_URL", "PNM_USER", "PNM_PASSWORD"]
    df_pnm_raw = pd.DataFrame()
    if not args.skip_pnm:
        from src.pnm import enrich_from_pnm
        missing_pnm = [v for v in _PNM_VARS if not os.getenv(v)]
        if missing_pnm:
            log.warning("Faltan vars PNM (%s) — saltando enriquecimiento", ", ".join(missing_pnm))
        else:
            log.info("Consultando PNM por CMs HFC...")
            df_exporte, df_pnm_raw = enrich_from_pnm(
                df_exporte,
                os.getenv("PNM_URL"),
                os.getenv("PNM_USER"),
                os.getenv("PNM_PASSWORD"),
            )
            log.info("PNM: %d CMs encontrados", len(df_pnm_raw))

    username = os.getenv("DB_USER", "")
    path = export_to_excel(df_exporte, df_consolidated, df_axtract_raw, df_pnm_raw, args.output, username)
    log.info("Excel generado: %s", path)


def main():
    _validate_env()
    args = _parse_args()

    if args.test_connection:
        test_connection()
    else:
        run(args)


if __name__ == "__main__":
    main()
