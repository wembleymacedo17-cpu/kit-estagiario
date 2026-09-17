import pandas as pd
import os
import streamlit as st  
from datetime import date, datetime, timedelta
import time
import qrcode 
import re
import unicodedata
import uuid
from io import BytesIO

# ==================== IMPORTS DO BANCO ====================
from database import SessionLocal, Colaborador, Dependente, EscolhaKit, Retirada, registrar_log
from conector_Postgre import SupabaseConnector
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from notificador_email import NotificadorEmail, SMTP_SERVER, SMTP_PORT, LOGIN_SMTP, SENHA_KEY, EMAIL_REMETENTE
from query import DOMINIOS_PESSOAIS_PERMITIDOS, TAMANHO_MAXIMO_MB, EXTENSOES_PERMITIDAS
from rate_limiter import verificar_limite_clique
from dotenv import load_dotenv

# Carrega explicitamente o .env que fica ao lado deste arquivo (mesmo padrão do database.py),
# evitando que um .env de outra pasta/projeto seja carregado por engano.
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
load_dotenv(env_path, override=True, encoding='utf-8')

# ==================== FUNÇÕES DE BUSCA E AUTENTICAÇÃO ====================

def busca_colaborador(situacoes_invalidas=["Desligado", "Aposentadoria p/Invalidez"]):
    with st.form("form_busca"):
        st.subheader("🔐 Acesso ao Sistema")
        cracha_digitado = st.text_input("Crachá (somente números):")
        cpf_digitado = st.text_input("CPF (somente números):", max_chars=11)
        data_nasc_digitada = st.text_input("Data de Nascimento (DD/MM/AAAA):", max_chars=10)
        buscar = st.form_submit_button("🔍 Entrar")
        
    if not buscar:
        return None
        
    if not cracha_digitado.strip() or not cracha_digitado.strip().isdigit():
        st.warning("⚠️ Por favor, digite um número de crachá válido.")
        return None
        
    cpf_limpo = re.sub(r'\D', '', cpf_digitado)
    if len(cpf_limpo) != 11:
        st.warning("⚠️ Por favor, digite um CPF válido com 11 dígitos.")
        return None
        
    try:
        data_nasc_obj = datetime.strptime(data_nasc_digitada.strip(), "%d/%m/%Y").date()
    except ValueError:
        st.warning("⚠️ Formato de data inválido. Use DD/MM/AAAA.")
        return None

    cracha_numero = int(cracha_digitado.strip())
    supabase_connector = SupabaseConnector()
    
    try:
        # Tenta converter a coluna textual para DATE antes de formatar
        # Caso a data já esteja salva como 'YYYY-MM-DD' em texto, o TO_DATE/::date trata perfeitamente
        query_banco = f"""
        SELECT 
            cracha, nome, cpf, 
            data_nascimento AS data_nascimento_raw,
            descricao_situacao,
            titulo_reduzido_cargo, id_cargo
        FROM colaboradores
        WHERE cracha = {cracha_numero}
        """
        df = pd.read_sql(query_banco, supabase_connector.engine)
        colaborador = df.to_dict(orient="records")[0] if not df.empty else None
        
        if not colaborador:
            st.error("⚠️ Crachá não encontrado na base de dados.")
            return None
            
        if colaborador["descricao_situacao"] in situacoes_invalidas:
            st.error(f"⚠️ Colaborador não elegível. Situação atual: {colaborador['descricao_situacao']}")
            return None

        # 1. Filtro exclusivo para Estagiários
        cargos_estagiario = [600, 601, 602, 5001]
        id_cargo_colab = colaborador.get("id_cargo")
        if id_cargo_colab is None or int(id_cargo_colab) not in cargos_estagiario:
            st.error("Essa página é dedicada somente para Cadastro de Estagiário -> acesse o site para fazer o cadastro -> https://kitescolar.hospitaldebase.com.br/kit/")
            st.stop()
            
        # 2. Validação do CPF
        cpf_banco = str(colaborador.get("cpf", "")).strip().split(".")[0]
        cpf_banco = re.sub(r'\D', '', cpf_banco).zfill(11)
        if cpf_banco != cpf_limpo:
            st.session_state.tentativas_cpf_erro = st.session_state.get("tentativas_cpf_erro", 0) + 1
            st.error("⚠️ CPF incorreto.")
            if st.session_state.tentativas_cpf_erro >= 2:
                st.warning("🔎 Está com dificuldades para lembrar seu CPF? Tente inserir: 0000000000")
            return None

        # 3. Tratamento Flexível do Texto da Data
        val_raw = str(colaborador.get("data_nascimento_raw", "")).strip().split("T")[0].split(" ")[0]
        data_banco_obj = None

        # Tenta converter os formatos de texto mais comuns vindos do banco
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                data_banco_obj = datetime.strptime(val_raw, fmt).date()
                break
            except ValueError:
                continue

        if not data_banco_obj or data_banco_obj != data_nasc_obj:
            st.session_state.tentativas_data_erro = st.session_state.get("tentativas_data_erro", 0) + 1
            st.error("⚠️ Data de nascimento incorreta.")
            if st.session_state.tentativas_data_erro >= 2:
                st.warning("🔎 Está com dificuldades para lembrar sua data de nascimento? Tente inserir: 31/12/1900")
            return None

        st.session_state.colaborador = {
            "id": colaborador["cracha"],
            "cracha": colaborador["cracha"],            
            "Crachá": colaborador["cracha"],
            "Nome": colaborador["nome"],
            "nome": colaborador["nome"],
            "cpf": cpf_banco,
            "data_nascimento": data_banco_obj,
            "Título Reduzido (Cargo)": colaborador["titulo_reduzido_cargo"],
            "Descrição (Situação)": colaborador["descricao_situacao"],
            "id_cargo": int(colaborador["id_cargo"])
        }
        st.rerun()
    finally:
        supabase_connector.fechar_conexao()


