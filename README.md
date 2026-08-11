# Contabilidad Suprabond

Webapp Streamlit para visibilidad de costos internos por SKU, cálculo de
costo de mercadería vendida (COGS) por período y análisis de precios. Vive en
`contabilidad.streamlit.app`.

## Cuatro secciones

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

4. **Precios** — Cruza los últimos 12 meses de facturación con el costo
   vigente y clasifica cada SKU con un semáforo: cuáles aguantan una
   suba de precio, cuáles no hay que tocar y cuáles se están vendiendo
   por debajo del costo. Incluye simulador de suba y registro de las
   decisiones para medirlas después.

   La pregunta que responde es "¿a qué códigos les puedo subir el precio
   sin perder unidades?". **El camino obvio no sirve**: en venta mayorista
   el descuento lo genera el volumen, así que correlacionar precio contra
   unidades siempre da "bajar el precio vende más" — que es causalidad
   invertida. El análisis usa tres lentes que esquivan eso:

   - *Experimento hacia abajo*: bajaron el precio y el volumen NO subió.
   - *Experimento hacia arriba*: subieron el precio y el volumen NO cayó.
   - *Piso de marca*: quedó bajo `costo × 1,85` y nunca respondió a bajas.

   Controles obligatorios, sin los cuales el análisis miente: se descartan
   los SKU estacionales (se detecta por concentración en 5 meses
   *consecutivos*, no se hardcodea), los que tuvieron quiebre de stock
   (semanas sin venta que aumentan respecto del período anterior) y los
   lanzamientos sin historia comparable.

   **Límite conocido:** se observa el precio promedio *realizado*
   (monto/unidades), no el precio de lista. Una "suba" puede ser
   simplemente que ese mes compraron menos clientes bonificados.

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
