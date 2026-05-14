# crucesmacros

Herramienta Python para consultar incidentes activos de infraestructura GPON desde la base de datos Siebel (Oracle) de Tigo, consolidar los datos por incidente y generar un reporte Excel.

## Requisitos previos

- Python 3.9+
- [Oracle Instant Client](https://www.oracle.com/database/technologies/instant-client/winx64-64-downloads.html) (Basic Package, versión 21.x para Windows 64-bit)
- Acceso a la red interna de Tigo (VPN si es necesario)

## Instalación

### 1. Clonar / descargar el proyecto

```bash
cd crucesmacros
```

### 2. Crear entorno virtual (recomendado)

```bash
python -m venv .venv
.venv\Scripts\activate
```

### 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 4. Instalar Oracle Instant Client

1. Descargar el **Basic Package** de Oracle Instant Client para Windows (64-bit)
2. Extraer el ZIP en una carpeta, por ejemplo: `C:\oracle\instantclient_21_3`
3. No se requiere instalación adicional

### 5. Configurar credenciales

Copiar `.env.example` como `.env` y completar los valores:

```bash
copy .env.example .env
```

Editar `.env`:

```
DB_USER=tu_usuario
DB_PASSWORD=tu_contrasena
DB_DSN=host:puerto/nombre_servicio
ORACLE_CLIENT_PATH=C:\oracle\instantclient_21_3
OUTPUT_DIR=.
```

> **Importante:** El archivo `.env` contiene credenciales y nunca debe subirse al repositorio.

## Uso

### Probar la conexión

```bash
python main.py --test-connection
```

Conecta a Oracle, ejecuta `SELECT 1 FROM DUAL` y muestra el resultado. No ejecuta el query de incidentes.

### Generar reporte

```bash
python main.py --limit 1000
```

El script:
1. Conecta a Oracle
2. Ejecuta el query de incidentes activos (máximo 1000 incidentes — valor recomendado para uso diario)
3. Consolida las filas: **1 fila por incidente** con columnas por tipo de infraestructura
4. Genera un archivo Excel en `OUTPUT_DIR` con el nombre `crucesmacros_YYYYMMDD_HHMMSS.xlsx`

### Salida esperada

```
Conectando a Oracle...
Ejecutando query...
Filas obtenidas del query: 10
Consolidando por incidente...
Incidentes únicos procesados: 1
Excel generado: .\crucesmacros_20260415_143022.xlsx
```

## Estructura del Excel generado

Cada fila representa un incidente único con las siguientes columnas consolidadas:

| Grupo | Columnas |
|---|---|
| Incidente | NRO_DE_INCIDENTE, NUMERO_CUN, ESTADO, FECHA_DE_APERTURA, ... |
| Cliente | NRO_DOC_CUENTA, NOMBRE_CUENTA, DIRECCION, CIUDAD, ... |
| Infraestructura OLT | ID_OLT |
| Infraestructura ARPON | ID_ARPON |
| Infraestructura SPLITTER | ID_SPLITTER |
| Infraestructura NAP | ID_NAP |
| Infraestructura HILO | ID_HILO |
| Infraestructura TARJETA | ID_TARJETA |
| Puerto Físico | ID_PUERTOFISICO |
| CPE (router/ONT) | ID_CPE, MAC_CPE, MARCA_CPE, MODELO_CPE |
| STBOX (decodificador) | ID_STBOX, MAC_STBOX, MARCA_STBOX, MODELO_STBOX |

## Estructura del proyecto

```
crucesmacros/
├── .env                    # Credenciales (no al repo)
├── .env.example            # Template de credenciales
├── .gitignore
├── CLAUDE.md               # Guía técnica para desarrollo con IA
├── Readme.md               # Este archivo
├── requirements.txt
├── main.py                 # Entry point
└── src/
    ├── __init__.py
    ├── connection.py       # Conexión Oracle (thick mode, context manager)
    ├── queries.py          # Query SQL de incidentes
    ├── processor.py        # Consolidación/pivot de datos
    └── exporter.py         # Exportación a Excel
```

## Solución de problemas

### `DPI-1047: Cannot locate a 64-bit Oracle Client library`

El Oracle Instant Client no está instalado o la ruta en `ORACLE_CLIENT_PATH` es incorrecta. Verificar que la carpeta existe y contiene archivos `.dll`.

### `ORA-12541: TNS:no listener` / `ORA-12170: TNS:Connect timeout`

Problema de conectividad de red. Verificar VPN, host y puerto en `DB_DSN`.

### `ORA-01017: invalid username/password`

Credenciales incorrectas en `DB_USER` / `DB_PASSWORD`.
