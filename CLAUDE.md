# crucesmacros — Guía para Claude

## Qué hace este proyecto

Conecta a una base de datos Oracle de producción (versión antigua, 11g/12c) del sistema Siebel de Tigo, ejecuta un query de incidentes activos ("Queja Daño"), consolida los resultados y genera un archivo Excel.

El query devuelve múltiples filas por incidente (una por elemento de infraestructura: GPON u HFC). El procesamiento consolida eso en **1 fila por incidente**.

---

## Reglas críticas — NO violar

1. **Thick mode obligatorio**: `oracledb.init_oracle_client()` debe llamarse antes de cualquier `connect()`. Oracle 11g/12c no funciona con thin mode (puro Python).

2. **Nunca `fetchall()`**: Usar siempre `fetchmany(arraysize=50)`. La BD es producción, `fetchall()` en una query sin límite podría traer millones de filas.

3. **No modificar la sintaxis `(+)` del SQL**: Es Oracle outer join syntax específica para 11g/12c. No convertir a ANSI JOIN.

4. **Siempre context manager para conexiones**: El `get_connection()` usa `try/finally conn.close()`. Toda ejecución de query debe estar dentro de ese context manager.

5. **`cursor.callTimeout = 30000`**: Siempre setear antes de ejecutar. Protege de queries colgadas en producción.

6. **El `.env` nunca al repo**: Las credenciales solo en `.env` local, excluido por `.gitignore`.

---

## Arquitectura

```
main.py
  └── src/connection.py    # get_connection() context manager, thick mode init
  └── src/queries.py       # INCIDENTS_QUERY constante SQL
  └── src/processor.py     # consolidate(df) → pivot por NRO_DE_INCIDENTE
  └── src/mapper.py        # to_exporte_schema(df) → DataFrame con columnas EXPORTE
  └── src/text_cleaner.py  # clean_ids(df) → strip prefijo ciudad en IDs HFC
  └── src/axtract.py       # enrich_from_axtract() → consulta NBI API GPON, rellena AXTRACT_*
  └── src/exporter.py      # export_to_excel(df, output_dir) → path .xlsx
```

### Flujo de datos

1. `main.py` carga `.env`, valida vars
2. `fetch_raw()` ejecuta `INCIDENTS_QUERY` con `fetchmany(50)`, construye DataFrame
3. `processor.consolidate(df)` pivota: `TIPO_INFRAESTRUCTURA` → columnas (`ID_NODO_OPTICO_ELECTRICO`, `ID_AMPLIFICADOR`, `ID_TAP`, `MAC_CPE`, etc.)
4. `mapper.to_exporte_schema(df)` mapea al schema fijo de columnas EXPORTE
5. `text_cleaner.clean_ids(df)` strip prefijo ciudad de IDs HFC: `BERLMDE-NOE16837` → `NOE16837`
6. `main.py` calcula `NODO_AMP = ID_NODO_LIMPIO + ID_AMPLIFICADOR_LIMPIO`
7. `axtract.enrich_from_axtract()` consulta NBI API por cada ONT serial GPON, rellena columnas `AXTRACT_*` y `REFERENCIA`
8. `exporter.export_to_excel()` genera `Ingreso_Siebel_YYYY-MM-DD_HH-MM_USER.xlsx` (hojas EXPORTE + RAW + AXTRACT)

---

## Lógica de pivot (processor.py)

El campo clave es `TIPO_INFRAESTRUCTURA`. Valores posibles:
- **GPON:** `OLT`, `ARPON`, `SPLITTER`, `NAP`, `HILO`, `TARJETA`, `PUERTOFISICO`
- **HFC:** `CDI`, `NODO_OPTICO_ELECTRICO`, `NOE`, `AMPLIFICADOR`, `AMPLIFICAD`, `DERIVADOR`, `TAP`, `ODF`
- **Equipo:** tipo especial con `MAC_EQUIPO`, `ID_EQUIPO`, `TIPO_EQUIPO` (CPE/STBOX/MTA), `MARCA_EQUIPO`, `MODELO_EQUIPO`

Para `Equipo`, se calcula `PIVOT_KEY = f"Equipo_{TIPO_EQUIPO}"` para distinguir CPE de STBOX de MTA.

El pivot genera columnas dinámicas por incidente según los tipos presentes, p.ej.:
- HFC: `ID_NODO_OPTICO_ELECTRICO` (o `ID_NOE`), `ID_AMPLIFICADOR`, `ID_TAP`
- Equipos: `MAC_CPE`, `MAC_STBOX`, `MAC_MTA`, `ID_CDI`

El `mapper.py` colapsa aliases con `_coalesce()` (ej. `ID_NOE` / `ID_NODO_OPTICO_ELECTRICO` → `ID_NODO`).