def eh_email_pessoal(email: str) -> bool:
    partes = email.strip().lower().split("@")
    if len(partes) != 2:
        return False
    return partes[1] in DOMINIOS_PESSOAIS_PERMITIDOS

def valida_telefone(telefone):
    numero = re.sub(r'\D', '', telefone)
    if len(numero) != 11:
        return False, "Telefone deve ter 11 dígitos (com DDD)."
    return True, "Telefone válido."

def formata_telefone(telefone):
    numero = re.sub(r'\D', '', telefone)
    if len(numero) == 11:
        return f"({numero[:2]}) {numero[2:7]}-{numero[7:]}"
    return numero

def adiciona_dados_contato(email_padrao: str = "", telefone_padrao: str = "", form_key: str = "form_contato"):
    with st.form(key=form_key):
        st.subheader("📞 Dados de Contato")
        email = st.text_input("E-mail Pessoal", value=email_padrao, placeholder="seuemail@gmail.com")
        confirmacao_email = st.text_input("Confirme o E-mail", value=email_padrao, placeholder="seuemail@gmail.com")
        telefone = st.text_input("Número de Telefone (WhatsApp)", value=telefone_padrao)
        salvar = st.form_submit_button("💾 Salvar Dados de Contato")
        
    if not salvar:
        return None
        
    erros = []
    email_digitado = email.strip()
    email_confirmado = confirmacao_email.strip()
    
    if not email_digitado or not email_confirmado:
        erros.append("⚠️ O preenchimento e a confirmação do e-mail são obrigatórios.")
    elif email_digitado.lower() != email_confirmado.lower():
        erros.append("⚠️ Os e-mails não conferem.")
    elif not eh_email_pessoal(email_digitado):
        erros.append("⚠️ Utilize um e-mail pessoal (Gmail, Hotmail, Outlook, Yahoo, iCloud, etc). E-mails corporativos não são aceitos.")

    if not telefone.strip():
        erros.append("⚠️ Telefone é obrigatório.")
    else:
        telefone_valido, mensagem_telefone = valida_telefone(telefone)
        if not telefone_valido:
            erros.append(f"⚠️ {mensagem_telefone}")
            
    if erros:
        for x in erros: st.error(f"{x}")
        return None
        
    st.success("✅ Dados de contato salvos com sucesso!")
    return {"email": email_digitado.lower(), "telefone": formata_telefone(telefone)}

def padroniza_texto(texto):
    texto = texto.strip().upper()
    texto = unicodedata.normalize('NFKD', texto)
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r'[^A-Z\s]', '', texto)
    texto = re.sub(r'\s+', ' ', texto)
    return texto

