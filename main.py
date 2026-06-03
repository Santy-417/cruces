import argparse
import json
import logging
import os
import sys
import time

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

REQUIRED_VARS = ["DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT", "DB_SERVICE_NAME", "ORACLE_CLIENT_PATH"]

log = logging.getLogger(__name__)


def _setup_logging(log_file: str) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def _validate_env():
    missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
    if missing:
        log.error("Faltan variables de entorno en .env: %s", ", ".join(missing))
        log.error("Copiar .env.example como .env y completar los valores.")
        sys.exit(1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="crucesmacros — Exportador de incidentes Siebel")
    parser.add_argument("--test-connection", action="store_true", help="Probar conexion sin ejecutar query")
    parser.add_argument("--limit", type=int, default=10, help="Maximo de filas a traer (0 = sin limite, default: 10)")
    parser.add_argument("--mode", choices=["all", "10h", "24h"], default="all",
                        help="all: todos los activos | 10h: ultimas 10h | 24h: ultimas 24h (default: all)")
    parser.add_argument("--output", default=os.getenv("OUTPUT_DIR", "."), help="Directorio de salida del Excel")
    parser.add_argument("--skip-clean", action="store_true", help="Saltear limpieza de IDs (text_cleaner)")
    parser.add_argument("--skip-axtract", action="store_true", help="Saltear consulta a Axtract")
    parser.add_argument("--skip-pnm", action="store_true", help="Saltear consulta a PNM")
    parser.add_argument("--log-file", default="crucesmacros.log",
                        help="Archivo de log (default: crucesmacros.log)")
    parser.add_argument("--enrich", choices=["axtract", "pnm"],
                        help="Enriquecer Excel existente sin consultar Oracle (requiere --file)")
    parser.add_argument("--file", default=None,
                        help="Ruta al Excel a enriquecer con --enrich")
    return parser.parse_args()


def test_connection():
    from src.connection import get_connection

    log.info("Probando conexion a Oracle...")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.callTimeout = 10000
        cursor.execute("SELECT 1 FROM DUAL")
        row = cursor.fetchone()
        cursor.close()
    log.info("Conexion exitosa. SELECT 1 FROM DUAL: %s", row[0])


def fetch_raw(limit: int = 10, mode: str = "all") -> pd.DataFrame:
    from src.connection import get_connection
    from src.queries import INCIDENTS_QUERY, INCIDENTS_QUERY_10H, INCIDENTS_QUERY_24H

    if mode == "10h":
        base = INCIDENTS_QUERY_10H
    elif mode == "24h":
        base = INCIDENTS_QUERY_24H
    else:
        base = INCIDENTS_QUERY

    if limit > 0:
        query = base.replace("AND ROWNUM <= 1000", f"AND ROWNUM <= {limit}")
    else:
        query = base.replace("AND ROWNUM <= 1000", "")
    timeout_ms = 600_000 if mode in ("10h", "24h") else 120_000

    log.info("Conectando a Oracle...")
    rows = []
    column_names = []

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.callTimeout = timeout_ms
        log.info("Ejecutando query (modo=%s, limite=%s)...", mode, limit if limit > 0 else "sin limite")
        log.info("Esperando respuesta de Oracle (mantener VPN activa)...")
        try:
            cursor.execute(query)
        except Exception as e:
            msg = str(e)
            if "03113" in msg or "03114" in msg or "closed the connection" in msg.lower():
                log.error("Conexion perdida con Oracle. Verificar que la VPN este activa durante toda la consulta.")
            raise
        column_names = [desc[0] for desc in cursor.description]
        log.info("Oracle respondio. Leyendo datos en lotes de 50...")

        t0 = time.time()
        batch = cursor.fetchmany(numRows=50)
        while batch:
            rows.extend(batch)
            if len(rows) % 200 == 0:
                log.info("  %d filas recibidas...", len(rows))
            batch = cursor.fetchmany(numRows=50)
        cursor.close()

    elapsed = time.time() - t0
    if not rows:
        log.warning("La query no devolvio ningun resultado.")
    else:
        log.info("%d filas recibidas en %.1f segundos.", len(rows), elapsed)

    return pd.DataFrame(rows, columns=column_names)


