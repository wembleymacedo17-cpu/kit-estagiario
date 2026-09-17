import streamlit as st
import os
import json
import requests
import re
from io import BytesIO
from PIL import Image
import google.generativeai as genai
from datetime import datetime
from database import SessionLocal, Dependente, Colaborador, Retirada, EscolhaKit, LogAuditoria
from notificador_revisao import NotificadorEmail, SMTP_SERVER, SMTP_PORT, LOGIN_SMTP, SENHA_KEY, EMAIL_REMETENTE

st.set_page_config(page_title="Revisão RH - Documentos", layout="wide")

# ===================== MODAIS DE CONFIRMAÇÃO (POP-UPS) =====================

@st.dialog("⚠️ Confirmar Troca de Colaborador")
def modal_confirmar_troca(item_novo):
    st.write("Você está prestes a alternar a revisão de documentos para:")
    st.info(f"👉 **{item_novo}**")
    st.write("Deseja confirmar o carregamento deste registro?")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ Confirmar", type="primary", use_container_width=True):
            st.session_state.dep_confirmado = item_novo
            st.session_state.dep_pendente = None
            st.rerun()
    with col2:
        if st.button("❌ Cancelar", use_container_width=True):
            st.session_state.dep_pendente = None
            st.session_state.selectbox_revisor_quarentena = st.session_state.dep_confirmado
            st.rerun()


@st.dialog("✅ Confirmar Aprovação de Documentos")
def modal_confirmar_aprovacao(id_dep, cracha_col, nome_col, email_dest, corpo_email, nome_filho):
    st.markdown("Você está prestes a **APROVAR** a documentação do dependente:")
    st.success(f"👦 **Dependente:** {nome_filho}\n\n👤 **Colaborador:** {nome_col} (Crachá: {cracha_col})")
    st.write(f"📧 **E-mail de Notificação:** `{email_dest}`")
    st.warning("⚠️ O status será atualizado no banco de dados e o e-mail de confirmação será disparado.")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ Sim, Finalizar Aprovação", type="primary", use_container_width=True, key="btn_modal_sim_aprov"):
            with st.spinner("⏳ Processando aprovação, atualizando banco e enviando e-mail..."):
                ok = aprovar_documento(id_dep, cracha_col, nome_col, email_dest, corpo_email)
                if ok:
                    st.session_state.dep_confirmado = None  # Limpa seleção para evitar auto-carregamento imediato
                    st.toast("✅ Aprovado com sucesso!", icon="🎉")
                    st.rerun()
                else:
                    st.error("⚠️ Status atualizado no banco, mas falhou ao enviar o e-mail.")
    with col2:
        if st.button("❌ Voltar / Cancelar", use_container_width=True, key="btn_modal_cancel_aprov"):
            st.rerun()


@st.dialog("❌ Confirmar Reprovação e Liberação")
def modal_confirmar_reprovacao(id_dep, cracha_col, nome_col, email_dest, corpo_email, nome_filho):
    st.markdown("Você está prestes a **REPROVAR** a documentação do dependente:")
    st.error(f"👦 **Dependente:** {nome_filho}\n\n👤 **Colaborador:** {nome_col} (Crachá: {cracha_col})")
    st.write(f"📧 **E-mail de Notificação:** `{email_dest}`")
    st.warning("⚠️ O cadastro atual será **removido** para permitir que o colaborador refaça a solicitação.")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("❌ Sim, Confirmar Reprovação", type="primary", use_container_width=True, key="btn_modal_sim_reprov"):
            with st.spinner("⏳ Processando reprovação, limpando cadastro e enviando e-mail..."):
                ok = enviar_email_reprovacao(id_dep, cracha_col, nome_col, email_dest, corpo_email)
                if ok:
                    st.session_state.dep_confirmado = None  # Limpa seleção para evitar auto-carregamento imediato
                    st.toast("📧 Reprovado e cadastro liberado!", icon="✅")
                    st.rerun()
                else:
                    st.error("❌ Falha ao enviar o e-mail. Registro mantido por segurança.")
    with col2:
        if st.button(" Voltar / Cancelar", use_container_width=True, key="btn_modal_cancel_reprov"):
            st.rerun()

# ===================== CONFIGURAÇÃO GEMINI =====================
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

