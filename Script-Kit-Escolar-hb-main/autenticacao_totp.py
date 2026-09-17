import io
import base64
import re
import pyotp
import qrcode
import streamlit as st
from sqlalchemy import text
from datetime import datetime, date, timedelta, timezone
from database import (
    registrar_log, 
    conta_esta_bloqueada, 
    obter_ip_cliente, 
    SessionLocal, 
    LogAuditoria
)


def gerar_secret_totp():
    """Gera uma nova chave secreta Base32."""
    return pyotp.random_base32()


def gerar_qrcode_base64(secret_key: str, nome_usuario: str) -> str:
    """Gera o QR Code em formato imagem Base64 para exibição nativa no Streamlit."""
    totp = pyotp.TOTP(secret_key)
    uri = totp.provisioning_uri(name=nome_usuario, issuer_name="Funfarme - Kit Escolar")
    
    img = qrcode.make(uri)
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


def validar_codigo_totp(secret_key: str, codigo_digitado: str) -> bool:
    """Valida o código de 6 dígitos com tolerância de relógio."""
    if not secret_key or not codigo_digitado:
        return False
    secret_limpa = str(secret_key).strip()
    codigo_limpo = str(codigo_digitado).strip()
    totp = pyotp.TOTP(secret_limpa)
    return totp.verify(codigo_limpo, valid_window=3)