---

## Variables de entorno requeridas

| Variable | Descripción |
|---|---|
| `DB_USER` | Usuario Oracle |
| `DB_PASSWORD` | Contraseña Oracle |
| `DB_HOST` | Host del servidor Oracle |
| `DB_PORT` | Puerto (normalmente 1521) |
| `DB_SERVICE_NAME` | Nombre del servicio Oracle |
| `ORACLE_CLIENT_PATH` | Ruta al Oracle Instant Client (Windows) |
| `OUTPUT_DIR` | Carpeta destino del Excel (default: `.`) |
| `AXTRACT_URL` | Endpoint NBI API Axtract (POST) |
| `AXTRACT_USER` | Usuario Basic Auth Axtract |
| `AXTRACT_PASSWORD` | Password Basic Auth Axtract |
| `PNM_URL` | Endpoint NBI API PNM (POST) |
| `PNM_USER` | Usuario Basic Auth PNM |
| `PNM_PASSWORD` | Password Basic Auth PNM |

---

## Axtract (NBI API GPON)

- Endpoint: POST `AXTRACT_URL` con Basic Auth
- Solo se consultan filas donde `EQUIPO` es serial ONT GPON (no MAC HFC)
- Detección GPON: valor no vacío, sin `":"`, y que NO sea 12 hex chars puros (esos son MACs HFC sin formatear)
- `store_name`: `cpe_store` — campos consultados: `["cpeid", "mode_props", "metadata"]`
- Query: `$or` con `$regex` sobre `cpeid` y `metadata.ont.sn_raw` (tolerante a formato)
- Columnas que rellena en EXPORTE: `AXTRACT_ONT_STATUS`, `AXTRACT_RX_POWER`, `AXTRACT_TX_POWER`, `AXTRACT_RX_OLT_POWER`, `AXTRACT_ALARM_CODE`, `AXTRACT_ALARM_SEVERITY`, `AXTRACT_ALARM_STATE`, `AXTRACT_FTTX_TIME`, `AXTRACT_CMTS`, `AXTRACT_CMTS_UP`, `AXTRACT_ARPON`, `AXTRACT_SPLITTER`, `AXTRACT_NAP`, `AXTRACT_PUERTO_NAP`, `REFERENCIA`
- Hoja Excel: `AXTRACT` (JSON crudo de la API, una fila por incidente consultado)
- Si faltan `AXTRACT_URL/USER/PASSWORD` → se salta con WARNING, no es error fatal

---

## PNM (NBI API HFC)

- Endpoint: POST `PNM_URL` con Basic Auth
- Solo se consultan filas donde `MAC_CPE` tiene `":"` (formato MAC HFC)
- `store_name`: `cm_store` — campos consultados: `["cpeid", "mode_props", "metadata"]`
- Query: exact match `{"cpeid": mac}` con MAC formateada con colons
- Columnas internas → header Excel: `PNM_R`→"Status" (reg_status string), `PNM_S`→"Dw SNR" (CMTS US SNR min), `PNM_T`→"PL Dw" (DS RX Power min), `PNM_U`→"Up SNR" (DS SNR min), `PNM_V`→"PL Up" (US TX Power max), `PNM_W`→"CMTS", `PNM_X`→"CMTS Up", `PNM_Y`→"US Alias"
- Formato condicional en EXPORTE (cols W–AA): verde/amarillo/rojo según umbrales por métrica
- Hoja Excel: `PNM` (JSON crudo de la API, una fila por CM consultado)
- Si faltan `PNM_URL/USER/PASSWORD` → se salta con WARNING, no es error fatal
- **Cobertura esperada**: cm_store solo contiene módems activamente monitoreados por PNM, no todos los CMs de la red. Cobertura típica: ~2% de HFC activo. Esto es normal.

---

## Comandos útiles

```bash
# Verificar instalación de paquetes
python -c "import oracledb; import pandas; import openpyxl; import dotenv; print('OK')"

# Probar conexión sin correr el query completo
python main.py --test-connection

# Correr el pipeline completo
python main.py
```

---

## Oracle Instant Client (Windows)

Descargar desde Oracle Technology Network:
- Versión recomendada: 21.x Basic Package (64-bit)
- Extraer en `C:\oracle\instantclient_21_X`
- Poner esa ruta en `ORACLE_CLIENT_PATH` del `.env`
- No requiere instalación, solo descomprimir

---

## Evolución futura planeada

- [ ] Dashboard de visualización (Streamlit o similar)
- [ ] Filtros por fecha, tecnología, estado
- [ ] Cruce contra Excel de referencia (inventario externo)
