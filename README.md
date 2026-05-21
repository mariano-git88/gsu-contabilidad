# Contabilidad Suprabond

Webapp Streamlit para visibilidad de costos internos por SKU y cálculo
de costo de mercadería vendida (COGS) por período. Vive en
`contabilidad.streamlit.app`.

## Tres secciones

1. **Productos** — Catálogo activo desde la API de Contabilium UY. SKU,
   nombre, costo interno cargado en el ERP (informativo), precio neto,
   precio con IVA, stock, rentabilidad. Buscador, filtros y descarga de
   plantilla pre-llenada para la sección de carga.

2. **Carga de costos** — Upload de CSV/XLSX con columnas `sku` y `costo`.
   Se elige una fecha de vigencia para toda la carga; cada lote queda en
   un Google Sheet en modo append-only (nunca pisa filas).

3. **COGS mensual** — Selección de mes → trae facturación de Contabilium
   (FAC + NDF − NCF) y multiplica cada línea por el costo vigente del
   SKU a la fecha de la factura. Devuelve total COGS, COGS por SKU,
   margen bruto y panel de salud con SKUs vendidos sin costo cargado.

## Setup local

```bash
# 1. Clonar repo
cd "Contabilidad - Claude"

# 2. Crear venv e instalar
python -m venv .venv
source .venv/bin/activate  # en Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Configurar secrets
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# Editar y completar:
#   - app_password
#   - contabilium_client_id / contabilium_client_secret
#   - gsheets.spreadsheet_id
#   - gsheets.service_account_json_path (local) o
#     [gsheets.service_account] (producción)

# 4. Correr
streamlit run app.py
```

## Google Sheet — setup inicial

1. Crear un Google Sheet nuevo. Anotar su ID (la parte de la URL entre
   `/d/` y `/edit`).
2. Crear un Service Account en Google Cloud Console + descargar el JSON
   de credenciales.
3. En el Sheet, compartirlo con el `client_email` del service account
   como **Editor**.
4. La primera vez que la app escriba, crea automáticamente la tab
   `costos_historico` con headers correctos.

Schema de la tab `costos_historico`:

| sku | costo | fecha_vigencia_desde | fecha_carga | usuario | nota |
|---|---|---|---|---|---|

## Despliegue a Streamlit Cloud

1. Pushear repo a GitHub.
2. En Streamlit Cloud → "New app" → seleccionar repo y branch.
3. En "Advanced settings" → Secrets, pegar el contenido de
   `secrets.toml` adaptado (descomentando el bloque
   `[gsheets.service_account]` con el JSON inline).
4. Custom domain → `contabilidad.streamlit.app`.

## Dependencias entre secciones

- **Productos** sirve la plantilla pre-llenada para **Carga de costos**.
- **Carga de costos** alimenta el Sheet que consume **COGS mensual**.
- **COGS mensual** depende de tener al menos una carga en el Sheet con
  vigencia anterior a la fecha de las facturas del período.

## Convenciones

- Costo se guarda **neto sin IVA**, en UYU. Misma unidad que el `monto`
  de las ventas (que ya es neto).
- Cada carga aplica una sola `fecha_vigencia_desde` a todos los SKUs del
  lote — la elección por carga (no por fila) mantiene la operación simple.
- El catálogo de productos se cachea 1h; la facturación mensual, 30min.
  Botón "Sincronizar ahora" en Productos invalida el cache.
