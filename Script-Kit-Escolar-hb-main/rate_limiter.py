import time
import streamlit as st
from fastapi import Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address

# ==========================================
# 1. CONFIGURAÇÃO PARA FASTAPI (api_baixas.py)
# ==========================================
limiter = Limiter(key_func=get_remote_address)

def setup_fastapi_limiter(app):
    """Vincula o middleware de rate limit à instância do FastAPI."""
    app.state.limiter = limiter
    app.add_exception_handler(429, _rate_limit_exceeded_handler)


# ==========================================
# 2. CONFIGURAÇÃO PARA STREAMLIT (kit-colaborador.py)
# ==========================================
def verificar_limite_clique(chave_acao: str, intervalo_segundos: int = 5) -> bool:
    """
    Bloqueia cliques muito rápidos no Streamlit.
    Retorna True se o clique for PERMITIDO, ou False se for BLOQUEADO.
    """
    agora = time.time()
    chave_estado = f"ultimo_clique_{chave_acao}"

    if chave_estado in st.session_state:
        tempo_decorrido = agora - st.session_state[chave_estado]
        if tempo_decorrido < intervalo_segundos:
            tempo_restante = int(intervalo_segundos - tempo_decorrido) + 1
            st.error(f"⏳ Aguarde {tempo_restante} segundo(s) antes de tentar novamente para não sobrecarregar o sistema.")
            return False

    # Atualiza o timestamp do último clique
    st.session_state[chave_estado] = agora
    return True