def run(args: argparse.Namespace):
    from src.processor import consolidate
    from src.mapper import to_exporte_schema
    from src.text_cleaner import clean_ids
    from src.exporter import export_to_excel

    df_raw = fetch_raw(limit=args.limit, mode=args.mode)

    if df_raw.empty:
        log.info("No se encontraron incidentes activos. No se genera Excel.")
        return

    log.info("Consolidando por incidente...")
    df_consolidated = consolidate(df_raw)
    log.info("Incidentes unicos procesados: %d", len(df_consolidated))

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


def enrich_existing(args: argparse.Namespace):
    from src.exporter import (export_to_excel,
                               REVERSE_PNM_RENAME, REVERSE_AXTRACT_RENAME)

    path = args.file
    if not path or not os.path.exists(path):
        log.error("--file no especificado o archivo no existe: %s", path)
        sys.exit(1)

    # Marker de inicio para que app.py pueda slicear el log desde aquí
    log.info("Conectando a Oracle...")
    log.info("Cargando Excel: %s", path)
    sheets = pd.read_excel(path, sheet_name=None, engine="openpyxl")
    df_exporte = sheets.get("EXPORTE", pd.DataFrame())
    df_raw     = sheets.get("RAW",     pd.DataFrame())
    df_axtract = sheets.get("AXTRACT", pd.DataFrame())
    df_pnm     = sheets.get("PNM",     pd.DataFrame())

    # Revertir renombres de headers para recuperar columnas internas
    df_exporte = df_exporte.rename(columns={**REVERSE_PNM_RENAME, **REVERSE_AXTRACT_RENAME})

    username   = os.getenv("DB_USER", "")
    output_dir = os.path.dirname(os.path.abspath(path))

    if args.enrich == "axtract":
        _AXTRACT_VARS = ["AXTRACT_URL", "AXTRACT_USER", "AXTRACT_PASSWORD"]
        missing = [v for v in _AXTRACT_VARS if not os.getenv(v)]
        if missing:
            log.error("Faltan vars Axtract: %s", ", ".join(missing))
            sys.exit(1)
        log.info("Consultando Axtract por CPEs GPON...")
        from src.axtract import enrich_from_axtract
        df_exporte, df_axtract = enrich_from_axtract(
            df_exporte,
            os.getenv("AXTRACT_URL"),
            os.getenv("AXTRACT_USER"),
            os.getenv("AXTRACT_PASSWORD"),
        )
        log.info("Axtract: %d CPEs encontrados", len(df_axtract))

    elif args.enrich == "pnm":
        _PNM_VARS = ["PNM_URL", "PNM_USER", "PNM_PASSWORD"]
        missing = [v for v in _PNM_VARS if not os.getenv(v)]
        if missing:
            log.error("Faltan vars PNM: %s", ", ".join(missing))
            sys.exit(1)
        log.info("Consultando PNM por CMs HFC...")
        from src.pnm import enrich_from_pnm
        df_exporte, df_pnm = enrich_from_pnm(
            df_exporte,
            os.getenv("PNM_URL"),
            os.getenv("PNM_USER"),
            os.getenv("PNM_PASSWORD"),
        )
        log.info("PNM: %d CMs encontrados", len(df_pnm))

    log.info("Generando Excel...")
    path_out = export_to_excel(df_exporte, df_raw, df_axtract, df_pnm,
                               output_dir, username, filepath=path)
    log.info("Excel generado: %s", os.path.abspath(path_out))


def main():
    args = _parse_args()
    _setup_logging(args.log_file)
    _validate_env()

    try:
        if args.test_connection:
            test_connection()
        elif args.enrich:
            enrich_existing(args)
        else:
            run(args)
    except KeyboardInterrupt:
        log.info("Proceso cancelado por el usuario (Ctrl+C).")
        sys.exit(0)


if __name__ == "__main__":
    main()
