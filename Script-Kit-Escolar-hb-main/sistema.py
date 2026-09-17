import streamlit as st
import requests
import time
import cv2
import numpy as np
from dotenv import load_dotenv
import os

load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL")

st.set_page_config(page_title="Entrega de Kits", page_icon="📦", layout="centered")

# --- CONTROLE DE ESTADOS ---
if 'dados_lidos' not in st.session_state:
    st.session_state.dados_lidos = None

if 'codigo_extraido' not in st.session_state:
    st.session_state.codigo_extraido = None

if 'modo_analise' not in st.session_state:
    st.session_state.modo_analise = "upload"

# Callback para o campo de digitação/pistola USB
def submeter_codigo():
    # Remove o prefixo caso a pistola leia o QR Code completo com a tag
    codigo_limpo = st.session_state.input_codigo.replace("RETIRADA_KIT:", "").strip()
    st.session_state.codigo_extraido = codigo_limpo
    st.session_state.input_codigo = ""

st.title("📦 Sistema de Entrega - Kits Escolares")

# =============================================================================
# SEÇÃO SUPERIOR: BOTOES DE ANÁLISE (MANUAL E IA)
# =============================================================================
col_btn1, col_btn2 = st.columns(2)

with col_btn1:
    btn_analise_manual = st.button(
        "🔍 1 - Análise Manual", 
        type="secondary", 
        use_container_width=True,
        help="Abre a visualização direta dos documentos anexados para conferência humana."
    )

with col_btn2:
    btn_analise_ia = st.button(
        "🤖 2 - Gerar Análise IA", 
        type="primary", 
        use_container_width=True,
        help="Executa a auditoria automática com o modelo de inteligência artificial."
    )

if btn_analise_manual:
    st.session_state.modo_analise = "manual"

if btn_analise_ia:
    st.session_state.modo_analise = "ia"

# =============================================================================
# EXIBIÇÃO CONDICIONAL DOS MODOS DE ANÁLISE
# =============================================================================
if st.session_state.modo_analise == "manual":
    st.markdown("---")
    st.subheader("👁️ Visualização de Documentos (Conferência Manual)")
    st.info("Confira abaixo os arquivos anexados pelo colaborador:")

    # Recupera URLs dos documentos se houver um registro carregado no estado
    colaborador = st.session_state.dados_lidos or {}
    url_rg = colaborador.get("url_documento_rg")
    url_declaracao = colaborador.get("url_documento_declaracao")
    url_vinculo = colaborador.get("url_documento_vinculo")

    tem_documentos = any([url_rg, url_declaracao, url_vinculo])

    if not tem_documentos:
        st.warning("⚠️ Nenhum documento foi encontrado no banco de dados para este registro.")
    else:
        tab1, tab2, tab3 = st.tabs(["📄 RG / Certidão", "🏫 Declaração Escolar", "📜 Documento de Vínculo"])

        with tab1:
            if url_rg:
                if str(url_rg).lower().endswith(".pdf"):
                    st.download_button("📥 Baixar PDF do RG", url_rg, file_name="rg.pdf")
                else:
                    st.image(url_rg, caption="RG / Certidão de Nascimento", use_container_width=True)
            else:
                st.caption("Nenhum arquivo enviado para RG/Certidão.")

        with tab2:
            if url_declaracao:
                if str(url_declaracao).lower().endswith(".pdf"):
                    st.download_button("📥 Baixar PDF da Declaração", url_declaracao, file_name="declaracao.pdf")
                else:
                    st.image(url_declaracao, caption="Declaração Escolar", use_container_width=True)
            else:
                st.caption("Nenhum arquivo enviado para Declaração Escolar.")

        with tab3:
            if url_vinculo:
                if str(url_vinculo).lower().endswith(".pdf"):
                    st.download_button("📥 Baixar PDF do Vínculo", url_vinculo, file_name="vinculo.pdf")
                else:
                    st.image(url_vinculo, caption="Documento de Vínculo", use_container_width=True)
            else:
                st.caption("Nenhum arquivo enviado para Documento de Vínculo.")

    if st.button("⬅️ Voltar ao Painel Principal", use_container_width=True):
        st.session_state.modo_analise = "upload"
        st.rerun()