def analisar_documentos_com_ia(doc_identidade_fonte, declaracao_fonte, doc_vinculo_fonte, motivo_reprova, nome_colaborador, nome_filho):
    try:
        conteudo_gemini = []
        
        def processar_fonte(fonte, rotulo):
            if not fonte:
                return
            if isinstance(fonte, dict):
                bytes_data = fonte["bytes"]
                nome_arq = fonte["name"].lower()
                if nome_arq.endswith('.pdf'):
                    conteudo_gemini.append({"mime_type": "application/pdf", "data": bytes_data})
                else:
                    conteudo_gemini.append(f"--- {rotulo} ---")
                    conteudo_gemini.append(Image.open(BytesIO(bytes_data)))
            elif isinstance(fonte, str):
                resp = requests.get(fonte, timeout=10)
                resp.raise_for_status()
                if fonte.lower().endswith('.pdf'):
                    conteudo_gemini.append({"mime_type": "application/pdf", "data": resp.content})
                else:
                    conteudo_gemini.append(f"--- {rotulo} (URL) ---")
                    conteudo_gemini.append(Image.open(BytesIO(resp.content)))

        processar_fonte(doc_identidade_fonte, "Documento de Identidade")
        processar_fonte(declaracao_fonte, "Declaração Escolar")
        processar_fonte(doc_vinculo_fonte, "Documento de Vínculo")

        if not conteudo_gemini:
            return {"sugestao_acao": "REPROVAR", "analise_tecnica_rh": "⚠️ Nenhum documento fornecido para a IA.", "corpo_email": ""}

        model = genai.GenerativeModel(
            'gemini-3.1-flash-lite',
            generation_config={"response_mime_type": "application/json"}
        )
        
        motivo_texto = motivo_reprova or "Não especificado pelo sistema"
        data_hoje_str = datetime.now().strftime("%d/%m/%Y")
        ano_atual_str = datetime.now().strftime("%Y")

        prompt = f"""
        Você é um auditor sênior de Recursos Humanos da instituição Funfarme atuando em SEGUNDA INSTÂNCIA DE REVISÃO.
        
        O cadastro do dependente '{nome_filho}' do colaborador '{nome_colaborador}' foi retido em quarentena pelo sistema automatizado.
        Motivo apontado pela automação inicial: '{motivo_texto}'.
        Data atual do sistema: {data_hoje_str} (Ano Letivo: {ano_atual_str}).

        🚨 DIRETRIZES DE AUDITORIA E TOLERÂNCIA A ERROS DE OCR:

        1. VALIDAÇÃO VISUAL INDEPENDENTE (ANTI-VÍCIO DE CONFIRMAÇÃO):
           - A automação primária utiliza OCR eletrônico que frequentemente erra a leitura de números e caracteres em documentos.
           - Se os dados impressos visualmente no documento estiverem corretos e a divergência for um falso positivo de OCR, desconsidere o bloqueio e APROVE O CADASTRO.

        2. DATAS DE EMISSÃO DA DECLARAÇÃO ESCOLAR:
           - Documentos emitidos no ano corrente ou em anos imediatamente anteriores são válidos. Só aponte "data futura" se o ano for maior que {ano_atual_str}.

        3. VERIFICAÇÃO DE UNIÃO ESTÁVEL / CASAMENTO (FLUXO B1):
           - Se um Documento de Vínculo for anexado (União Estável ou Certidão de Casamento), verifique obrigatoriamente se o nome do colaborador '{nome_colaborador}' ou da mãe/pai consta no documento e se possui carimbo ou firma reconhecida.

        4. CRITÉRIO DE DECISÃO:
           - Se o documento estiver legível, íntegro e a suposta divergência for erro eletrônico, aprove.
           - Se houver inconsistência real, reprove indicando a correção necessária.

        REGRA OBRIGATÓRIA DO E-MAIL DE REPROVAÇÃO:
        - Sempre oriente o colaborador a ACESSAR O SISTEMA NOVAMENTE para refazer o cadastro.

        ESQUEMA JSON OBRIGATÓRIO:
        {{
            "sugestao_acao": "APROVAR" ou "REPROVAR",
            "analise_tecnica_rh": "Explicação técnica detalhada.",
            "corpo_email": "Olá, {nome_colaborador}. [Rascunho de e-mail de orientações do RH]."
        }}
        """
        
        conteudo_gemini.insert(0, prompt)
        resposta = model.generate_content(conteudo_gemini)
        return json.loads(resposta.text.strip())
    except Exception as e:
        return {
            "sugestao_acao": "REPROVAR",
            "analise_tecnica_rh": f"⚠️ Erro ao processar análise da IA: {e}",
            "corpo_email": f"Olá, {nome_colaborador}.\n\nPor favor, revise os documentos enviados para o dependente {nome_filho} e efetue o recadastro no sistema.\n\nAtenciosamente,\nEquipe de Recursos Humanos"
        }

# ===================== FUNÇÕES DE BANCO E E-MAIL =====================

