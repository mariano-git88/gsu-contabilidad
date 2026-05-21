"""
auth.py — Login con contraseña única para la webapp de Contabilidad.

Una sola password compartida (single-user, Mariano). Vive en
st.secrets["contabilidad_password"]:
  - Localmente en .streamlit/secrets.toml
  - En producción en Streamlit Cloud → Settings → Secrets

Para cambiar la password en producción: editar el secret en el dashboard
de Streamlit Cloud y la app se redeploya sola.

Heredado del dashboard GSU con texto adaptado.
"""

import hmac

import streamlit as st


def _verify(stored_password: str, entered_password: str) -> bool:
    return hmac.compare_digest(stored_password, entered_password)


def check_password() -> bool:
    """
    True si el usuario está autenticado en esta sesión.

    Si no, pinta el formulario de login y devuelve False. El caller
    debe `st.stop()` cuando reciba False para no renderizar el resto.
    """
    if st.session_state.get("authenticated", False):
        return True

    left, center, right = st.columns([1, 2, 1])
    with center:
        st.markdown(
            "<h1 style='margin-bottom:0.25rem;'>Contabilidad Suprabond</h1>",
            unsafe_allow_html=True,
        )
        st.caption(
            "Costos internos por SKU y cálculo de COGS mensual. "
            "Acceso restringido."
        )

        with st.form("login_form", clear_on_submit=False):
            password = st.text_input(
                "Contraseña",
                type="password",
                autocomplete="current-password",
                placeholder="••••••••",
            )
            submit = st.form_submit_button("Ingresar", use_container_width=True)

        if submit:
            stored = st.secrets.get("contabilidad_password")
            if stored is None:
                st.error(
                    "El administrador del sitio aún no configuró la "
                    "contraseña en Streamlit Cloud."
                )
                return False

            if _verify(stored, password):
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Contraseña incorrecta.")

    return False


def logout_button() -> None:
    if not st.session_state.get("authenticated"):
        return
    with st.sidebar:
        st.markdown("---")
        if st.button("Cerrar sesión", use_container_width=True):
            st.session_state.pop("authenticated", None)
            st.rerun()