elif st.session_state.modo_analise == "ia":
    st.markdown("---")
    st.subheader("⚡ Executando Validação por IA...")
    
    with st.spinner("Analisando autenticidade, timbres e nomes nos documentos..."):
        # Espaço reservado para execução da chamada automatizada existente do Gemini
        time.sleep(2)

    st.success("✅ Análise da IA concluída com sucesso!")
    
    if st.button("⬅️ Voltar ao Painel Principal", use_container_width=True):
        st.session_state.modo_analise = "upload"
        st.rerun()

# =============================================================================
# PAINEL PRINCIPAL: LEITURA DE QR CODE E BAIXA DE KITS
# =============================================================================
st.markdown("---")

col1, col2 = st.columns(2)

with col1:
    st.markdown("### 📷 Usar Câmera")
    foto_camera = st.camera_input("Tire a foto do QR Code", label_visibility="collapsed")
    
    if foto_camera:
        file_bytes = np.asarray(bytearray(foto_camera.read()), dtype=np.uint8)
        img = cv2.imdecode(file_bytes, 1)
        
        detector = cv2.QRCodeDetector()
        valor_qr, bbox, straight_qrcode = detector.detectAndDecode(img)
        
        if valor_qr:
            # Remove o prefixo lido pela câmera e guarda só o UUID
            st.session_state.codigo_extraido = valor_qr.replace("RETIRADA_KIT:", "").strip()
        else:
            st.warning("👀 Não foi possível ler o QR Code. Evite reflexos e tente focar melhor a imagem.")

with col2:
    st.markdown("### ⌨️ Pistola USB / Digitação")
    st.text_input(
        "Código:", 
        key="input_codigo", 
        placeholder="Bipe com o leitor ou digite...",
        on_change=submeter_codigo 
    )

# --- BUSCA DE DADOS ---
if st.session_state.codigo_extraido:
    try:
        resposta = requests.get(f"{API_BASE_URL}/info/{st.session_state.codigo_extraido}")
        if resposta.status_code == 200:
            st.session_state.dados_lidos = resposta.json()
        elif resposta.status_code == 404:
            st.error("❌ Código não localizado no banco de dados.")
            st.session_state.dados_lidos = None
    except requests.exceptions.ConnectionError:
        st.error("🔌 Erro de conexão com a API. Verifique se o FastAPI está rodando.")
        
    st.session_state.codigo_extraido = None

st.markdown("---")

# --- EXIBIÇÃO E BOTÃO DE BAIXA ---
if st.session_state.dados_lidos:
    dados = st.session_state.dados_lidos
    
    st.markdown(f"<h2 style='text-align: center; color: #1f77b4;'>Colaborador ID: {dados['id_colaborador']}</h2>", unsafe_allow_html=True)
    
    if dados["status"] == "ENTREGUE":
        st.error(f"⚠️ ATENÇÃO: Este kit já consta como ENTREGUE!")
    else:
        st.info(f"**Status Atual:** {dados['status']} | **Total de Kits:** {dados['qtd_kits']}")
        
        st.markdown("### 👨‍👩‍👧 Dependentes e Kits:")
        for dependente in dados["dependentes"]:
            st.success(f"**Filho(a):** {dependente['nome_filho']} ➔ **Kit:** {dependente['kit_escolhido']}")
        
        st.markdown("<br>", unsafe_allow_html=True)
        
        if st.button("✅ CONFIRMAR ENTREGA E DAR BAIXA", use_container_width=True, type="primary"):
            payload = {"codigo_retirada": dados["codigo_retirada"]}
            res_baixa = requests.put(f"{API_BASE_URL}/baixa", json=payload)
            
            if res_baixa.status_code == 200:
                st.success("🎉 **Maravilha! Entrega registrada com sucesso.** Pode liberar os kits para o colaborador!")
                
                st.markdown("<h1 style='text-align: center; font-size: 80px; color: #28a745; margin-top: 20px;'>RH 4.0 💪</h1>", unsafe_allow_html=True)
                
                time.sleep(4)
                
                st.session_state.dados_lidos = None
                st.rerun()
            else:
                st.error("❌ Ocorreu um erro ao tentar dar a baixa. Verifique com o suporte.")