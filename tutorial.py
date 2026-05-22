"""
tutorial.py — Contenido del tutorial de la webapp Contabilidad.

Se pinta dentro de la pestaña "Tutorial". Pensado para que cualquier
persona de Suprabond pueda abrir la app por primera vez y entender,
sin que nadie le explique en vivo, qué hace cada sección.

El contenido vive solo en este módulo. Para actualizarlo, se edita
acá sin tocar app.py ni el resto del código.
"""

import streamlit as st


def render() -> None:
    """Renderiza el contenido completo del tutorial."""

    st.markdown("## Tutorial")
    st.caption("Cómo usar esta herramienta, sección por sección.")

    st.markdown(
        """
        ### ¿Qué es esta aplicación?

        Es la herramienta de **contabilidad de costos de GSU**. Sirve
        para dos cosas:

        1. **Mantener los costos internos de cada producto** (SKU),
           cargándolos desde una planilla.
        2. **Calcular el costo de la mercadería vendida (COGS)** de un
           mes: cuánto costó —a precio de costo— todo lo que se
           facturó en ese período, y por lo tanto cuál fue el
           **margen bruto**.

        Los datos de productos y de facturación se leen en vivo desde
        **Contabilium**. Los costos se cargan y guardan en una
        **planilla de Google** propia de esta herramienta.
        """
    )

    st.divider()

    st.markdown(
        """
        ### Sección 1 — Productos

        Muestra el **catálogo completo** de productos tal como está
        hoy en Contabilium: SKU, nombre, precio de venta, stock y el
        costo cargado en el ERP.

        - El botón **Sincronizar ahora** vuelve a traer el catálogo
          desde Contabilium (normalmente se actualiza solo cada hora).
        - La columna **Costo Contabilium** es solo informativa: es lo
          que figura en el ERP, que hoy está casi vacío. **No es** el
          costo que usa esta herramienta para calcular.
        - Abajo de todo podés **descargar una plantilla** (CSV o XLSX)
          con todos los SKUs activos, lista para completar los costos
          y volver a subirla en la sección siguiente.
        """
    )

    st.divider()

    st.markdown(
        """
        ### Sección 2 — Carga de costos

        Es donde se define **el costo real de cada producto**. Esta es
        la fuente de verdad: lo que cargues acá es lo que se usa para
        calcular el COGS.

        **Cómo cargar costos:**

        1. Preparar una planilla (`.csv` o `.xlsx`) con dos columnas
           obligatorias: **`sku`** y **`costo`**. Opcionalmente una
           tercera columna **`nota`**.
        2. Subirla en **1. Subir archivo**.
        3. En **2. Fecha de vigencia**, elegir desde qué fecha aplican
           esos costos. Importante: **todos los productos de esa carga
           empiezan a regir desde la fecha que elijas**.
        4. En **3. Previsualizar**, revisar el cuadro: te muestra el
           costo anterior, el nuevo, la variación y si el SKU existe
           en el catálogo de Contabilium.
        5. En **4. Guardar**, confirmar. Las filas se agregan a la
           planilla de Google.

        > **El histórico nunca se pisa.** Cada carga se *suma* a las
        > anteriores. Si en enero cargaste un costo y en mayo lo
        > cargás de nuevo más caro, quedan los dos: el cálculo de COGS
        > de enero usa el de enero, y el de mayo usa el de mayo.

        > Los productos que **no incluyas** en una carga mantienen su
        > último costo vigente. No hace falta volver a cargar todo
        > cada vez, solo lo que cambió.
        """
    )

    st.divider()

    st.markdown(
        """
        ### Sección 3 — COGS mensual

        Calcula, para el mes que elijas, el **costo de la mercadería
        vendida** y el **margen bruto**.

        1. Elegí **año** y **mes**.
        2. Tocá **Calcular COGS del período**.
        3. La herramienta trae toda la facturación de ese mes desde
           Contabilium y, para cada producto vendido, busca su costo
           **vigente a la fecha de cada factura**.

        **Qué vas a ver:**

        - **Venta neta**: lo facturado en el mes (sin IVA).
        - **COGS**: el costo de todo lo vendido.
        - **Margen bruto** y **Margen %**: la diferencia entre ambos.
        - **COGS por producto**: el detalle SKU por SKU.
        - **Panel de salud**: si hay productos que se vendieron pero
          **no tienen costo cargado**, aparecen acá. Esos productos
          quedan fuera del cálculo del COGS hasta que les cargues el
          costo en la Sección 2 y recalcules.

        > Las **notas de crédito** (devoluciones) se restan, igual que
        > en la venta. El COGS refleja la mercadería neta vendida.
        """
    )

    st.divider()

    st.markdown(
        """
        ### Conceptos importantes

        - **Los costos van netos, sin IVA.** Misma base que la venta
          neta, para que el margen tenga sentido.
        - **Fecha de vigencia**: el costo de un producto puede cambiar
          en el tiempo. Por eso cada carga lleva una fecha desde la
          cual rige. Así, el COGS de un mes pasado usa el costo que
          correspondía *en ese momento*, no el de hoy.
        - **Orden recomendado de uso**: Productos (para bajar la
          plantilla) → Carga de costos (para cargarlos) → COGS mensual
          (para calcular).
        - Para **cerrar sesión**, usá el botón en la barra lateral.
        """
    )