def buscar_dependentes_revisao():
    db = SessionLocal()
    try:
        return db.query(
            Dependente,
            Colaborador.nome.label("nome_colaborador"),
            Colaborador.cracha.label("cracha_colaborador"),
            Retirada.email.label("email_retirada")
        ).join(
            Colaborador, Colaborador.cracha == Dependente.id_colaborador
        ).outerjoin(
            Retirada, Retirada.id_colaborador == Dependente.id_colaborador
        ).filter(
            Dependente.revisao_rh != 'Não'
        ).all()
    finally:
        db.close()

def limpar_motivo(detalhes):
    """Extrai a mensagem central do erro removendo prefixos de sistema."""
    if not detalhes:
        return "Sem detalhes"
    texto = str(detalhes).strip()
    texto = re.sub(r'^(Bypass forçado \([^\)]+\)\. Erro contornado:\s*|IA Rejeitou [^:]+:\s*|IA Rejeitou:\s*|RH reprovou dependente [^:]+:\s*)', '', texto, flags=re.IGNORECASE)
    return texto.strip()


def buscar_estatisticas_seguranca():
    db = SessionLocal()
    try:
        logs = db.query(LogAuditoria).filter(
            LogAuditoria.acao.in_(["IA_VALIDACAO_FALHA", "ENVIO_RH_BYPASS", "REPROVACAO_RH"])
        ).order_by(LogAuditoria.data_hora.asc()).all()
        
        violacoes = [l for l in logs if l.acao == "ENVIO_RH_BYPASS"]
        
        tentativas_unicas = set()
        agrupamento = {}

        for log in logs:
            if not log.cracha:
                continue
                
            cracha_val = int(log.cracha)
            motivo_limpo = limpar_motivo(log.detalhes)
            
            if log.data_hora:
                minuto_str = log.data_hora.strftime("%Y-%m-%d %H:%M")
            else:
                minuto_str = "sem_data"
            
            chave_tentativa = (cracha_val, motivo_limpo, minuto_str)
            
            if chave_tentativa in tentativas_unicas:
                continue
            tentativas_unicas.add(chave_tentativa)

            chave_grupo = (cracha_val, motivo_limpo)
            if chave_grupo not in agrupamento:
                agrupamento[chave_grupo] = {
                    "cracha": cracha_val,
                    "motivo": motivo_limpo,
                    "qtd": 0,
                    "ultima_data": log.data_hora
                }
            
            agrupamento[chave_grupo]["qtd"] += 1
            if log.data_hora and (agrupamento[chave_grupo]["ultima_data"] is None or log.data_hora > agrupamento[chave_grupo]["ultima_data"]):
                agrupamento[chave_grupo]["ultima_data"] = log.data_hora

        reincidentes = [item for item in agrupamento.values() if item["qtd"] >= 2]
        reincidentes.sort(key=lambda x: x["qtd"], reverse=True)
        
        crachas_unicos = len(set(r["cracha"] for r in reincidentes))
        
        return len(violacoes), crachas_unicos, reincidentes, violacoes
    finally:
        db.close()


def verificar_bloqueio_ia_3a_tentativa(cracha, motivo_atual=None):
    if not cracha:
        return False
        
    db = SessionLocal()
    try:
        cracha_num = int(cracha)
        logs = db.query(LogAuditoria).filter(
            LogAuditoria.cracha == cracha_num,
            LogAuditoria.acao.in_(["IA_VALIDACAO_FALHA", "ENVIO_RH_BYPASS", "REPROVACAO_RH"])
        ).order_by(LogAuditoria.data_hora.asc()).all()

        if not logs:
            return False

        motivo_pesquisa = limpar_motivo(motivo_atual).lower() if motivo_atual else None
        
        tentativas_unicas = set()
        contagem_tentativas = 0

        for log in logs:
            motivo_log = limpar_motivo(log.detalhes).lower()
            minuto_str = log.data_hora.strftime("%Y-%m-%d %H:%M") if log.data_hora else "sem_data"
            
            chave_tentativa = (cracha_num, motivo_log, minuto_str)
            if chave_tentativa in tentativas_unicas:
                continue
            tentativas_unicas.add(chave_tentativa)

            if not motivo_pesquisa or (motivo_pesquisa in motivo_log or motivo_log in motivo_pesquisa):
                contagem_tentativas += 1

        return contagem_tentativas >= 3
    except Exception:
        return False
    finally:
        db.close()