def verificar_autenticacao_totp(colaborador: dict, engine_db) -> bool:
    """Interface do Streamlit para o fluxo de TOTP com Trava Dupla, Proteção de Conta e Auditoria."""
    st.subheader("🔒 Validação de Segurança (2FA)")

    ip_atual = obter_ip_cliente()
    
    # Variáveis do colaborador
    nome_colab = colaborador.get("Nome") or colaborador.get("nome") or "Colaborador"
    cracha_colab = colaborador.get("Crachá") or colaborador.get("cracha") or colaborador.get("id")
    secret_cadastrado = colaborador.get("totp_secret")
    
    data_nasc_banco = colaborador.get("data_nascimento")
    cpf_banco = str(colaborador.get("cpf", "")).strip().zfill(11)

    LIMITE_FALHAS = 5
    MINUTOS_BLOQUEIO = 5

    # 🛑 BARREIRA PREVENTIVA: Bloqueia o Crachá se houver falhas acumuladas nos últimos 5 min
    if conta_esta_bloqueada(cracha_colab, limite_falhas=LIMITE_FALHAS, minutos=MINUTOS_BLOQUEIO):
        st.error(
            f"🚨 **Acesso temporariamente bloqueado por segurança.**\n\n"
            f"Detectamos múltiplas tentativas de acesso incorretas para o crachá `{cracha_colab}`. "
            f"Por favor, aguarde **{MINUTOS_BLOQUEIO} minutos** antes de tentar novamente."
        )
        return False

    # Função auxiliar para buscar falhas reais direto no banco
    def obter_tentativas_restantes():
        db = SessionLocal()
        try:
            limite_tempo = datetime.now(timezone.utc) - timedelta(minutes=MINUTOS_BLOQUEIO)
            total_falhas = db.query(LogAuditoria).filter(
                LogAuditoria.cracha == cracha_colab,
                LogAuditoria.acao.in_(["FALHA_CONFIRMACAO_IDENTIDADE", "FALHA_ATIVACAO_TOTP", "FALHA_LOGIN_TOTP"]),
                LogAuditoria.data_hora >= limite_tempo
            ).count()
            return max(0, LIMITE_FALHAS - total_falhas)
        finally:
            db.close()

    # -------------------------------------------------------------
    # FLUXO 1: PRIMEIRO ACESSO (Confirmação de Identidade pré-QR Code)
    # -------------------------------------------------------------
    if not colaborador.get("totp_ativo") or not secret_cadastrado:
        
        if "identidade_confirmada" not in st.session_state:
            st.session_state.identidade_confirmada = False

        if not st.session_state.identidade_confirmada:
            st.info("👋 **Primeiro acesso detectado!** Para sua segurança, confirme seus dados cadastrais para liberar a configuração do aplicativo autenticador:")

            with st.form("form_valida_identidade"):
                st.write(f"Colaborador: **{nome_colab}** | Crachá: **{cracha_colab}**")
                
                col1, col2 = st.columns(2)
                with col1:
                    cpf_digitado_input = st.text_input(
                        "CPF Completo:", 
                        max_chars=14, 
                        placeholder="Ex: 123.456.789-00",
                        help="Digite todos os 11 dígitos do seu CPF (com ou sem pontuação)"
                    )
                with col2:
                    dt_informada = st.date_input(
                        "Data de Nascimento:",
                        min_value=date(1940, 1, 1),
                        max_value=date.today(),
                        format="DD/MM/YYYY"
                    )
                
                btn_validar_dados = st.form_submit_button("Confirmar Identidade", type="primary", use_container_width=True)

            if btn_validar_dados:
                data_nasc_convertida = None
                if data_nasc_banco:
                    if hasattr(data_nasc_banco, "date"):
                        data_nasc_convertida = data_nasc_banco.date()
                    elif isinstance(data_nasc_banco, date):
                        data_nasc_convertida = data_nasc_banco
                    elif isinstance(data_nasc_banco, str):
                        for formato in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
                            try:
                                data_nasc_convertida = datetime.strptime(data_nasc_banco.strip(), formato).date()
                                break
                            except ValueError:
                                continue

                cpf_digitado_limpo = re.sub(r'\D', '', cpf_digitado_input).zfill(11)
                cpf_banco_limpo = re.sub(r'\D', '', cpf_banco).zfill(11)

                valida_data = (data_nasc_convertida and dt_informada == data_nasc_convertida)
                valida_cpf = (len(cpf_digitado_limpo) == 11 and cpf_digitado_limpo == cpf_banco_limpo)

                if valida_data and valida_cpf:
                    st.session_state.identidade_confirmada = True
                    registrar_log(cracha_colab, "CONFIRMACAO_IDENTIDADE_SUCESSO", "Data de nascimento e CPF completo validados com sucesso.", ip_origem=ip_atual)
                    st.success("✅ Identidade confirmada!")
                    st.rerun()
                else:
                    erros_identidade = []
                    if not valida_cpf:
                        erros_identidade.append("CPF incorreto ou incompleto.")
                    if not valida_data:
                        erros_identidade.append("Data de nascimento incorreta.")
                    
                    msg_detalhes = " ".join(erros_identidade)
                    registrar_log(cracha_colab, "FALHA_CONFIRMACAO_IDENTIDADE", f"Tentativa negada: {msg_detalhes}", ip_origem=ip_atual)
                    
                    restantes = obter_tentativas_restantes()
                    st.error(f"❌ Falha na validação: {msg_detalhes} Você tem **{restantes}** tentativa(s) restante(s) antes do bloqueio de {MINUTOS_BLOQUEIO} minutos.")
                    if restantes == 0:
                        st.rerun()
            
            return False

        # -------------------------------------------------------------
        # LIBERAÇÃO DO QR CODE / CHAVE MANUAL APÓS CONFIRMAR IDENTIDADE
        # -------------------------------------------------------------
        if "temp_totp_secret" not in st.session_state:
            st.session_state.temp_totp_secret = pyotp.random_base32()

        secret = st.session_state.temp_totp_secret

        totp = pyotp.TOTP(secret)
        uri = totp.provisioning_uri(name=nome_colab, issuer_name="Funfarme - Kit Escolar")
        
        img = qrcode.make(uri)
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        img_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

        st.info("Escolha como deseja configurar o seu aplicativo autenticador:")

        tab_celular, tab_pc = st.tabs(["📱 Estou acessando pelo Celular", "💻 Estou acessando pelo Computador"])

        with tab_celular:
            st.markdown("### 📋 Configuração Manual")
            st.caption("Clique no ícone de copiar à direita do código abaixo:")
            st.code(secret, language=None)
            st.markdown("---")
            st.markdown("""
            **Passo a passo no celular:**
            1. Abra o app **Google Authenticator** ou **Authy**.
            2. Clique no **+** (canto inferior direito).
            3. Escolha **Inserir chave de configuração**.
            4. Nome da conta: `Funfarme - Kit Escolar`.
            5. Cole a chave copiada acima e clique em **Adicionar**.
            6. **Copie o código de 6 dígitos gerado no app e cole no campo abaixo.**
            """)

        with tab_pc:
            st.markdown("### 📷 Ler QR Code")
            st.caption("Ideal se você está no computador e com o celular em mãos:")
            st.markdown(
                f'<div style="text-align: center; margin: 15px 0;"><img src="data:image/png;base64,{img_b64}" width="200"></div>',
                unsafe_allow_html=True
            )
            st.markdown("---")
            st.markdown("""
            **Passo a passo no computador:**
            1. Abra o app **Google Authenticator** ou **Authy** no seu celular.
            2. Clique no **+** (canto inferior direito).
            3. Escolha **Ler QR Code**.
            4. Aponte a câmera do celular para a imagem acima.
            5. **Digite o código de 6 dígitos gerado no app no campo abaixo.**
            """)

        st.divider()

        with st.form("form_ativar_totp"):
            codigo_ativacao = st.text_input("Digite o código de 6 dígitos gerado no aplicativo:", max_chars=6)
            btn_ativar = st.form_submit_button("Ativar Segurança e Acessar", type="primary", use_container_width=True)

        if btn_ativar:
            if validar_codigo_totp(secret, codigo_ativacao):
                query_update = """
                    UPDATE colaboradores 
                    SET totp_secret = :secret, totp_ativo = TRUE 
                    WHERE cracha = :cracha
                """
                with engine_db.begin() as conn:
                    conn.execute(text(query_update), {"secret": str(secret).strip(), "cracha": cracha_colab})

                colaborador["totp_secret"] = str(secret).strip()
                colaborador["totp_ativo"] = True
                st.session_state.autenticado_totp = True
                
                registrar_log(cracha_colab, "TOTP_ATIVADO_SUCESSO", "Primeiro acesso concluído e chave 2FA vinculada ao dispositivo.", ip_origem=ip_atual)

                if "temp_totp_secret" in st.session_state:
                    del st.session_state.temp_totp_secret
                if "identidade_confirmada" in st.session_state:
                    del st.session_state.identidade_confirmada

                st.success("✅ Validador configurado com sucesso!")
                return True
            else:
                registrar_log(cracha_colab, "FALHA_ATIVACAO_TOTP", "Código de 6 dígitos incorreto durante a tentativa de ativação.", ip_origem=ip_atual)
                
                restantes = obter_tentativas_restantes()
                st.error(f"❌ Código incorreto. Você tem **{restantes}** tentativa(s) restante(s) antes do bloqueio de {MINUTOS_BLOQUEIO} minutos.")
                
                if restantes <= 3:
                    st.warning("⚠️ **Dica de relógio:** Verifique se a **data e a hora** do seu celular/dispositivo estão configuradas para sincronização automática com a rede. Relógios dessincronizados impedem a validação do código.")
                
                if restantes == 0:
                    st.rerun()

        return False

    # -------------------------------------------------------------
    # FLUXO 2: ACESSOS RECORRENTES (Já configurado anteriormente)
    # -------------------------------------------------------------
    else:
        with st.form("form_login_totp"):
            st.write(f"Olá, **{nome_colab}**! Digite o código de 6 dígitos do seu aplicativo autenticador:")
            codigo_login = st.text_input("Código do Authenticator", max_chars=6)
            btn_entrar = st.form_submit_button("Entrar no Sistema", type="primary", use_container_width=True)

        if btn_entrar:
            if validar_codigo_totp(secret_cadastrado, codigo_login):
                registrar_log(cracha_colab, "LOGIN_TOTP_SUCESSO", "Acesso autorizado via código 2FA.", ip_origem=ip_atual)
                return True
            else:
                registrar_log(cracha_colab, "FALHA_LOGIN_TOTP", "Código TOTP inválido ou expirado digitado no login recorrente.", ip_origem=ip_atual)
                
                restantes = obter_tentativas_restantes()
                st.error(f"❌ Código inválido ou expirado. Você tem **{restantes}** tentativa(s) restante(s) antes do bloqueio temporário de {MINUTOS_BLOQUEIO} minutos.")
                
                if restantes <= 3:
                    st.warning("⚠️ **Dica importante:** Verifique se a **data e a hora do seu celular** estão sincronizadas automaticamente. Diferenças de horário invalidam os códigos gerados pelo autenticador.")
                
                if restantes == 0:
                    st.rerun()

        return False