def validar_arquivo(arquivo, nome_campo: str) -> bool:
    if arquivo is None:
        return True

    tamanho_mb = arquivo.size / (1024 * 1024)
    if tamanho_mb > TAMANHO_MAXIMO_MB:
        st.error(f"⚠️ O arquivo anexado em **'{nome_campo}'** excede o limite permitido de {TAMANHO_MAXIMO_MB} MB.")
        return False

    extensao = arquivo.name.split(".")[-1].lower()
    if extensao not in EXTENSOES_PERMITIDAS:
        st.error(f"⚠️ Formato inválido em **'{nome_campo}'**. Formatos aceitos: {', '.join(EXTENSOES_PERMITIDAS).upper()}.")
        return False

    return True

# ==================== INTEGRAÇÕES DE BUCKETS (AWS / SUPABASE) ====================

def _get_s3_client():
    return boto3.client("s3", region_name=os.getenv("AWS_REGION"))

def upload_documento(arquivo_buffer, tipo_documento, cracha) -> str | None:
    # Mantido o upload padrão que você já utilizava
    BASE_URL_DOCUMENTO_ANALISE = os.getenv("BASE_URL_DOCUMENTO_ANALISE", "").rstrip("/") + "/"
    BUCKET_DOCS = os.getenv("S3_BUCKET_DOCS")

    try:
        s3_client = _get_s3_client()
        hash_curto = uuid.uuid4().hex[:6]
        extensao = arquivo_buffer.name.split('.')[-1]
        nome_arquivo = f"{tipo_documento}-{cracha}-{hash_curto}.{extensao}"
        caminho_s3 = f"docs/{nome_arquivo}"
        arquivo_buffer.seek(0)

        s3_client.put_object(
            Bucket=BUCKET_DOCS,
            Key=caminho_s3, 
            Body=arquivo_buffer.read(),
            ContentType=arquivo_buffer.type,
        )
        return f"{BASE_URL_DOCUMENTO_ANALISE}{caminho_s3}"
    except Exception as e:
        print(f"❌ Erro ao tentar salvar documento: {e}")
        return None
    finally:
        arquivo_buffer.seek(0)

def gerar_url_assinada_qrcode(cracha: str) -> str | None:
    try:
        s3_client = _get_s3_client()
        bucket_name = os.getenv("S3_BUCKET_QRCODES", "s3-bucket-qrcodes")
        key = f"qrcodes/{cracha}.png"
        return s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': key},
            ExpiresIn=3600
        )
    except Exception as e:
        print(f"Erro ao gerar URL do QR Code: {e}")
        return None

def salvar_qrcode_bucket(buffer_qrcode: BytesIO, cracha: str) -> str | None:
    BASE_URL_QRCODE = os.getenv("BASE_URL_QRCODE", "")
    BUCKET_QRCODES = os.getenv("S3_BUCKET_QRCODES")

    try:
        s3_client = _get_s3_client()
        nome_arquivo = f"{cracha}.png"
        caminho_s3 = f"qrcodes/{nome_arquivo}"
        buffer_qrcode.seek(0)

        s3_client.put_object(
            Bucket=BUCKET_QRCODES,
            Key=caminho_s3,
            Body=buffer_qrcode.read(),
            ContentType="image/png",
        )
        return f"{BASE_URL_QRCODE}{caminho_s3}"
    except Exception as e:
        print(f"❌ Erro no S3: {e}")
        return None
    finally:
        buffer_qrcode.seek(0)

# ==================== KITS VIA SUPABASE ====================

# 1. Função de catálogo
def catalogo_kits_por_escolaridade():
    BASE_URL = os.getenv("SUPABASE_URL_KITS")
    if not BASE_URL:
        st.error("⚠️ A variável `SUPABASE_URL_KITS` não está configurada no .env!")
        return {}, ""
    
    kits_em = [f"EM-{i}" for i in range(1, 14)]
    kits_superior = [f"SUP-{i}" for i in range(1, 10)]
    
    return {
        "Ensino Médio": kits_em,
        "Ensino Superior": kits_superior
    }, BASE_URL



# ==================== TELAS DE ESCOLHA E RETIRADA ====================