def aprovar_documento(id_dependente, cracha_colaborador, nome_colaborador, email_destino, corpo_personalizado=None):
    db = SessionLocal()
    try:
        dependente = db.query(Dependente).filter(Dependente.id_dependente == id_dependente).first()
        if dependente:
            dependente.revisao_rh = 'Não'
            
        retirada = db.query(Retirada).filter(Retirada.id_colaborador == cracha_colaborador).first()
        if retirada:
            retirada.status = 'PENDENTE'
            
        db.commit()
        
        notificador = NotificadorEmail(
            smtp_server=SMTP_SERVER,
            smtp_port=SMTP_PORT,
            login_smtp=LOGIN_SMTP,
            senha=SENHA_KEY
        )
        
        assunto = "✅ Cadastro Aprovado - Kit Escolar"
        
        if corpo_personalizado and corpo_personalizado.strip():
            corpo_email = corpo_personalizado
        else:
            corpo_email = f"Olá, {nome_colaborador}.\n\nO RH analisou o documento enviado para o dependente {dependente.nome_filho} e sua documentação foi APROVADA com sucesso!\n\nSeu kit já está registrado no nosso sistema. Em breve divulgaremos as datas de entrega.\n\nAtenciosamente,\nEquipe de Recursos Humanos"

        return notificador.disparar(remetente=EMAIL_REMETENTE, destinatarios=[email_destino], assunto=assunto, corpo=corpo_email)
    except Exception as e:
        db.rollback()
        st.error(f"Erro ao aprovar documento: {e}")
        return False
    finally:
        db.close()

def enviar_email_reprovacao(id_dependente, cracha_colaborador, nome_colaborador, email_destino, corpo_personalizado):
    db = SessionLocal()
    try:
        dependente = db.query(Dependente).filter(Dependente.id_dependente == id_dependente).first()
        if not dependente:
            return False
        
        notificador = NotificadorEmail(
            smtp_server=SMTP_SERVER,
            smtp_port=SMTP_PORT,
            login_smtp=LOGIN_SMTP,
            senha=SENHA_KEY
        )
        
        assunto = "⚠️ Ação Necessária: Correção no Cadastro do Kit Escolar"
        sucesso = notificador.disparar(remetente=EMAIL_REMETENTE, destinatarios=[email_destino], assunto=assunto, corpo=corpo_personalizado)
        
        if sucesso:
            cracha_val = int(cracha_colaborador) if cracha_colaborador is not None else None
            novo_log = LogAuditoria(
                cracha=cracha_val,
                acao="REPROVACAO_RH",
                detalhes=f"RH reprovou dependente {dependente.nome_filho}. Motivo/E-mail enviado."
            )
            db.add(novo_log)
            
            db.query(EscolhaKit).filter(EscolhaKit.id_dependente == id_dependente).delete(synchronize_session=False)
            db.delete(dependente)
            db.query(Retirada).filter(Retirada.id_colaborador == cracha_colaborador).delete(synchronize_session=False)
            db.commit()
            return True
        return False
    except Exception as e:
        db.rollback()
        st.error(f"Erro ao processar reprovação: {e}")
        return False
    finally:
        db.close()

def renderizar_midia(fonte, titulo):
    if isinstance(fonte, dict):
        bytes_data = fonte["bytes"]
        nome_arq = fonte["name"].lower()
        if nome_arq.endswith('.pdf'):
            st.markdown(f"**📄 {titulo} (PDF):**")
            base64_pdf = BytesIO(bytes_data).read()
            st.download_button(f"📥 Baixar/Abrir {titulo}", base64_pdf, file_name=fonte["name"])
        else:
            st.image(Image.open(BytesIO(bytes_data)), caption=titulo, use_container_width=True)
    elif isinstance(fonte, str):
        if fonte.lower().endswith('.pdf'):
            st.markdown(f"**📄 {titulo} (PDF):**")
            st.markdown(f'<iframe src="{fonte}" width="100%" height="500px" type="application/pdf"></iframe>', unsafe_allow_html=True)
            st.markdown(f"🔗 [Clique aqui caso o PDF não abra na tela]({fonte})")
        else:
            st.image(fonte, caption=titulo, use_container_width=True)

# ===================== INTERFACE DO USUÁRIO =====================

st.title("📂 Painel Integrado de Auditoria - RH")

tab_revisao, tab_alertas = st.tabs(["📋 Fila de Revisão de Documentos", "🚨 Alertas e Monitoramento de Segurança"])

