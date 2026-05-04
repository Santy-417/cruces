import os
from contextlib import contextmanager

import oracledb
from dotenv import load_dotenv

load_dotenv()

_client_initialized = False


def _ensure_thick_mode():
    global _client_initialized
    if not _client_initialized:
        lib_dir = os.getenv("ORACLE_CLIENT_PATH")
        if not lib_dir:
            raise EnvironmentError(
                "ORACLE_CLIENT_PATH no está definido en .env. "
                "Se requiere Oracle Instant Client para conectar a Oracle 11g/12c."
            )
        oracledb.init_oracle_client(lib_dir=lib_dir)
        _client_initialized = True


@contextmanager
def get_connection():
    """
    Context manager para conexiones Oracle.
    Garantiza que la conexión siempre se cierre, incluso ante excepciones.
    Usa thick mode (Oracle Instant Client) requerido para Oracle 11g/12c.
    """
    _ensure_thick_mode()

    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    host = os.getenv("DB_HOST")
    port = os.getenv("DB_PORT")
    service_name = os.getenv("DB_SERVICE_NAME")

    if not all([user, password, host, port, service_name]):
        raise EnvironmentError(
            "Faltan variables de entorno. Verificar DB_USER, DB_PASSWORD, "
            "DB_HOST, DB_PORT y DB_SERVICE_NAME en .env."
        )

    conn = oracledb.connect(
        user=user,
        password=password,
        host=host,
        port=int(port),
        service_name=service_name,
    )
    try:
        yield conn
    finally:
        conn.close()