def escolher_kits_colaborador():
    st.divider()
    st.subheader("🎒 Escolha o Seu Kit Escolar")

    cracha_colaborador = st.session_state.colaborador["cracha"]
    db = SessionLocal()

    try:
        colaborador_db = db.query(Colaborador).filter(Colaborador.cracha == cracha_colaborador).first()
        if not colaborador_db:
            st.error("⚠️ Colaborador não encontrado na base de dados.")
            return None

        # CORREÇÃO: Usamos .order_by(...desc()).limit(1) para pegar APENAS o cadastro mais recente
        dependentes_colaborador = db.query(Dependente).filter(
            (Dependente.id_colaborador == colaborador_db.id) | 
            (Dependente.id_colaborador == cracha_colaborador)
        ).order_by(Dependente.id_dependente.desc()).limit(1).all()

        if not dependentes_colaborador:
            st.warning("Nenhum cadastro acadêmico encontrado.")
            return None

        catalogo, base_url = catalogo_kits_por_escolaridade()

        st.markdown("""
            <style>
            div[data-testid="stColumn"] img {
                height: 180px !important;
                object-fit: contain !important;
            }
            </style>
        """, unsafe_allow_html=True)

        info_dependentes = []

        with st.form("form_escolha_kits"):
            for dependente in dependentes_colaborador:
                escolaridade = dependente.escolaridade
                ano_escolar = dependente.ano_escola
                id_dependente = dependente.id_dependente

                st.write(f"**Escolaridade:** {escolaridade} | **Ano/Semestre:** {ano_escolar}")
                opcoes_kits = catalogo.get(escolaridade, [])

                if not opcoes_kits:
                    st.error(f"Não existem kits cadastrados para a escolaridade: {escolaridade}")
                    continue

                st.markdown("**Catálogo Disponível — marque o kit desejado:**")
                itens_por_linha = 4

                for i in range(0, len(opcoes_kits), itens_por_linha):
                    colunas = st.columns(itens_por_linha)
                    for j in range(itens_por_linha):
                        if i + j < len(opcoes_kits):
                            nome_kit = opcoes_kits[i + j]
                            nome_arquivo = nome_kit.replace(" ", "")
                            base_url_limpa = base_url.rstrip("/")
                            
                            url_img = f"{base_url_limpa}/{nome_arquivo}.png"

                            with colunas[j]:
                                st.image(url_img, caption=nome_kit, width='stretch')
                                st.checkbox(nome_kit, key=f"chk_kit_{id_dependente}_{nome_kit}")

                info_dependentes.append({
                    "ID_Dependente": id_dependente,
                    "ID_Colaborador": colaborador_db.id,
                    "Nome_filho": dependente.nome_filho,
                    "Escolaridade": escolaridade,
                    "Ano_escolar": ano_escolar,
                    "Opcoes_kits": opcoes_kits
                })

            st.write("---")
            ciente = st.checkbox("Estou ciente de que as mochilas possuem variações de cores e acabamentos e a distribuição estará condicionada ao estoque disponível.", key="chk_ciencia_variacao_kit")
            salvar_escolhas = st.form_submit_button("✅ Confirmar escolha do kit")

        if salvar_escolhas:
            if not ciente:
                st.error("⚠️ Você precisa marcar a opção 'Estou ciente...' para prosseguir.")
                return None

            escolhas = []
            erros = []

            for info in info_dependentes:
                id_dependente = info["ID_Dependente"]
                marcados = [nk for nk in info["Opcoes_kits"] if st.session_state.get(f"chk_kit_{id_dependente}_{nk}")]

                if len(marcados) == 0:
                    erros.append("⚠️ Selecione um kit.")
                    continue
                if len(marcados) > 1:
                    erros.append("⚠️ Selecione apenas UM kit.")
                    continue

                escolhas.append({
                    "ID_Dependente": id_dependente,
                    "ID_Colaborador": info["ID_Colaborador"],
                    "Nome_filho": info["Nome_filho"],
                    "Escolaridade": info["Escolaridade"],
                    "Ano_escolar": info["Ano_escolar"],
                    "Kit_Escolhido": marcados[0]
                })

            if erros:
                for erro in erros: st.error(erro)
                return None

            novas_escolhas = []
            data_aceite = datetime.now()

            for escolha in escolhas:
                nova_escolha = EscolhaKit(
                    id_colaborador=escolha["ID_Colaborador"],
                    id_dependente=escolha["ID_Dependente"],
                    kit_escolhido=escolha["Kit_Escolhido"],
                    aceite_variacao_kit=True,
                    data_aceite_variacao=data_aceite
                )
                db.add(nova_escolha)
                db.commit()
                db.refresh(nova_escolha)
                novas_escolhas.append(escolha)

            resumo_str_direto = " | ".join([f"{e['Nome_filho']} - {e['Escolaridade']} - {e['Kit_Escolhido']}" for e in novas_escolhas])
            retirada_db = db.query(Retirada).filter(
                (Retirada.id_colaborador == colaborador_db.id) | 
                (Retirada.id_colaborador == cracha_colaborador)
            ).first()

            if retirada_db:
                retirada_db.resumo_kits = resumo_str_direto
                retirada_db.qtd_kits = len(novas_escolhas)
                retirada_db.status = 'PENDENTE'
                db.commit()

            st.session_state.escolhas_kits = novas_escolhas
            registrar_log(colaborador_db.id, "KIT_SELECIONADO", "Escolha confirmada de kit próprio.")
            st.success("🎉 Kit escolhido com sucesso!")
            return novas_escolhas

    finally:
        db.close()