# ================= ABA 1: FILA DE REVISÃO =================
with tab_revisao:
    st.write("Avalie os documentos que foram barrados pela Inteligência Artificial ou enviados à força (Bypass) pelos colaboradores.")
    st.divider()

    try:
        registros = buscar_dependentes_revisao()
    except Exception as e:
        st.error(f"🚨 Erro de conexão com o Banco de Dados. Verifique a internet e as credenciais do Supabase.\n\n**Detalhes:** {e}")
        st.stop()

    if not registros:
        st.info("🎉 Excelente! Não há documentos pendentes de revisão no momento.")
    else:
        st.metric(label="📋 Fila de Quarentena RH", value=f"{len(registros)} documento(s) pendente(s)")
        
        opcoes = {
            f"{dep.nome_filho} (Colaborador: {nome_col})": (dep, nome_col, cracha, email_ret) 
            for dep, nome_col, cracha, email_ret in registros
        }
        
        lista_opcoes = list(opcoes.keys())

        # Gerenciamento de Estado para Confirmação via Modal
        if "dep_confirmado" not in st.session_state or st.session_state.dep_confirmado not in lista_opcoes:
            st.session_state.dep_confirmado = lista_opcoes[0] if lista_opcoes else None
        if "dep_pendente" not in st.session_state:
            st.session_state.dep_pendente = None

        idx_atual = lista_opcoes.index(st.session_state.dep_confirmado) if st.session_state.dep_confirmado in lista_opcoes else 0

        selecao_selectbox = st.selectbox(
            "Selecione um dependente para revisar:", 
            lista_opcoes, 
            index=idx_atual,
            key="selectbox_revisor_quarentena"
        )

        # Dispara o Pop-up ao alterar a seleção no Selectbox
        if selecao_selectbox != st.session_state.dep_confirmado:
            st.session_state.dep_pendente = selecao_selectbox

        if st.session_state.dep_pendente:
            modal_confirmar_troca(st.session_state.dep_pendente)

        selecao = st.session_state.dep_confirmado

        if selecao:
            dep_selecionado, nome_colaborador, cracha_colaborador, email_retirada = opcoes[selecao]
            
            col_info, col_img = st.columns([1.1, 1.4])
            
            chave_estado_msg = f"msg_{dep_selecionado.id_dependente}"
            chave_estado_analise = f"analise_{dep_selecionado.id_dependente}"
            chave_versao_widget = f"v_reprov_{dep_selecionado.id_dependente}"
            chave_modo_visualizacao = f"modo_vis_{dep_selecionado.id_dependente}"
            
            key_rg = f"mem_rg_{dep_selecionado.id_dependente}"
            key_decl = f"mem_decl_{dep_selecionado.id_dependente}"
            key_vinc = f"mem_vinc_{dep_selecionado.id_dependente}"
            
            if key_rg not in st.session_state: st.session_state[key_rg] = None
            if key_decl not in st.session_state: st.session_state[key_decl] = None
            if key_vinc not in st.session_state: st.session_state[key_vinc] = None
            
            motivo_exibicao = dep_selecionado.motivo_reprova_ia or "Não especificado pelo sistema"
            
            msg_reprovacao_padrao = (
                f"Olá, {nome_colaborador}.\n\n"
                f"Informamos que, após reanálise presencial/manual da equipe de RH, o cadastro do dependente {dep_selecionado.nome_filho} "
                f"foi REPROVADO devido a inconsistências identificadas na documentação enviada.\n\n"
                f"Seu cadastro anterior foi liberado. Por gentileza, acesse o sistema novamente e realize um novo cadastro anexando a documentação corrigida.\n\n"
                f"Atenciosamente,\nEquipe de Recursos Humanos"
            )
            
            if chave_estado_msg not in st.session_state or not st.session_state[chave_estado_msg]:
                st.session_state[chave_estado_msg] = msg_reprovacao_padrao
            if chave_estado_analise not in st.session_state:
                st.session_state[chave_estado_analise] = None
            if chave_versao_widget not in st.session_state:
                st.session_state[chave_versao_widget] = 0
            if chave_modo_visualizacao not in st.session_state:
                st.session_state[chave_modo_visualizacao] = "manual"

            with col_info:
                st.subheader("📋 Dados da Revisão")
                st.markdown(f"**Nome do Colaborador:** {nome_colaborador}")
                st.markdown(f"**Crachá:** {cracha_colaborador}")
                st.markdown(f"**Nome do Filho(a):** {dep_selecionado.nome_filho}")
                
                st.error(f"**🤖 Motivo da Reprovação (IA):**\n\n{motivo_exibicao}")
                
                if st.session_state[chave_estado_analise]:
                    res_ia = st.session_state[chave_estado_analise]
                    st.write("---")
                    st.subheader("🧠 Sugestão e Análise da IA (RH)")
                    
                    if res_ia.get("sugestao_acao") == "APROVAR":
                        st.success(f"**Recomendação:** APROVAR CADASTRO\n\n**Justificativa:** {res_ia.get('analise_tecnica_rh')}")
                    else:
                        st.warning(f"**Recomendação:** REPROVAR E SOLICITAR CORREÇÃO\n\n**Justificativa:** {res_ia.get('analise_tecnica_rh')}")

            st.write("---")
            st.subheader("⚙️ Ações de Revisão")
            
            email_colaborador = st.text_input("E-mail de Contato (Editável):", value=email_retirada or "", placeholder="email@empresa.com.br")
            
            msg_aprovacao_padrao = f"Olá, {nome_colaborador}.\n\nO RH analisou o documento enviado para o dependente {dep_selecionado.nome_filho} e sua documentação foi APROVADA com sucesso!\n\nSeu kit já está registrado no nosso sistema. Em breve divulgaremos as datas de entrega.\n\nAtenciosamente,\nEquipe de Recursos Humanos"
            
            tab_aprovar, tab_reprovar = st.tabs(["✅ Aprovar Cadastro", "❌ Reprovar Cadastro"])
            
            # 🟢 ABA 1: APROVAÇÃO
            with tab_aprovar:
                corpo_email_aprovacao = st.text_area(
                    "📝 E-mail de Aprovação (Editável):", 
                    value=msg_aprovacao_padrao, 
                    height=180,
                    key=f"aprov_{dep_selecionado.id_dependente}"
                )
                
                if st.button("✅ Confirmar Aprovação", use_container_width=True, type="primary"):
                    if not email_colaborador:
                        st.warning("⚠️ Informe o e-mail do colaborador para aprovar.")
                    else:
                        # Abre a Modal Pop-up para exigir o 2º clique consciente no meio da tela
                        modal_confirmar_aprovacao(
                            dep_selecionado.id_dependente,
                            cracha_colaborador,
                            nome_colaborador,
                            email_colaborador,
                            corpo_email_aprovacao,
                            dep_selecionado.nome_filho
                        )

            # 🔴 ABA 2: REPROVAÇÃO
            with tab_reprovar:
                key_dinamica_reprov = f"reprov_{dep_selecionado.id_dependente}_v{st.session_state[chave_versao_widget]}"
                
                corpo_email_reprovacao = st.text_area(
                    "📝 E-mail de Reprovação (Gere com a IA ao lado ou digite):", 
                    value=st.session_state[chave_estado_msg], 
                    height=180,
                    key=key_dinamica_reprov
                )
                
                if st.button("❌ Confirmar Reprovação e Limpar Cadastro", use_container_width=True):
                    if not email_colaborador:
                        st.warning("⚠️ Informe o e-mail do colaborador para enviar as instruções.")
                    elif not corpo_email_reprovacao.strip():
                        st.warning("⚠️ O corpo do e-mail não pode estar vazio ao reprovar.")
                    else:
                        # Abre a Modal Pop-up para exigir o 2º clique consciente no meio da tela
                        modal_confirmar_reprovacao(
                            dep_selecionado.id_dependente,
                            cracha_colaborador,
                            nome_colaborador,
                            email_colaborador,
                            corpo_email_reprovacao,
                            dep_selecionado.nome_filho
                        )

        with col_img:
            st.subheader("📄 Visualização e Envio de Documentos")
            
            bloqueio_ia_ativo = verificar_bloqueio_ia_3a_tentativa(cracha_colaborador, dep_selecionado.motivo_reprova_ia)
            
            if bloqueio_ia_ativo:
                st.session_state[chave_modo_visualizacao] = "manual"
                st.error("🚨 **AVISO DE SEGURANÇA:** O colaborador atingiu 3 tentativas de validação pelo mesmo motivo. O uso do Gemini foi **desabilitado automaticamente**. Realize a análise estritamente manual.")
            
            modo_atual = st.session_state[chave_modo_visualizacao]
            
            btn_col1, btn_col2 = st.columns(2)
            
            with btn_col1:
                tipo_btn_m = "primary" if modo_atual == "manual" else "secondary"
                if st.button("🔍 1 - Análise Manual", use_container_width=True, type=tipo_btn_m):
                    st.session_state[chave_modo_visualizacao] = "manual"
                    st.rerun()
            
            with btn_col2:
                tipo_btn_ia = "primary" if modo_atual == "ia" else "secondary"
                if not bloqueio_ia_ativo:
                    if st.button("🤖 2 - Módulo IA (Gemini)", use_container_width=True, type=tipo_btn_ia):
                        st.session_state[chave_modo_visualizacao] = "ia"
                        st.rerun()
                else:
                    st.button("🤖 IA Bloqueada (3ª Tentativa)", use_container_width=True, disabled=True)

            st.write("---")

            urls_banco = []
            if dep_selecionado.url_documento and str(dep_selecionado.url_documento).strip() != "None":
                urls_banco = [u.strip() for u in str(dep_selecionado.url_documento).split(",") if u.strip()]

            st.markdown("### 🛠️ Painel de Testes / Sobrescrita Manual")
            up_rg = st.file_uploader("1️⃣ Sobrescrever RG / Certidão", type=["pdf", "png", "jpg", "jpeg"], key=f"up_rg_{dep_selecionado.id_dependente}")
            up_decl = st.file_uploader("2️⃣ Sobrescrever Declaração Escolar", type=["pdf", "png", "jpg", "jpeg"], key=f"up_decl_{dep_selecionado.id_dependente}")
            up_vinc = st.file_uploader("3️⃣ Sobrescrever Vínculo", type=["pdf", "png", "jpg", "jpeg"], key=f"up_vinc_{dep_selecionado.id_dependente}")

            if up_rg:
                st.session_state[key_rg] = {"name": up_rg.name, "bytes": up_rg.getvalue()}
            if up_decl:
                st.session_state[key_decl] = {"name": up_decl.name, "bytes": up_decl.getvalue()}
            if up_vinc:
                st.session_state[key_vinc] = {"name": up_vinc.name, "bytes": up_vinc.getvalue()}

            fonte_rg_para_ia = st.session_state[key_rg] or (urls_banco[0] if len(urls_banco) >= 1 else None)
            fonte_decl_para_ia = st.session_state[key_decl] or (urls_banco[1] if len(urls_banco) >= 2 else None)
            fonte_vinc_para_ia = st.session_state[key_vinc] or (urls_banco[2] if len(urls_banco) >= 3 else None)

            # =============================================================================
            # MODO 1: VISUALIZAÇÃO MANUAL
            # =============================================================================
            if modo_atual == "manual":
                st.success("🟢 **Modo Ativo:** Visualizador Manual de Documentos")
                
                tab_doc1, tab_doc2, tab_doc3 = st.tabs(["📄 RG / Certidão", "🏫 Declaração Escolar", "📜 Documento de Vínculo"])
                
                with tab_doc1:
                    if fonte_rg_para_ia:
                        renderizar_midia(fonte_rg_para_ia, "RG / Certidão")
                    else:
                        st.caption("Nenhum arquivo encontrado para RG/Certidão.")

                with tab_doc2:
                    if fonte_decl_para_ia:
                        renderizar_midia(fonte_decl_para_ia, "Declaração Escolar")
                    else:
                        st.caption("Nenhum arquivo encontrado para Declaração Escolar.")

                with tab_doc3:
                    if fonte_vinc_para_ia:
                        renderizar_midia(fonte_vinc_para_ia, "Documento de Vínculo")
                    else:
                        st.caption("Nenhum arquivo encontrado para Documento de Vínculo.")

            # =============================================================================
            # MODO 2: MÓDULO IA COM LÓGICA DE MENSAGEM CORRIGIDA
            # =============================================================================
            elif modo_atual == "ia":
                st.info("🤖 **Modo Ativo:** Módulo de Auditoria Automatizada IA")
                
                has_any_doc = any([fonte_rg_para_ia, fonte_decl_para_ia, fonte_vinc_para_ia])
                
                if not has_any_doc:
                    st.warning("⚠️ **Nenhum documento localizado** (nem no Banco de Dados via URL e nem por Upload Manual). Adicione ao menos um arquivo nos campos acima para habilitar o botão.")
                else:
                    st.markdown("Clique no botão abaixo para processar os documentos no Gemini:")
                    
                    btn_executar_ia = st.button("🪄 Executar Analisador Gemini Agora", type="primary", use_container_width=True)
                    
                    if btn_executar_ia:
                        with st.spinner("🧠 Analisando documentos no Gemini, verificando autenticidade e gerando parecer..."):
                            resultado = analisar_documentos_com_ia(
                                fonte_rg_para_ia,
                                fonte_decl_para_ia,
                                fonte_vinc_para_ia,
                                dep_selecionado.motivo_reprova_ia,
                                nome_colaborador,
                                dep_selecionado.nome_filho
                            )
                            st.session_state[chave_estado_analise] = resultado
                            
                            if resultado.get("sugestao_acao") == "REPROVAR":
                                email_reprovacao = resultado.get("corpo_email", "") or (
                                    f"Olá, {nome_colaborador}.\n\n"
                                    f"Informamos que o cadastro do dependente {dep_selecionado.nome_filho} "
                                    f"para o Kit Escolar foi REPROVADO.\n\n"
                                    f"Motivo/Orientação: {resultado.get('analise_tecnica_rh', 'Documentação inconsistente.')}\n\n"
                                    f"Seu cadastro foi liberado. Por gentileza, acesse o sistema novamente e realize um novo cadastro com os documentos corrigidos.\n\n"
                                    f"Atenciosamente,\nEquipe de Recursos Humanos"
                                )
                            else:
                                email_reprovacao = (
                                    f"Olá, {nome_colaborador}.\n\n"
                                    f"Informamos que, após reanálise presencial/manual realizada pela equipe de RH, o cadastro do dependente {dep_selecionado.nome_filho} "
                                    f"para o Kit Escolar foi REPROVADO devido a inconsistências identificadas na documentação apresentada.\n\n"
                                    f"Seu cadastro anterior foi liberado. Por gentileza, acesse o sistema novamente e realize um novo cadastro anexando a documentação corrigida.\n\n"
                                    f"Atenciosamente,\nEquipe de Recursos Humanos"
                                )
                            
                            st.session_state[chave_estado_msg] = email_reprovacao
                            st.session_state[chave_versao_widget] += 1
                            st.success("✅ Análise da IA concluída! Parecer gerado e campo de e-mail atualizado.")
                            st.rerun()

# ================= ABA 2: ALERTAS E SEGURANÇA (GERENCIAL) =================
with tab_alertas:
    st.subheader("🛡️ Painel Executivo de Alertas e Segurança")
    st.caption("Acompanhe em tempo real as tentativas de bypass do sistema e os colaboradores reincidentes.")

    col_btn, _ = st.columns([1, 4])
    with col_btn:
        if st.button("🔄 Atualizar Métricas", use_container_width=True):
            st.rerun()

    total_violacoes, total_colab_reincidentes, lista_reincidentes, lista_violacoes = buscar_estatisticas_seguranca()

    # --- 1. CARDS DE MÉTRICAS RÁPIDAS ---
    col_m1, col_m2 = st.columns(2)
    with col_m1:
        st.metric(
            label="⚠️ Tentativas de Bypass (Violações)",
            value=total_violacoes,
            delta="Forçados pelo usuário",
            delta_color="inverse"
        )
    with col_m2:
        st.metric(
            label="👥 Colaboradores em Reincidência de Erro",
            value=total_colab_reincidentes,
            delta="Pessoas com 2 ou mais tentativas",
            delta_color="off"
        )

    st.divider()

    st.markdown("### 🚨 Colaboradores com Reincidência de Erro")
    st.caption("Listagem de usuários na 2ª tentativa (atenção) ou com IA bloqueada (3ª tentativa pelo mesmo motivo).")

    if not lista_reincidentes:
        st.success("✅ Nenhum colaborador atingiu 2 ou mais reprovações no momento.")
    else:
        dados_reincidentes_tabela = []
        for r in lista_reincidentes:
            status_ia = "🛑 BLOQUEADA (3ª+ Tentativa)" if r["qtd"] >= 3 else "⚠️ ATENÇÃO (2ª Tentativa)"
            dt_str = r["ultima_data"].strftime("%d/%m/%Y %H:%M") if r["ultima_data"] else "N/A"
            
            dados_reincidentes_tabela.append({
                "Crachá": str(r["cracha"]),
                "Qtd. Tentativas": r["qtd"],
                "Status IA": status_ia,
                "Motivo da Falha Recorrente": r["motivo"],
                "Última Ocorrência": dt_str
            })
            
        st.dataframe(
            dados_reincidentes_tabela,
            use_container_width=True,
            hide_index=True
        )

    st.divider()

    st.markdown("### 🚨 Registro de Tentativas de Bypass do Sistema")
    st.caption("Histórico dos usuários que forçaram o envio ao RH após recusa inicial da automação.")

    if not lista_violacoes:
        st.info("Nenhuma tentativa de bypass registrada.")
    else:
        dados_violacoes_tabela = []
        for v in lista_violacoes:
            dt_violacao = v.data_hora.strftime("%d/%m/%Y %H:%M:%S") if v.data_hora else "N/A"
            dados_violacoes_tabela.append({
                "Data / Hora": dt_violacao,
                "Crachá": str(v.cracha),
                "Ação Registrada": v.acao,
                "Detalhes do Bypass": v.detalhes
            })

        st.dataframe(
            dados_violacoes_tabela,
            use_container_width=True,
            hide_index=True
        )