def exibir_qrcode_final():
    st.divider()
    st.subheader("🎟️ QR Code para Retirada")

    colaborador = st.session_state.colaborador
    contato = st.session_state.contato
    db = SessionLocal()
    
    try:
        retirada_existente = db.query(Retirada).filter(Retirada.id_colaborador == colaborador["id"]).first()
        if not retirada_existente:
            codigo_retirada = str(uuid.uuid4())
            resumo_kits = " | ".join([f"{e['Nome_filho']} - {e['Kit_Escolhido']}" for e in st.session_state.escolhas_kits])
            nova_retirada = Retirada(
                codigo_retirada=codigo_retirada,
                id_colaborador=colaborador["id"],
                email=contato["email"],
                telefone=contato["telefone"],
                qtd_kits=1,
                resumo_kits=resumo_kits,
                status="PENDENTE"
            )
            db.add(nova_retirada)
            db.commit()
            db.refresh(nova_retirada)
            retirada_existente = nova_retirada
            
        conteudo_qr = f"RETIRADA_KIT:{retirada_existente.codigo_retirada}"
        qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
        qr.add_data(conteudo_qr)
        qr.make(fit=True)
        imagem_qrcode = qr.make_image(fill_color="black", back_color="white")

        buffer = BytesIO()
        imagem_qrcode.save(buffer, format="PNG")
        buffer.seek(0)

        if not st.session_state.get("email_qrcode_enviado", False):
            notificador = NotificadorEmail(SMTP_SERVER, SMTP_PORT, LOGIN_SMTP, SENHA_KEY)
            email_enviado = notificador.disparar(
                remetente=EMAIL_REMETENTE,
                destinatarios=contato["email"],
                assunto="🎒 Seu QR Code - Kit Escolar Funfarme",
                corpo=(
                    f"Olá, {colaborador['Nome']}!\n\n"
                    "Segue em anexo o QR Code para retirada do seu Kit Escolar.\n"
                    "Apresente este código no momento da retirada."
                ),
                anexo=buffer,
                nome_anexo="qrcode_kit_escolar.png"
            )
            if email_enviado:
                st.success("📧 QR Code enviado para o seu e-mail.")
            else:
                st.warning("⚠️ Não foi possível enviar o e-mail com o QR Code. Utilize o código exibido abaixo.")
            st.session_state.email_qrcode_enviado = True

        st.write(f"Estagiário: {colaborador['Nome']}")
        st.write(f"Crachá: {colaborador['cracha']}")
        st.write(f"Status: {retirada_existente.status}")
        st.info(retirada_existente.resumo_kits)

        st.image(buffer, caption="Apresente este QR Code para retirada do kit.", width=300)

        if not st.session_state.get("qrcode_processado", False):
            cracha = str(colaborador["id"])
            salvar_qrcode_bucket(buffer, cracha)
            st.session_state.qrcode_processado = True

    finally:
        db.close()


# ==================== INTERFACE PRINCIPAL ====================

def interface():
    st.set_page_config(page_title='Funfarme - Kit Escolar (Estagiários)', page_icon='🎒', layout="wide")
    st.title('🎒 Funfarme - Kit Escolar (Estagiários)')

    if 'colaborador' not in st.session_state: st.session_state.colaborador = None
    if 'contato' not in st.session_state: st.session_state.contato = None
    if 'cadastro_finalizado' not in st.session_state: st.session_state.cadastro_finalizado = False
    if 'escolhendo_kits' not in st.session_state: st.session_state.escolhendo_kits = False
    if 'escolhas_kits' not in st.session_state: st.session_state.escolhas_kits = []
    
    # 1. ETAPA DE LOGIN
    if st.session_state.colaborador is None:
        busca_colaborador()
        return

    # 2. SE JÁ ESTIVER CADASTRADO (VERIFICA NO BANCO SE TEM DEPENDENTE/KIT)
    if not st.session_state.escolhendo_kits and not st.session_state.cadastro_finalizado:
        db = SessionLocal()
        try:
            dependentes_existentes = db.query(Dependente).filter(
                Dependente.id_colaborador == st.session_state.colaborador['id']
            ).all()
            
            if dependentes_existentes:
                st.success("✅ **STATUS: CADASTRO CONCLUÍDO**")
                st.info("Você já realizou o cadastro do seu Kit Escolar.")
                exibir_qrcode_final()
                return
        finally:
            db.close()

    # 3. ETAPA DE CONTATO
    if st.session_state.contato is None:
        st.divider()
        st.subheader("📋 Ficha do Estagiário")
        st.text_input("Nome Completo", value=st.session_state.colaborador['Nome'], disabled=True)
        st.text_input("Cargo", value=st.session_state.colaborador['Título Reduzido (Cargo)'] or "", disabled=True)

        contato = adiciona_dados_contato(form_key=f"form_contato_{st.session_state.colaborador['id']}")
        if contato is not None:
            st.session_state.contato = contato
            st.rerun()
        return

    st.success(f"👤 Estagiário: {st.session_state.colaborador['Nome']} | ✅ Contato salvo.")

    # 4. TELA FINAL / ESCOLHA DO KIT
    if st.session_state.cadastro_finalizado:
        st.divider()
        st.success("✅ Cadastro finalizado com sucesso! Obrigado.")
        if st.session_state.escolhas_kits:
            exibir_qrcode_final()
        st.balloons()
        return

    if st.session_state.escolhendo_kits:
        escolhas_kits = escolher_kits_colaborador()
        if escolhas_kits is not None:
            st.session_state.escolhas_kits = escolhas_kits
            st.session_state.escolhendo_kits = False
            st.session_state.cadastro_finalizado = True                
            st.rerun()
        return

    # 5. FLUXO DE CADASTRO DO ESTAGIÁRIO (FAKE IA)
    st.subheader("🎓 Cadastro Acadêmico")
    st.info("Por favor, preencha seus dados acadêmicos e anexe sua **Certidão de Nascimento ou RG** e a **Declaração Escolar / Comprovante de Matrícula**.")

    if 'escolaridade' not in st.session_state: st.session_state.escolaridade = ""
    if 'ano_escolar' not in st.session_state: st.session_state.ano_escolar = ""

    st.selectbox("Sua Escolaridade", ["", "Ensino Médio", "Ensino Superior"], format_func=lambda x: "Selecione a Escolaridade..." if x == "" else x, key="escolaridade")
    
    opcoes_ano = {
        "": [],
        "Ensino Médio": ["", "1º Ano", "2º Ano", "3º Ano"],
        "Ensino Superior": ["", "1º Semestre", "2º Semestre", "3º Semestre", "4º Semestre", "5º Semestre", "6º Semestre", "7º Semestre", "8º Semestre", "9º Semestre", "10º Semestre"]
    }
    
    if st.session_state.escolaridade:
        st.selectbox("Ano/Semestre em Andamento", opcoes_ano[st.session_state.escolaridade], format_func=lambda x: "Selecione o Ano/Semestre..." if x == "" else x, key="ano_escolar")

    with st.form("form_estagiario"):
        genero_est = st.selectbox("Seu Gênero:", ["", "Masculino", "Feminino"], format_func=lambda x: "Selecione o Gênero..." if x == "" else x)
        
        certidao_est = st.file_uploader("Anexe SUA Certidão de Nascimento ou RG", type=["pdf", "png", "jpg", "jpeg"], help="Se for RG, envie o documento aberto ou a parte que mostra os pais.")
        declaracao_est = st.file_uploader("Anexe SUA Declaração Escolar / Comprovante de Matrícula", type=["pdf", "png", "jpg", "jpeg"])
        
        st.divider()
        aceite_lgpd = st.checkbox("Concordo com o tratamento, armazenamento e uso dos dados para a concessão do Kit Escolar, em conformidade com a LGPD e as normas de compliance da instituição.", key="lgpd_est")
        salvar_est = st.form_submit_button("Validar e Adicionar")

    if salvar_est:
        if not verificar_limite_clique("valida_estagiario", 5):
            st.stop()

        # Verifica duplicidade: mesmo nome + mesma data de nascimento já cadastrados
        nome_verificacao = padroniza_texto(st.session_state.colaborador['Nome'])
        data_nasc_verificacao = st.session_state.colaborador['data_nascimento']
        db_dup = SessionLocal()
        try:
            ja_cadastrado = db_dup.query(Dependente).filter(
                Dependente.nome_filho == nome_verificacao,
                Dependente.data_nascimento == data_nasc_verificacao
            ).first()
        finally:
            db_dup.close()

        if ja_cadastrado:
            st.error("⚠️ Você já possui cadastro em nosso sistema.")
            st.stop()

        valido_cert = validar_arquivo(certidao_est, "Certidão/RG")
        valido_decl = validar_arquivo(declaracao_est, "Declaração Escolar")
        if not valido_cert or not valido_decl:
            st.stop()

        erros_est = []
        if not genero_est: erros_est.append("O gênero é obrigatório.")
        if not st.session_state.escolaridade: erros_est.append("A escolaridade é obrigatória.")
        if not st.session_state.ano_escolar: erros_est.append("O ano escolar é obrigatório.")
        if not certidao_est: erros_est.append("O RG / Certidão de Nascimento é obrigatório.")
        if not declaracao_est: erros_est.append("A Declaração Escolar é obrigatória.")
        if not aceite_lgpd: erros_est.append("Você deve aceitar a política de privacidade (LGPD) para prosseguir.")

        if erros_est:
            for e in erros_est: st.error(f"⚠️ {e}")
        else:
            # REGRA 3: Validação Fictícia ("Fingindo que a IA está lendo")
            with st.spinner("Analisando documentos... Aguarde"):
                time.sleep(3)
                
                # Fazendo os uploads reais para salvar no registro (opcional)
                cracha_colab = int(st.session_state.colaborador['Crachá'])
                url_doc_est = None
                try:
                    urls = []
                    u1 = upload_documento(certidao_est, "identidade", str(cracha_colab))
                    if u1: urls.append(u1)
                    u2 = upload_documento(declaracao_est, "declaracao", str(cracha_colab))
                    if u2: urls.append(u2)
                    url_doc_est = ",".join(urls) if urls else None
                except Exception:
                    pass

                db = SessionLocal()
                try:
                    colaborador_db = db.query(Colaborador).filter(Colaborador.cracha == cracha_colab).first()
                    nome_colab = padroniza_texto(st.session_state.colaborador['Nome'])
                    
                    novo_dep_db = Dependente(
                        id_colaborador=colaborador_db.id if colaborador_db else cracha_colab,
                        nome_filho=nome_colab,
                        data_nascimento=st.session_state.colaborador['data_nascimento'],
                        genero=genero_est,
                        escolaridade=st.session_state.escolaridade,
                        ano_escola=st.session_state.ano_escolar,
                        revisao_rh="Aprovado (Validação Estagiário)",
                        fluxo_documento="Estagiário - RG + Declaração Escolar",
                        aceite_ia=False,
                        aceite_lgpd=aceite_lgpd,
                        data_aceite=datetime.now(),
                        motivo_reprova_ia=None,
                        url_documento=url_doc_est
                    )
                    db.add(novo_dep_db)
                    db.commit()
                    db.refresh(novo_dep_db)
                    
                    registrar_log(cracha_colab, "VALIDACAO_SUCESSO", "Validação de Estagiário aprovada.")
                    st.success("✅ Documentos validados com sucesso!")
                    
                    st.session_state.escolhendo_kits = True
                    st.rerun()
                finally:
                    db.close()

interface()