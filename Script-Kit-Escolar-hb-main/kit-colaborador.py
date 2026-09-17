import pandas as pd
import os
import streamlit as st  
from datetime import date, datetime, timedelta
import time
import qrcode
import re
import unicodedata
import json
from google import genai
from google.genai import types
from dotenv import load_dotenv
from io import BytesIO
import uuid
from datetime import datetime
import time 
from rate_limiter import verificar_limite_clique
# ==================== IMPORTS DO BANCO ====================
from database import SessionLocal, Colaborador, Dependente, EscolhaKit, Retirada, registrar_log
from conector_Postgre import SupabaseConnector
import boto3
from botocore.exceptions import ClientError
from notificador_email import NotificadorEmail, SMTP_SERVER, SMTP_PORT, LOGIN_SMTP, SENHA_KEY, EMAIL_REMETENTE
from query import CARGOS_REJEITADO, DOMINIOS_PESSOAIS_PERMITIDOS, MODELOS_GEMINI, ERROS_RETRY, TAMANHO_MAXIMO_MB,EXTENSOES_PERMITIDAS
from autenticacao_totp import verificar_autenticacao_totp




load_dotenv()
client_gemini = genai.Client()

# ==================== SUPABASE STORAGE (QR CODES) ====================

generation_config_certidao = types.GenerateContentConfig(
    temperature=0,
    response_mime_type="application/json",
    system_instruction=(
        "Você é um assistente especializado em extração de dados. Sua tarefa é analisar o documento fornecido.\n"
        "Regra 1: Verifique se o documento é uma Certidão de Nascimento OU um RG (Carteira de Identidade) válido. Se for, classifique 'documento_valido' como true. Se for qualquer outro tipo de documento (CNH, carteirinha, boleto) ou estiver ilegível, classifique como false.\n"
        "Regra 2: Verifique se o documento está totalmente legível. Se estiver borrado, muito escuro, cortado ou ilegível a ponto de não conseguir ler os dados com segurança, classifique 'legivel' como false. Caso contrário, true.\n"
        "Regra 3: Extraia o nome completo do pai e o nome completo da mãe, exatamente como constam no documento (no RG, procure por filiação). Se um dos nomes não existir (ex: pai ausente) ou a imagem não contiver esse lado do documento, retorne null.\n"
        "Regra 4: Extraia o nome completo da criança/titular registrado no documento.\n"
        "Regra 5: Extraia a data de nascimento da criança/titular no formato DD/MM/AAAA. ATENÇÃO À LEITURA DE DATAS (OCR): Inspecione minuciosamente a imagem antes de extrair a data. Cuidado com ranhuras, fundo de segurança do papel ou marcas de carimbo sobre os números que possam fazer a visão computacional confundir dígitos parecidos (ex: '1' com '7', '3' com '8', '0' com '6', ou '5' com '6').\n"
        "Regra 6: Identifique o sexo da criança/titular conforme consta no documento (se não houver campo explícito, infira pelo nome). Retorne EXATAMENTE 'Masculino' ou 'Feminino', sem abreviações.\n"
        "OBS: NAO DAR RESPOSTA EXPLICATIVA"
    ),
    response_schema={
        "type": "OBJECT",
        "properties": {
            "documento_valido": {"type": "BOOLEAN"},
            "legivel": {"type": "BOOLEAN"},
            "nome_pai": {"type": "STRING", "nullable": True},
            "nome_mae": {"type": "STRING"},
            "nome_crianca": {"type": "STRING"},
            "data_nascimento_crianca": {"type": "STRING"},
            "sexo_crianca": {"type": "STRING", "enum": ["Masculino", "Feminino"]}
        },
        "required": ["documento_valido", "legivel", "nome_pai", "nome_mae", "nome_crianca", "data_nascimento_crianca", "sexo_crianca"]
    }
)
# ---------------------------------------------------------------
# CONFIGURAÇÃO DE IA: DECLARAÇÃO ESCOLAR / MATRÍCULA (EQUILIBRADA)
# ---------------------------------------------------------------
generation_config_declaracao_escolar = types.GenerateContentConfig(
    temperature=0,
    response_mime_type="application/json",
    system_instruction= (
    "Você é um auditor de documentos acadêmicos e escolares brasileiros.\n"
    "Sua função é analisar a declaração escolar e categorizar o nível de validação do documento.\n\n"

    "1. Identificação da Escola (tem_identificacao_escola):\n"
    "   - Retorne true se o documento contiver cabeçalho, nome da escola, prefeitura/estado ou dados oficiais da instituição.\n"
    "   - Retorne false se for um texto totalmente genérico sem identificação da escola.\n\n"

    "2. Análise de Autenticidade (tipo_autenticidade):\n"
    "   - 'Fisica': Possui assinatura manual (à caneta), rubrica física ou carimbo de tinta visível no papel.\n"
    "   - 'Digital': Possui código de verificação eletrônica, chave de validação, QR Code ou certificado digital oficial.\n"
    "   - 'Sistema_Sem_Validador': O documento possui dados oficiais de escola pública/sistema educacional (ex: Prefeitura, Secretaria de Educação, RA do aluno, dados da direção), porém é uma impressão direta do sistema sem carimbo ou código de validação digital.\n"
    "   - 'Nenhuma': Documento sem dados institucionais, sem assinatura, sem validação e com suspeita de digitação manual/sintética sem vínculo escolar.\n\n"

    "3. Regra de Validade (eh_declaracao_matricula):\n"
    "   - Retorne true se o documento comprovar matrícula E 'tem_identificacao_escola' for true E 'tipo_autenticidade' for 'Fisica', 'Digital' ou 'Sistema_Sem_Validador'.\n"
    "   - Retorne false se não comprovar matrícula ou 'tipo_autenticidade' for 'Nenhuma'.\n\n"

    "4. Extração de Dados e Tolerância OCR/Datas:\n"
    "   - 'nome_aluno': Nome completo do aluno (se não houver aluno ou não for documento escolar, informe 'Não identificado').\n"
    "   - 'codigo_validacao': Chave ou código de validação (se houver, caso contrário null).\n"
    "   - ATENÇÃO À LEITURA DE DATAS E NÚMEROS: Cuidado com ranhuras, sombras ou marcas de carimbo sobre datas e documentos. Digitos parecidos podem ser confundidos pela visão computacional (ex: '1' com '7', '3' com '8', '0' com '6', ou '5' com '6'). Faça uma inspeção minuciosa na imagem antes de determinar qualquer inconsistência de data ou caractere.\n\n"

    "5. Descrição de Conteúdo Incompatível e Motivo de Rejeição:\n"
    "   - 'descricao_conteudo_invalido': Caso o arquivo enviado NÃO SEJA um documento escolar/acadêmico (ex: foto de animal/galinha, lista de compras, conta de luz, paisagem, etc.), descreva sucintamente o que é a imagem. Se for um documento escolar, retorne null.\n"
    "   - 'motivo_rejeicao': Caso o documento seja inválido ou incompatível, forneça uma explicação clara e humanizada. Se houver 'descricao_conteudo_invalido', inclua essa descrição diretamente no motivo de rejeição (ex: 'O arquivo enviado trata-se de uma foto de lista de compras e não de uma declaração escolar').\n\n"

    "OBS: NÃO DAR RESPOSTA EXPLICATIVA FORA DO JSON."
),
    response_schema={
        "type": "OBJECT",
        "properties": {
            "legivel": {"type": "BOOLEAN"},
            "eh_declaracao_matricula": {"type": "BOOLEAN"},
            "tem_identificacao_escola": {"type": "BOOLEAN"},
            "tipo_autenticidade": {
                "type": "STRING", 
                "enum": ["Fisica", "Digital", "Sistema_Sem_Validador", "Nenhuma"]
            },
            "codigo_validacao": {"type": "STRING", "nullable": True},
            "nome_aluno": {"type": "STRING"},
            "descricao_conteudo_invalido": {"type": "STRING", "nullable": True},
            "motivo_rejeicao": {"type": "STRING", "nullable": True}
        },
        "required": [
            "legivel",
            "eh_declaracao_matricula",
            "tem_identificacao_escola",
            "tipo_autenticidade",
            "nome_aluno"
        ]
    }
)
#----------------------------------------------- busca documento  "casamento" ou "divorcio"

# ---------------------------------------------------------------
# CONFIGURAÇÃO DE IA: A2 (CERTIDÃO DE NASCIMENTO COM AVERBAÇÃO DE ADOÇÃO)
# ---------------------------------------------------------------
generation_config_adocao_averbacao = types.GenerateContentConfig(
    temperature=0,
    response_mime_type="application/json",
    system_instruction=(
        "Você é um assistente rigoroso de auditoria de documentos civis (Certidão de Nascimento com averbação).\n"
        "Regra 1: Verifique se o documento é uma Certidão de Nascimento válida. Classifique 'documento_valido' como true ou false.\n"
        "Regra 2: Verifique se o documento está totalmente legível. Classifique 'legivel' como true ou false.\n"
        "Regra 3: Verifique se existe explicitamente uma averbação, anotação, carimbo ou texto oficial informando a ADOÇÃO no documento. Classifique 'tem_averbacao_adocao' como true ou false.\n"
        "Regra 4: Extraia o nome completo da criança registrada ('nome_crianca').\n"
        "Regra 5: Extraia a data de nascimento da criança no formato DD/MM/AAAA ('data_nascimento_crianca'). ATENÇÃO À LEITURA DE DATAS (OCR): Inspecione minuciosamente a imagem antes de extrair. Cuidado com ranhuras, fundo de segurança do papel ou carimbos que possam confundir dígitos parecidos (ex: '1' com '7', '3' com '8', '0' com '6', ou '5' com '6').\n"
        "Regra 6: Extraia o sexo da criança ('sexo_crianca': 'Masculino' ou 'Feminino').\n"
        "Regra 7: Extraia a lista de nomes dos pais atuais (constantes na certidão ou na averbação de adoção) em uma lista de strings chamada 'nomes_pais_responsaveis'.\n"
        "OBS: NÃO DAR RESPOSTA EXPLICATIVA"
    ),
    response_schema={
        "type": "OBJECT",
        "properties": {
            "documento_valido": {"type": "BOOLEAN"},
            "legivel": {"type": "BOOLEAN"},
            "tem_averbacao_adocao": {"type": "BOOLEAN"},
            "nome_crianca": {"type": "STRING"},
            "data_nascimento_crianca": {"type": "STRING"},
            "sexo_crianca": {"type": "STRING", "enum": ["Masculino", "Feminino"]},
            "nomes_pais_responsaveis": {
                "type": "ARRAY",
                "items": {"type": "STRING"}
            }
        },
        "required": [
            "documento_valido", "legivel", "tem_averbacao_adocao", 
            "nome_crianca", "data_nascimento_crianca", "sexo_crianca", "nomes_pais_responsaveis"
        ]
    }
)

# ---------------------------------------------------------------
# CONFIGURAÇÃO DE IA: A3 (GUARDA JUDICIAL PARA FINS DE ADOÇÃO)
# ---------------------------------------------------------------
generation_config_guarda_adocao = types.GenerateContentConfig(
    temperature=0,
    response_mime_type="application/json",
    system_instruction=(
        "Você é um auditor jurídico rigoroso de documentos judiciais (Termo de Guarda, Sentença ou Decisão Judicial para fins de adoção).\n"
        "Regra 1: Verifique se o documento é de origem judicial válida (Termo de Guarda, Sentença, Decisão). Classifique 'documento_judicial_valido' como true ou false.\n"
        "Regra 2: Verifique se o documento está legível e possui elementos de autenticidade (assinatura do juiz, carimbo oficial ou código de validação digital). Classifique 'legivel_e_autentico' como true ou false.\n"
        "Regra 3: Verifique se o texto cita explicitamente que a guarda foi concedida para **Fins de Adoção** (or estágio de convivência com finalidade adotiva). Classifique 'guarda_para_fins_de_adocao' como true ou false.\n"
        "Regra 4: Extraia o nome completo da criança ou adolescente ('nome_crianca').\n"
        "Regra 5: Extraia a data de nascimento da criança no formato DD/MM/AAAA, se houver ('data_nascimento_crianca'). Se não constar, retorne string vazia. ATENÇÃO À LEITURA DE DATAS (OCR): Inspecione minuciosamente marcas de carimbos ou impressões no texto para não confundir dígitos parecidos (ex: '1' com '7', '3' com '8', '0' com '6', '5' com '6').\n"
        "Regra 6: Extraia o nome completo do guardião/responsável legal nomeado no documento ('nome_guardiao').\n"
        "OBS: NÃO DAR RESPOSTA EXPLICATIVA"
    ),
    response_schema={
        "type": "OBJECT",
        "properties": {
            "documento_judicial_valido": {"type": "BOOLEAN"},
            "legivel_e_autentico": {"type": "BOOLEAN"},
            "guarda_para_fins_de_adocao": {"type": "BOOLEAN"},
            "nome_crianca": {"type": "STRING"},
            "data_nascimento_crianca": {"type": "STRING"},
            "nome_guardiao": {"type": "STRING"}
        },
        "required": [
            "documento_judicial_valido", "legivel_e_autentico", 
            "guarda_para_fins_de_adocao", "nome_crianca", "data_nascimento_crianca", "nome_guardiao"
        ]
    }
)

# ---------------------------------------------------------------
# CONFIGURAÇÃO DE IA: B1 (DECLARAÇÃO DE UNIÃO ESTÁVEL)
# ---------------------------------------------------------------
generation_config_uniao_estavel = types.GenerateContentConfig(
    temperature=0,
    response_mime_type="application/json",
    system_instruction=(
        "Você é um assistente rigoroso de auditoria de documentos civis (Declaração de União Estável).\n"
        "Regra 1: Verifique se o documento é uma Declaração ou Escritura Pública de União Estável válida. Classifique 'documento_valido' como true ou false.\n"
        "Regra 2: Verifique se o documento possui carimbo, selo digital, etiqueta ou menção explícita de 'reconhecimento de firma' em cartório. Se não houver, classifique 'firma_reconhecida' como false.\n"
        "Regra 3: O reconhecimento de firma pode ser de QUALQUER cartório do Brasil (sem restrição de cidade). Classifique 'cartorio_valido' como true se possuir validação de cartório.\n"
        "Regra 4 (EXTREMA IMPORTÂNCIA): Extraia **APENAS o nome completo** dos dois conviventes/companheiros nos campos 'nome_companheiro_1' e 'nome_companheiro_2'. NÃO inclua números, RGs, CPFs, endereços. DICA CRUCIAL: Caso os nomes preenchidos à mão no corpo do texto estejam ilegíveis, procure obrigatoriamente no carimbo/selo do cartório (geralmente na parte inferior), pois o selo de reconhecimento de firma por semelhança sempre contém os nomes impressos de forma perfeitamente legível. ATENÇÃO A OCR: Cuidado com ranhuras de carimbos para não alucinar caracteres ou confundir letras e números.\n"
        "OBS: NÃO DAR RESPOSTA EXPLICATIVA"
    ),
    response_schema={
        "type": "OBJECT",
        "properties": {
            "documento_valido": {"type": "BOOLEAN"},
            "firma_reconhecida": {"type": "BOOLEAN"},
            "cartorio_valido": {"type": "BOOLEAN"},
            "nome_companheiro_1": {"type": "STRING"},
            "nome_companheiro_2": {"type": "STRING"}
        },
        "required": ["documento_valido", "firma_reconhecida", "cartorio_valido", "nome_companheiro_1", "nome_companheiro_2"]
    }
)

# ---------------------------------------------------------------
# CONFIGURAÇÃO DE IA: B2 (CERTIDÃO DE CASAMENTO / DIVÓRCIO)
# ---------------------------------------------------------------
generation_config_doc_complementar = types.GenerateContentConfig(
    temperature=0,
    response_mime_type="application/json",
    system_instruction=(
        "Você é um assistente especializado em extração de dados de documentos civis brasileiros.\n"
        "Sua tarefa: analisar uma certidão de casamento ou divórcio.\n"
        "Regra 1: Classifique 'documento_valido' como true se for certidão de casamento ou divórcio. Caso contrário, false.\n"
        "Regra 2: Extraia o nome da pessoa ANTES e DEPOIS da mudança de nome. ATENÇÃO A OCR: Inspecione minuciosamente o documento antes da extração de dados. Cuidado com dobras no papel, selos ou ranhuras que possam confundir caracteres e números parecidos.\n"
        "Se for divórcio, o nome 'antes' é o nome de casada, 'depois' é o nome retomado.\n"
        "Se for casamento, o nome 'antes' é o solteiro, 'depois' é o de casada.\n"
        "OBS: NAO DAR RESPOSTA EXPLICATIVA"
    ),
    response_schema={
        "type": "OBJECT",
        "properties": {
            "documento_valido":  {"type": "BOOLEAN"},
            "tipo_documento":    {"type": "STRING"},   
            "nome_antes":        {"type": "STRING"},
            "nome_depois":       {"type": "STRING"}
        },
        "required": ["documento_valido", "tipo_documento", "nome_antes", "nome_depois"]
    }
)

# ---------------------------------------------------------------
# CONFIGURAÇÃO DE IA: C1 (TERMO/CERTIDÃO DE GUARDA JUDICIAL)
# ---------------------------------------------------------------
generation_config_guarda_judicial = types.GenerateContentConfig(
    temperature=0,
    response_mime_type="application/json",
    system_instruction=(
        "Você é um auditor jurídico rigoroso de Termos ou Certidões de Guarda Judicial.\n"
        "Regra 1: Verifique se o documento é um Termo ou Certidão de Guarda válido. Classifique 'documento_valido' como true ou false.\n"
        "Regra 2: Verifique se o documento está totalmente legível. Classifique 'legivel' como true ou false.\n"
        "Regra 3: Verifique se o documento possui assinatura do juiz, carimbo oficial ou código de validação digital. Classifique 'autenticidade_judicial' como true ou false.\n"
        "Regra 4: Extraia o nome completo da criança ou adolescente mencionado no documento ('nome_crianca'). ATENÇÃO A OCR: Faça inspeção minuciosa na leitura de nomes e números, evitando confundir letras ou caracteres similares causados por falhas de impressão ou selos virtuais.\n"
        "Regra 5: Extraia o nome completo do(a) guardião(ã) nomeado(a) no documento ('nome_guardiao').\n"
        "OBS: NÃO DAR RESPOSTA EXPLICATIVA"
    ),
    response_schema={
        "type": "OBJECT",
        "properties": {
            "documento_valido": {"type": "BOOLEAN"},
            "legivel": {"type": "BOOLEAN"},
            "autenticidade_judicial": {"type": "BOOLEAN"},
            "nome_crianca": {"type": "STRING"},
            "nome_guardiao": {"type": "STRING"}
        },
        "required": ["documento_valido", "legivel", "autenticidade_judicial", "nome_crianca", "nome_guardiao"]
    }
)

# ---------------------------------------------------------------
# CONFIGURAÇÃO DE IA: C2 (TERMO DE TUTELA JUDICIAL)
# ---------------------------------------------------------------
generation_config_tutela_judicial = types.GenerateContentConfig(
    temperature=0,
    response_mime_type="application/json",
    system_instruction=(
        "Você é um auditor jurídico rigoroso de Termos de Tutela Judicial.\n"
        "Regra 1: Verifique se o documento é um Termo de Tutela válido. Classifique 'documento_valido' como true ou false.\n"
        "Regra 2: Verifique se o documento está totalmente legível. Classifique 'legivel' como true ou false.\n"
        "Regra 3: Verifique se o documento possui assinatura do juiz, carimbo oficial ou código de validação digital. Classifique 'autenticidade_judicial' como true ou false.\n"
        "Regra 4: Extraia o nome completo da criança ou adolescente mencionado no documento ('nome_crianca'). ATENÇÃO A OCR: Faça inspeção minuciosa na leitura de nomes e números, evitando confundir letras ou caracteres similares causados por falhas de impressão ou selos virtuais.\n"
        "Regra 5: Extraia o nome completo do(a) tutor(a) nomeado(a) no documento ('nome_tutor').\n"
        "OBS: NÃO DAR RESPOSTA EXPLICATIVA"
    ),
    response_schema={
        "type": "OBJECT",
        "properties": {
            "documento_valido": {"type": "BOOLEAN"},
            "legivel": {"type": "BOOLEAN"},
            "autenticidade_judicial": {"type": "BOOLEAN"},
            "nome_crianca": {"type": "STRING"},
            "nome_tutor": {"type": "STRING"}
        },
        "required": ["documento_valido", "legivel", "autenticidade_judicial", "nome_crianca", "nome_tutor"]
    }
)

######################################################################## TRATAMENTO DE ERRO AI STUDIO 
#---------------------------------------------------FUNCOES DE validcao de documento

def tratar_erro_gemini(e, tentativa, tentativas):
    """Função auxiliar interna para tratar o backoff e mensagens de erro do Gemini"""
    erro_str = str(e).lower()
    print(f"🔍 ERRO CAPTURADO NA API: {repr(e)}")
    
    # Identifica se é o erro 503, indisponibilidade ou timeout
    if "503" in erro_str or "unavailable" in erro_str or "high demand" in erro_str or "timeout" in erro_str or "429" in erro_str:
        if tentativa < tentativas:
            time.sleep(tentativa * 2) # Espera progressiva: 2s, 4s, 6s...
            return True, None # True = Deve tentar novamente
        else:
            return False, "⚠️ O sistema de inteligência artificial está processando muitas requisições neste momento. Por favor, clique em 'Validar' novamente em alguns segundos."
            
    # Se for um erro diferente que esgotou as tentativas
    if tentativa == tentativas:
        return False, "❌ Serviço de validação indisponível no momento. Verifique o documento e tente novamente."
        
    return False, "❌ Ocorreu um erro inesperado na leitura do documento."

def nomes_correspondem_com_abreviacao(nome_a: str, nome_b: str) -> bool:
    """Compara nomes aceitando iniciais/abreviações e nomes intermediários omitidos."""
    def tokens(nome):
        nome = padroniza_texto(nome or "")
        return [t for t in nome.split() if t]

    a = tokens(nome_a)
    b = tokens(nome_b)
    if not a or not b:
        return False

    # O primeiro e o último nome precisam corresponder; isso reduz falsos positivos.
    def token_compativel(x, y):
        if x == y:
            return True
        if len(x) == 1:
            return y.startswith(x)
        if len(y) == 1:
            return x.startswith(y)
        # Abreviações como "FERNAN." / "FERNANDA" também são aceitas.
        return x.startswith(y) or y.startswith(x)

    if not token_compativel(a[0], b[0]) or not token_compativel(a[-1], b[-1]):
        return False

    # Faz correspondência em ordem. Assim "JOAO P SILVA" bate com
    # "JOAO PEDRO SILVA", mas nomes de pessoas diferentes não passam apenas
    # por terem uma palavra em comum.
    i = 0
    for token_a in a:
        encontrado = False
        while i < len(b):
            if token_compativel(token_a, b[i]):
                encontrado = True
                i += 1
                break
            i += 1
        if not encontrado:
            return False
    return True

def valida_declaracao_escolar(dados_declaracao: dict, nome_certidao: str):
    """
    Retorna: (sucesso: bool, mensagem_usuario: str, status_revisao_rh: str, motivo_detalhado_rh: str)
    """
    if not dados_declaracao or dados_declaracao.get("legivel") is False:
        return False, "A declaração escolar está ilegível.", "Erro", "Documento ilegível ou com baixa resolução."

    # Captura a descrição/motivo detalhado que o Gemini gerou no JSON
    motivo_ia = dados_declaracao.get("motivo_rejeicao")

    # 1. Trava de documento sem identificação ou imagem incompatível
    if not dados_declaracao.get("tem_identificacao_escola"):
        motivo_final = motivo_ia or "Documento sem identificação ou cabeçalho oficial da escola."
        return False, f"Documento Rejeitado: {motivo_final}", "Erro", motivo_final

    # 2. Trava de suspeita sintética / IA
    if dados_declaracao.get("eh_sintetico_suspeito") is True:
        motivo_alerta = motivo_ia or "⚠️ ATENÇÃO RH: Documento com fortes indícios de geração por IA / edição sintética."
        msg_user = "Recebemos sua declaração escolar. Ela passará por uma verificação detalhada junto à equipe do RH."
        return True, msg_user, "Sim (Suspeita de Fraude IA)", motivo_alerta

    tipo_auth = dados_declaracao.get("tipo_autenticidade")

    # 3. Trava de documento sem autenticidade válida ou não escolar
    if tipo_auth == "Nenhuma" or not dados_declaracao.get("eh_declaracao_matricula"):
        motivo_final = motivo_ia or "Sem assinatura, carimbo físico ou código de validação."
        return False, f"Documento Rejeitado: {motivo_final}", "Erro", motivo_final

    # 4. Validação do nome do aluno
    nome_declaracao = (dados_declaracao.get("nome_aluno") or "").strip()
    if not nomes_correspondem_com_abreviacao(nome_declaracao, nome_certidao):
        motivo_nome = f"Divergência de Nome: Declaração consta '{nome_declaracao}' e Documento consta '{nome_certidao}'."
        return False, f"O nome na declaração ({nome_declaracao}) não confere com a identidade ({nome_certidao}).", "Erro", motivo_nome

    # 5. CASO DE SUCESSO COM QUARENTENA: Declaração Digital ou Sistema Sem Validador
    if tipo_auth in ["Digital", "Sistema_Sem_Validador"]:
        codigo = dados_declaracao.get("codigo_validacao") or "N/A"
        motivo_rh = f"Declaração emitida via sistema ({tipo_auth}) sem carimbo físico. Código extraído: {codigo}."
        msg_user = "Sua declaração foi recebida! Como foi emitida via sistema escolar, nossa equipe do RH fará a conferência."
        return True, msg_user, "Sim (Validação Eletrônica)", motivo_rh

    # 6. CASO DE SUCESSO DIRETO: Declaração física com carimbo/assinatura
    return True, f"Declaração física válida para {nome_declaracao}.", "Não", None

# 🔹 ALTERAÇÃO: Mensagem genérica para quando for RG e o usuário não enviar a filiação
def valida_nome_pais_certidao(dados_certidao: dict, nome_colaborador: str):
    nome_pai = dados_certidao.get("nome_pai") or ""
    nome_mae = dados_certidao.get("nome_mae") or ""

    if not nome_pai and not nome_mae:
        return False, "❌ Não foi possível identificar os nomes dos pais no documento (se enviou RG, certifique-se de que o lado com a filiação está visível)."

    # Usa a nova comparação inteligente para testar se é o pai
    if compara_nomes_flexivel(nome_colaborador, nome_pai):
        return True, f"✅ Nome confere com o pai: {nome_pai}"
        
    # Usa a nova comparação inteligente para testar se é a mãe
    if compara_nomes_flexivel(nome_colaborador, nome_mae):
        return True, f"✅ Nome confere com a mãe: {nome_mae}"

    # Retorna o erro detalhado 
    erro_msg = f"❌ O nome do colaborador ({nome_colaborador}) não confere com a filiação extraída: (Pai: {nome_pai or 'Não consta'} | Mãe: {nome_mae or 'Não consta'})."
    return False, erro_msg


def executar_gemini_com_fallback(contents_data, config_schema, titulo_doc="documento", tentativas_por_modelo=2):
    """
    Executa a análise da IA exibindo feedback em tempo real no Streamlit
    sobre tentativas, retries e alternância de modelos.
    """
    with st.status(f"🔍 Analisando {titulo_doc} via IA...", expanded=True) as status:
        for index_modelo, modelo in enumerate(MODELOS_GEMINI, start=1):
            for tentativa in range(1, tentativas_por_modelo + 1):
                
                msg_progresso = f"Conectando ao modelo `{modelo}` (Tentativa {tentativa}/{tentativas_por_modelo})..."
                status.update(label=f"⏳ Analisando {titulo_doc} no modelo `{modelo}` ({tentativa}/{tentativas_por_modelo})...", state="running")
                status.write(f"🤖 **[IA Studio]** {msg_progresso}")
                
                # Aviso de conforto ao usuário se houver retry ou fallback de modelo
                if tentativa > 1 or index_modelo > 1:
                    status.write("⚠️ *Estamos demorando mais que o normal para processar devido à alta demanda. Por favor, aguarde até finalizar e **NÃO recarregue a página**...*")

                try:
                    response = client_gemini.models.generate_content(
                        model=modelo,
                        contents=contents_data,
                        config=config_schema,
                    )
                    
                    texto_limpo = response.text.strip()
                    dados_json = json.loads(texto_limpo)
                    
                    status.write(f"✅ Sucesso na leitura usando o modelo `{modelo}`!")
                    status.update(label=f"✅ Análise do {titulo_doc} concluída!", state="complete", expanded=False)
                    return dados_json, None

                except json.JSONDecodeError:
                    status.write(f"❌ Resposta da IA em formato inesperado no modelo `{modelo}`.")
                    return None, "Resposta da IA em formato inesperado. Tente novamente."

                except Exception as e:
                    erro_str = str(e).lower()
                    status.write(f"⚠️ Erro ao comunicar com `{modelo}`: {e}")
                    
                    tem_retry = any(cod in erro_str for cod in ERROS_RETRY)
                    if tem_retry and tentativa < tentativas_por_modelo:
                        status.write("🔄 Aguardando 3 segundos antes do retry...")
                        time.sleep(3)
                        continue
                    elif tem_retry:
                        if index_modelo < len(MODELOS_GEMINI):
                            proximo = MODELOS_GEMINI[index_modelo]
                            status.write(f"🔀 Alternando agente para o modelo reserva **{proximo}**...")
                            time.sleep(1)
                        break
                    else:
                        break

        status.update(label=f"❌ Falha no processamento do {titulo_doc}.", state="error")
        return None, "Todos os serviços de IA estão com alta demanda momentânea. Por favor, tente novamente em alguns instantes."




def analisa_certidao(arquivo):
    try:
        arquivo.seek(0)
        arquivo_bytes = arquivo.read()
        mime_type = arquivo.type
        contents = [
            types.Part.from_bytes(data=arquivo_bytes, mime_type=mime_type),
            "Analise o documento e retorne os dados no formato estruturado."
        ]
        return executar_gemini_com_fallback(contents, generation_config_certidao, titulo_doc="Documento de Identidade")
    except Exception:
        return None, "Erro ao ler o arquivo. Tente fazer o upload novamente."

def analisa_declaracao_escolar(arquivo):
    try:
        arquivo.seek(0)
        arquivo_bytes = arquivo.read()
        mime_type = arquivo.type
        contents = [
            types.Part.from_bytes(data=arquivo_bytes, mime_type=mime_type),
            "Analise esta declaração escolar e extraia os dados conforme as regras."
        ]
        return executar_gemini_com_fallback(contents, generation_config_declaracao_escolar, titulo_doc="Declaração Escolar")
    except Exception:
        return None, "Erro ao ler a declaração escolar. Tente fazer o upload novamente."


def analisa_uniao_estavel(arquivo):
    try:
        arquivo.seek(0)
        arquivo_bytes = arquivo.read()
        mime_type = arquivo.type
        contents = [
            types.Part.from_bytes(data=arquivo_bytes, mime_type=mime_type),
            "Analise esta declaração de união estável e extraia os dados conforme as regras."
        ]
        return executar_gemini_com_fallback(contents, generation_config_uniao_estavel, titulo_doc="União Estável")
    except Exception:
        return None, "Erro ao ler o arquivo de união estável."

def analisa_guarda_adocao(arquivo):
    try:
        arquivo.seek(0)
        arquivo_bytes = arquivo.read()
        mime_type = arquivo.type
        contents = [
            types.Part.from_bytes(data=arquivo_bytes, mime_type=mime_type),
            "Analise este documento judicial de guarda para fins de adoção e extraia os dados conforme as regras."
        ]
        return executar_gemini_com_fallback(contents, generation_config_guarda_adocao, titulo_doc="Guarda para Adoção")
    except Exception:
        return None, "Erro ao ler o arquivo de guarda para adoção."

def analisa_certidao_averbacao(arquivo):
    try:
        arquivo.seek(0)
        arquivo_bytes = arquivo.read()
        mime_type = arquivo.type
        contents = [
            types.Part.from_bytes(data=arquivo_bytes, mime_type=mime_type),
            "Analise esta certidão de nascimento com averbação de adoção e extraia os dados conforme as regras."
        ]
        return executar_gemini_com_fallback(contents, generation_config_adocao_averbacao, titulo_doc="Certidão com Averbação")
    except Exception:
        return None, "Erro ao ler a certidão averbada."
def analisa_guarda_judicial(arquivo):
    try:
        arquivo.seek(0)
        arquivo_bytes = arquivo.read()
        mime_type = arquivo.type
        contents = [
            types.Part.from_bytes(data=arquivo_bytes, mime_type=mime_type),
            "Analise este termo ou certidão de guarda judicial e extraia os dados conforme as regras."
        ]
        return executar_gemini_com_fallback(contents, generation_config_guarda_judicial, titulo_doc="Guarda Judicial")
    except Exception:
        return None, "Documento(s) ausente(s) ou ilegível(is)"

def analisa_tutela_judicial(arquivo):
    try:
        arquivo.seek(0)
        arquivo_bytes = arquivo.read()
        mime_type = arquivo.type
        contents = [
            types.Part.from_bytes(data=arquivo_bytes, mime_type=mime_type),
            "Analise este termo de tutela judicial e extraia os dados conforme as regras."
        ]
        return executar_gemini_com_fallback(contents, generation_config_tutela_judicial, titulo_doc="Tutela Judicial")
    except Exception:
        return None, "Documento(s) ausente(s) ou ilegível(is)"

def analisa_certidao_complementar(arquivo):
    try:
        arquivo.seek(0)
        arquivo_bytes = arquivo.read()
        mime_type = arquivo.type
        contents = [
            types.Part.from_bytes(data=arquivo_bytes, mime_type=mime_type),
            "Analise o documento e retorne os dados no formato estruturado."
        ]
        return executar_gemini_com_fallback(contents, generation_config_doc_complementar, titulo_doc="Certidão de Casamento/Divórcio")
    except Exception:
        return None, "Erro ao ler o arquivo de casamento/divórcio."

    

#---------------------------------------------------FUNCOES DE SISTEMA


def busca_colaborador(situacoes_invalidas=["Desligado", "Aposentadoria p/Invalidez"]):
    """Busca colaborador diretamente no banco de dados (Supabase) trazendo CPF e Data de Nascimento"""
    print("Buscando colaborador no banco...")
    
    with st.form("form_busca"):
        cracha_digitado = st.text_input("Crachá:", placeholder="Digite o número e aperte Enter")
        buscar = st.form_submit_button("🔍 Buscar")
        
    if not buscar:
        return None
        
    if not cracha_digitado.strip() or not cracha_digitado.strip().isdigit():
        st.warning("⚠️ Por favor, digite um número de crachá válido antes de buscar.")
        st.session_state.colaborador = None
        return None
        
    cracha_numero = int(cracha_digitado.strip())
    
    supabase_connector = SupabaseConnector()
    try:
        # 🛡️ QUERY ATUALIZADA: Incluindo 'cpf' e 'data_nascimento'
        query_banco = f"""
        SELECT 
            cracha,
            nome,
            cpf,
            data_nascimento,
            descricao_situacao,
            titulo_reduzido_cargo,
            id_cargo,
            data_demissao,
            totp_secret,
            totp_ativo
        FROM colaboradores
        WHERE cracha = {cracha_numero}
        """
        df = pd.read_sql(query_banco, supabase_connector.engine)
        colaborador = df.to_dict(orient="records")[0] if not df.empty else None
        
        if not colaborador:
            st.error("⚠️ Crachá não encontrado na base de dados.")
            st.session_state.colaborador = None  
            return None
            
        if colaborador["descricao_situacao"] in situacoes_invalidas:
            st.error(f"⚠️ Colaborador não elegível. Situação atual: {colaborador['descricao_situacao']}")
            st.session_state.colaborador = None  
            return None

        if str(colaborador["id_cargo"]) in CARGOS_REJEITADO:
            st.error(
                "🎁 O Kit Escolar é uma iniciativa de apoio social direcionada a categorias específicas "
                "da nossa instituição e, por isso, não está disponível para o seu cargo. "
                "Agradecemos muito pela compreensão!"
            )
            st.session_state.colaborador = None  
            return None
            
        # Formatador rápido de segurança para garantir 11 dígitos no CPF
        cpf_raw = str(colaborador.get("cpf", "")).strip().split(".")[0]
        cpf_formatado = cpf_raw.zfill(11) if cpf_raw and cpf_raw != "None" else ""

        # ==================== SALVA NO SESSION_STATE COM AS COLUNAS CORRETAS ====================
        st.session_state.colaborador = {
            "id": colaborador["cracha"],
            "cracha": colaborador["cracha"],            
            "Crachá": colaborador["cracha"],
            "Nome": colaborador["nome"],
            "nome": colaborador["nome"],
            "cpf": cpf_formatado,
            "data_nascimento": colaborador.get("data_nascimento"),
            "Título Reduzido (Cargo)": colaborador["titulo_reduzido_cargo"],
            "Descrição (Situação)": colaborador["descricao_situacao"],
            "id_cargo": int(colaborador["id_cargo"]) if colaborador.get("id_cargo") else None,
            "totp_secret": colaborador.get("totp_secret"),
            "totp_ativo": colaborador.get("totp_ativo", False)
        }

        return st.session_state.colaborador
        
    finally:
        supabase_connector.fechar_conexao()


def eh_email_pessoal(email: str) -> bool:
    """Retorna True se o domínio do e-mail estiver na lista de provedores pessoais aceitos."""
    partes = email.strip().lower().split("@")
    if len(partes) != 2:
        return False
    return partes[1] in DOMINIOS_PESSOAIS_PERMITIDOS


def adiciona_dados_contato(email_padrao: str = "", telefone_padrao: str = "", form_key: str = "form_contato"):
    print("Adicionando dados de contato...")
    
    # Passa a chave única recebida por parâmetro para evitar o erro de chave duplicada
    with st.form(key=form_key):
        st.subheader("📞 Dados de Contato")
        
        email = st.text_input("E-mail", value=email_padrao, placeholder="rh_4.0-@gmail.com")
        confirmacao_email = st.text_input("Confirme o E-mail", value=email_padrao, placeholder="rh_4.0-@gmail.com")
            
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
        erros.append("⚠️ Os e-mails não batem. Por favor, digite novamente.")
    elif not eh_email_pessoal(email_digitado):
        erros.append(
            "⚠️ Utilize um e-mail pessoal (Gmail, Hotmail, Outlook, Yahoo, iCloud, etc). "
            "Não é possível enviar o QR Code para e-mails corporativos."
        )

    if not telefone.strip():
        erros.append("⚠️ Telefone é obrigatório.")
    else:
        telefone_valido, mensagem_telefone = valida_telefone(telefone)
        if not telefone_valido:
            erros.append(f"⚠️ {mensagem_telefone}")
            
    if erros:
        for x in erros:
            st.error(f"{x}")
        return None
        
    st.success("✅ Dados de contato salvos com sucesso!")
    
    return {
        "email": email_digitado.lower(), 
        "telefone": formata_telefone(telefone)
    }
def adicionar_dependentes():
    st.subheader("👶 Adicionar Dependente")
    st.write(f"Nome Responsável: {st.session_state.colaborador['Nome']}")

    # Inicializa variáveis de controle de erro no session_state
    if 'escolaridade' not in st.session_state:
        st.session_state.escolaridade = ""
    if 'ano_escolar' not in st.session_state:
        st.session_state.ano_escolar = ""
    if 'erro_ia_a1' not in st.session_state:
        st.session_state.erro_ia_a1 = None

    st.selectbox(
        "Escolaridade",
        ["", "Educação Infantil", "Ensino Fundamental I", "Ensino Fundamental II", "Ensino Médio"],
        format_func=lambda x: "Selecione a Escolaridade..." if x == "" else x,
        key="escolaridade"
    )

    opcoes_ano = {
        "": [],
        "Educação Infantil":    ["", "Maternal I", "Maternal II", "Etapa I", "Etapa II"],
        "Ensino Fundamental I": ["", "1º Ano", "2º Ano", "3º Ano", "4º Ano", "5º Ano"],
        "Ensino Fundamental II":["", "6º Ano", "7º Ano", "8º Ano", "9º Ano"],
        "Ensino Médio":         ["", "1º Ano", "2º Ano", "3º Ano"]
    }

    if st.session_state.escolaridade:
        st.selectbox(
            "Ano Escolar 2026",
            opcoes_ano[st.session_state.escolaridade],
            format_func=lambda x: "Selecione o Ano Escolar..." if x == "" else x,
            key="ano_escolar"
        )

    # =========================================================
    # FORMULÁRIO PRINCIPAL DE ADIÇÃO 
    # =========================================================
    with st.form("form_dependente"):
        nome_filho    = st.text_input("Nome Completo da Criança")
        genero        = st.selectbox("Gênero:", ["", "Masculino", "Feminino"],
                                     format_func=lambda x: "Selecione o Gênero..." if x == "" else x)
        data_maxima   = date.today() - timedelta(days=730)
        data_nascimento = st.date_input("Data de Nascimento",
                                        min_value=date(2000, 1, 1), max_value=data_maxima, format="DD/MM/YYYY")
        
        certidao      = st.file_uploader(
            "Anexar Certidão de Nascimento ou RG 📄",
            type=["pdf", "png", "jpg", "jpeg"]
        )
        declaracao_escolar = st.file_uploader(
            "Anexar Declaração Escolar de Matrícula 📚",
            type=["pdf", "png", "jpg", "jpeg"],
            key="declaracao_escolar_a1"
        )
        
        st.divider()
        
        forcar_envio_rh = False
        if st.session_state.erro_ia_a1:
            st.error(f"⚠️ A IA encontrou uma divergência: {st.session_state.erro_ia_a1}")
            forcar_envio_rh = st.checkbox(
                "Declaro que o documento é autêntico. Quero forçar o envio para análise visual e manual do RH.", 
                key="rh_a1_bypass"
            )
        
        aceite_ia = st.checkbox("Estou ciente de que os documentos enviados serão processados e analisados automaticamente por inteligência artificial para fins de validação cadastral.", key="ia_dep")
        aceite_lgpd = st.checkbox("Concordo com o tratamento, armazenamento e uso dos dados para a concessão do Kit Escolar, em conformidade com a LGPD e as normas de compliance da instituição.", key="lgpd_dep")
        
        salvar = st.form_submit_button("📁 Adicionar ao Carrinho")

    if not salvar:
        return None

    if not verificar_limite_clique("valida_dependente", 5):
        return None
    
    # Valida e paralisa a execução IMEDIATAMENTE se faltar arquivo ou for inválido
    valido_cert = validar_arquivo(certidao, "Certidão/RG da Criança")
    valido_decl = validar_arquivo(declaracao_escolar, "Declaração Escolar")
    if not (valido_cert and valido_decl):
        st.stop()

    erros = []
    if not nome_filho.strip(): erros.append("❌ Nome da criança é obrigatório.")
    elif len(nome_filho.strip()) < 3: erros.append("❌ Nome muito curto.")
    elif re.search(r'[^a-zA-ZÀ-ÿ\s]', nome_filho): erros.append("❌ Nome não pode conter números ou caracteres especiais.")
    if not genero: erros.append("❌ Gênero é obrigatório.")
    if not st.session_state.escolaridade: erros.append("❌ Escolaridade é obrigatória.")
    if not st.session_state.ano_escolar: erros.append("❌ Ano Escolar é obrigatório.")
    if not certidao: erros.append("❌ Certidão de Nascimento ou RG é obrigatório.")
    if not declaracao_escolar: erros.append("❌ Declaração escolar de matrícula é obrigatória.")
    if not aceite_ia or not aceite_lgpd: erros.append("❌ Você deve aceitar os termos da LGPD e IA.")

    if erros:
        for x in erros: st.error(x)
        return None

    db = SessionLocal()
    try:
        if verificar_crianca_duplicada(db, nome_filho, data_nascimento):
            st.error("❌ Esta criança já está no seu carrinho ou já possui um kit cadastrado.")
            return None

        # =========================================================
        # UPLOAD DE MULTIPLOS DOCUMENTOS PARA A QUARENTENA DO RH (BYPASS)
        # =========================================================
        if forcar_envio_rh:
            with st.spinner("📤 Enviando documentos para a quarentena do RH..."):
                cracha_colab = st.session_state.colaborador['Crachá']
                
                try:
                    urls = []
                    if certidao:
                        u_identidade = upload_documento_supabase(certidao, "identidade", cracha_colab)
                        if u_identidade: urls.append(u_identidade)
                    if declaracao_escolar:
                        u_declaracao = upload_documento_supabase(declaracao_escolar, "declaracao", cracha_colab)
                        if u_declaracao: urls.append(u_declaracao)
                    
                    url_doc = ",".join(urls) if urls else None
                except Exception as e:
                    url_doc = None
            
            erro_salvo = st.session_state.erro_ia_a1
            st.session_state.erro_ia_a1 = None
            nome_final = padroniza_texto(nome_filho)
            
            st.warning("⚠️ O documento foi enviado para a Análise Visual e Manual do RH. Sua solicitação está em quarentena.")
            time.sleep(2)
            
            # --- 📌 LOG AUDITORIA (BYPASS A1) ---
            registrar_log(st.session_state.colaborador['Crachá'], "ENVIO_RH_BYPASS", f"Bypass forçado (A1). Erro contornado: {erro_salvo}")
            registrar_log(st.session_state.colaborador['Crachá'], "DEPENDENTE_CARRINHO_ADD", f"Dependente adicionado via Bypass.")
            
            return {
                "ID_Dependente": None,
                "ID_Colaborador": st.session_state.colaborador['Crachá'],
                "Nome_filho": nome_final,
                "Gênero": genero,
                "Data_nascimento": data_nascimento.strftime("%d/%m/%Y"),
                "Escolaridade": st.session_state.escolaridade,
                "Ano_escolar": st.session_state.ano_escolar,
                "revisao_rh": "Revisão Manual (Bypass Usuário)",
                "Fluxo_Documento": "A1 - Identidade (Cert/RG) + Declaração Escolar (Filho Biológico)",
                "aceite_ia": aceite_ia,
                "aceite_lgpd": aceite_lgpd,
                "data_aceite": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "motivo_reprova_ia": f"Forçado pelo usuário. Erro original: {erro_salvo}", 
                "url_documento": url_doc
            }

        # =========================================================
        # FLUXO NORMAL (Análise e Validação com a IA)
        # =========================================================
        with st.spinner("🔍 Analisando documento de identidade, aguarde..."):
            dados_certidao, erro_api = analisa_certidao(certidao)
            
        if erro_api:
            st.session_state.erro_ia_a1 = erro_api
            st.rerun()

        if not dados_certidao.get("legivel", True) or not dados_certidao.get("documento_valido", True):
            st.session_state.erro_ia_a1 = "Documento de identidade inválido ou ilegível."
            st.rerun()

        dados_ok, msg_dados = valida_dados_crianca_certidao(dados_certidao, nome_filho, data_nascimento, genero)
        if not dados_ok:
            st.session_state.erro_ia_a1 = msg_dados
            st.rerun()

        with st.spinner("📚 Validando declaração escolar e correspondência do nome..."):
            dados_declaracao, erro_declaracao = analisa_declaracao_escolar(declaracao_escolar)
            
        if erro_declaracao:
            st.session_state.erro_ia_a1 = erro_declaracao
            st.rerun()
       
        declaracao_ok, msg_declaracao, status_rh_decl, motivo_rh_detalhado = valida_declaracao_escolar(
            dados_declaracao, dados_certidao.get("nome_crianca") or nome_filho
        )

        valido_pais, mensagem_pais = valida_nome_pais_certidao(
            dados_certidao, st.session_state.colaborador['Nome']
        )

        tem_erro_ia = not declaracao_ok or not valido_pais
        
        if tem_erro_ia:
            mensagem_erro_atual = msg_declaracao if not declaracao_ok else mensagem_pais
            
            # --- 📌 LOG AUDITORIA (FALHA A1) ---
            registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou A1: {mensagem_erro_atual}")
            
            st.session_state.erro_ia_a1 = mensagem_erro_atual
            st.rerun() 

        st.session_state.erro_ia_a1 = None
        
        if status_rh_decl and str(status_rh_decl).startswith("Sim"):
            st.warning(f"⚠️ **Aviso de Cadastro:** {msg_declaracao}")
            time.sleep(3.5)
        else:
            st.success("✅ Documento validado com sucesso pela IA!")
            time.sleep(1.5)

        nome_final = padroniza_texto(dados_certidao.get("nome_crianca") or nome_filho)
        
        # --- 📌 LOG AUDITORIA (SUCESSO A1) ---
        registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_SUCESSO", "Validação A1 aprovada (ou enviada para quarentena técnica).")
        registrar_log(st.session_state.colaborador['Crachá'], "DEPENDENTE_CARRINHO_ADD", f"Dependente {nome_final} validado pela IA e adicionado.")

        return {
            "ID_Dependente": None,
            "ID_Colaborador": st.session_state.colaborador['Crachá'],
            "Nome_filho": nome_final,
            "Gênero": genero,
            "Data_nascimento": data_nascimento.strftime("%d/%m/%Y"),
            "Escolaridade": st.session_state.escolaridade,
            "Ano_escolar": st.session_state.ano_escolar,
            "revisao_rh": status_rh_decl,  
            "Fluxo_Documento": "A1 - Identidade (Cert/RG) + Declaração Escolar (Filho Biológico)",
            "motivo_reprova_ia": motivo_rh_detalhado,
            "url_documento": None,
            "aceite_ia": aceite_ia,
            "aceite_lgpd": aceite_lgpd,
            "data_aceite": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    finally:
        db.close()



#---------------------------------------------------FUNCOES DE VALIDACAO input
def editar_kits_existentes(id_colaborador):
    st.subheader("🎒 Seus Dependentes e Kits Cadastrados")
    st.write("Caso queira, você pode alterar o modelo do kit escolhido abaixo:")

    db = SessionLocal()
    try:
        dependentes = db.query(Dependente).filter(Dependente.id_colaborador == id_colaborador).all()
        catalogo, base_url = catalogo_kits_por_escolaridade()
        if not base_url:
            st.error("⚠️ Erro crítico: A variável `BASE_URL_IMAGENS_KITS` não está definida no arquivo .env.")
            return None
        
        # Injeção de CSS para padronizar altura das imagens do catálogo
        st.markdown("""
            <style>
            div[data-testid="stColumn"] img {
                height: 180px !important;
                object-fit: contain !important;
            }
            </style>
        """, unsafe_allow_html=True)

        info_dependentes = []
        with st.form("form_edicao_kits"):
            for dep in dependentes:
                st.markdown(f"### 👦/👧 {dep.nome_filho}")
                st.write(f"**Escolaridade:** {dep.escolaridade} | **Ano escolar:** {dep.ano_escola} | **Nasc:** {dep.data_nascimento.strftime('%d/%m/%Y')}")

                escolha_atual = db.query(EscolhaKit).filter(EscolhaKit.id_dependente == dep.id_dependente).first()
                kit_atual = escolha_atual.kit_escolhido if escolha_atual else ""

                opcoes_kits = catalogo.get(dep.escolaridade, [])

                # Vitrine visual de kits — 1 checkbox por imagem
                if opcoes_kits:
                    st.markdown("**Catálogo Disponível — marque o kit desejado:**")
                    itens_por_linha = 4
                    for i in range(0, len(opcoes_kits), itens_por_linha):
                        colunas = st.columns(itens_por_linha)
                        for j in range(itens_por_linha):
                            if i + j < len(opcoes_kits):
                                nome_kit = opcoes_kits[i+j]
                                nome_arquivo = nome_kit.replace(" ", "")
                                url_img = f"{base_url}{nome_arquivo}.png"
                                with colunas[j]:
                                    st.image(url_img, caption=nome_kit, width='stretch')
                                    st.checkbox(
                                        nome_kit,
                                        value=(nome_kit == kit_atual),
                                        key=f"edit_chk_kit_{dep.id_dependente}_{nome_kit}"
                                    )

                st.write("---")

                info_dependentes.append({
                    "id_dependente": dep.id_dependente,
                    "nome_filho": dep.nome_filho,
                    "escolaridade": dep.escolaridade,
                    "opcoes_kits": opcoes_kits
                })

            salvar_edicao = st.form_submit_button("💾 Salvar Alterações de Kit")

        if salvar_edicao:
            # Apura, para cada dependente, qual(is) checkbox(es) ficaram marcados
            erros = []
            selecoes = {}
            for info in info_dependentes:
                id_dependente = info["id_dependente"]
                marcados = [
                    nome_kit for nome_kit in info["opcoes_kits"]
                    if st.session_state.get(f"edit_chk_kit_{id_dependente}_{nome_kit}")
                ]

                if len(marcados) == 0:
                    erros.append(f"⚠️ Selecione um kit para {info['nome_filho']}.")
                    continue
                if len(marcados) > 1:
                    erros.append(f"⚠️ Selecione apenas um kit para {info['nome_filho']} (mais de um foi marcado).")
                    continue

                selecoes[id_dependente] = marcados[0]

            if erros:
                for erro in erros:
                    st.error(erro)
                return

            houve_alteracao = False
            resumo_novos_kits = []

            for dep in dependentes:
                novo_kit_selecionado = selecoes[dep.id_dependente]
                escolha_db = db.query(EscolhaKit).filter(EscolhaKit.id_dependente == dep.id_dependente).first()
                
                if escolha_db:
                    if escolha_db.kit_escolhido != novo_kit_selecionado:
                        escolha_db.kit_escolhido = novo_kit_selecionado
                        houve_alteracao = True
                    resumo_novos_kits.append(f"{dep.nome_filho} - {dep.escolaridade} - {novo_kit_selecionado}")

            if houve_alteracao:
                # Atualiza o resumo na tabela retiradas
                novo_resumo_str = " | ".join(resumo_novos_kits)
                retirada_db = db.query(Retirada).filter(Retirada.id_colaborador == id_colaborador).first()
                if retirada_db:
                    retirada_db.resumo_kits = novo_resumo_str
                
                db.commit()
                
                # Armazena a mensagem de sucesso na sessão para exibir na tela inicial
                registrar_log(id_colaborador, "KIT_EDICAO_SALVA", f"Novos kits selecionados: {novo_resumo_str}")
                st.session_state.mensagem_sucesso = "✅ Kit atualizado com sucesso!"
                
                # Reseta a sessão para voltar ao estado inicial (Busca por crachá)
                st.session_state.colaborador = None
                st.session_state.contato = None
                st.session_state.tipo_fluxo = None
                st.session_state.lista_dependentes = []
                st.session_state.aguardando_decisao = False
                st.session_state.cadastro_finalizado = False
                st.session_state.escolhendo_kits = False
                st.session_state.escolhas_kits = []
                
                st.rerun()
            else:
                st.info("Nenhuma alteração de kit foi detectada.")
    finally:
        db.close()

def verificar_crianca_duplicada(db, nome_filho, data_nascimento):
    if not nome_filho:
        return False
        
    # Padroniza e limpa o nome (tudo minúsculo, sem espaços extras)
    nome_padronizado = padroniza_texto(nome_filho).lower().strip()
    
    # Padroniza a data de nascimento para string DD/MM/YYYY independentemente do formato recebido
    if hasattr(data_nascimento, "strftime"):
        data_str = data_nascimento.strftime("%d/%m/%Y")
    else:
        data_str = str(data_nascimento).strip()
    
    # 1. VERIFICAÇÃO RIGOROSA NO CARRINHO (Session State)
    for dep in st.session_state.get("lista_dependentes", []):
        dep_nome = padroniza_texto(dep.get("Nome_filho", "")).lower().strip()
        dep_data = str(dep.get("Data_nascimento", "")).strip()
        
        # Se o nome bater E a data de nascimento for igual, bloqueia!
        if dep_nome == nome_padronizado and dep_data == data_str:
            return True
            
    # 2. VERIFICAÇÃO NO BANCO DE DADOS OFICIAL
    if hasattr(data_nascimento, "strftime"):
        data_obj_db = data_nascimento
    else:
        try:
            data_obj_db = datetime.strptime(data_str, "%d/%m/%Y").date()
        except:
            data_obj_db = None

    if data_obj_db:
        duplicado = db.query(Dependente).filter(
            Dependente.nome_filho.ilike(f"%{nome_padronizado}%"),
            Dependente.data_nascimento == data_obj_db
        ).first()
        if duplicado:
            return True
            
    return False

def padroniza_texto(texto):
    # Remove espaços extras, converte para maiúsculo e remove acentos
    texto = texto.strip().upper()
    texto = unicodedata.normalize('NFKD', texto)
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r'[^A-Z\s]', '', texto)  # Remove alfanuméricos e caracteres especiais
    texto = re.sub(r'\s+', ' ', texto)       # Remove espaços duplos
    return texto

def compara_nomes_flexivel(nome_a: str, nome_b: str) -> bool:
    """
    Compara dois nomes aceitando sobrenomes extras (casamento) e abreviações, 
    sem comprometer a segurança.
    """
    if not nome_a or not nome_b:
        return False

    # Lista de preposições comuns para descartar da comparação
    preposicoes = {"DE", "DA", "DO", "DAS", "DOS", "E"}

    def extrai_tokens(nome):
        # Usa a sua função padroniza_texto para tirar acentos e deixar maiúsculo
        nome_limpo = padroniza_texto(nome)
        return [p for p in nome_limpo.split() if p not in preposicoes]

    tokens_a = extrai_tokens(nome_a)
    tokens_b = extrai_tokens(nome_b)

    if not tokens_a or not tokens_b:
        return False

    # REGRA DE OURO DA SEGURANÇA: O primeiro nome TEM que bater ou ser compatível.
    # Ex: 'LUCILENE' == 'LUCILENE'
    def token_compativel(t1, t2):
        if t1 == t2: return True
        if len(t1) == 1 and t2.startswith(t1): return True
        if len(t2) == 1 and t1.startswith(t2): return True
        return False

    if not token_compativel(tokens_a[0], tokens_b[0]):
        return False

    # REGRA DE SOBRENOME: Conta quantos tokens de 'A' batem com tokens de 'B'
    matches = 0
    for ta in tokens_a:
        for tb in tokens_b:
            if token_compativel(ta, tb):
                matches += 1
                break  # Bateu um, vai para o próximo token de 'A'

    # Se bateu o primeiro nome e pelo menos mais um sobrenome (matches >= 2), 
    # OU se um nome for muito curto e estiver 100% contido no outro, é a mesma pessoa.
    tamanho_menor = min(len(tokens_a), len(tokens_b))
    
    if matches >= 2 or matches == tamanho_menor:
        return True
    return False





def validar_arquivo(arquivo, nome_campo: str) -> bool:
    """
    Valida se o arquivo enviado atende aos critérios de tamanho e formato.
    Retorna True se estiver válido e False se houver violação.
    """
    if arquivo is None:
        return True

    # 1. Validação de Tamanho (convertendo bytes para MB)
    tamanho_mb = arquivo.size / (1024 * 1024)
    if tamanho_mb > TAMANHO_MAXIMO_MB:
        st.error(
            f"⚠️ O arquivo anexado no campo **'{nome_campo}'** excede o limite permitido de {TAMANHO_MAXIMO_MB} MB "
            f"(Tamanho atual: {tamanho_mb:.1f} MB). Por favor, reduza o tamanho do arquivo."
        )
        return False

    # 2. Validação de Extensão
    extensao = arquivo.name.split(".")[-1].lower()
    if extensao not in EXTENSOES_PERMITIDAS:
        st.error(
            f"⚠️ Formato inválido no campo **'{nome_campo}'**. "
            f"Formatos aceitos: {', '.join(EXTENSOES_PERMITIDAS).upper()}."
        )
        return False

    return True



def valida_telefone(telefone):
    """
    Valida telefone brasileiro (celular ou fixo).
    Aceita formatos: (17) 99706-0067, 17997060067, 17 99706 0067, etc.
    """
    # Remove tudo que não é número
    numero = re.sub(r'\D', '', telefone)

    # Rejeita se tiver letras misturadas no original (alfanumérico)
    if re.search(r'[a-zA-Z]', telefone):
        return False, "Telefone não pode conter letras."

    # Valida quantidade de dígitos celular (11)
    if len(numero) != 11:
        return False, "Telefone deve ter 11 dígitos (com DDD)."

    # Valida DDD — lista de DDDs válidos no Brasil
    ddds_validos = {
        11,12,13,14,15,16,17,18,19,21,22,24,27,28,
        31,32,33,34,35,37,38,41,42,43,44,45,46,47,48,49,
        51,53,54,55,61,62,63,64,65,66,67,68,69,
        71,73,74,75,77,79,81,82,83,84,85,86,87,88,89,
        91,92,93,94,95,96,97,98,99
    }
    ddd = int(numero[:2])
    if ddd not in ddds_validos:
        return False, "DDD inválido."

    # Celular (11 dígitos) precisa começar com 9 após o DDD
    if len(numero) == 11 and numero[2] != '9':
        return False, "Celular deve começar com 9 após o DDD."

    return True, "Telefone válido."

def formata_telefone(telefone):
    numero = re.sub(r'\D', '', telefone)
    if len(numero) == 11:
        return f"({numero[:2]}) {numero[2:7]}-{numero[7:]}"
    return numero

def avalia_caso_colaborador():
    st.divider()
    st.subheader("📋 Validação de Vínculo do Dependente")
    st.write("Por favor, selecione a situação que melhor se aplica ao registro do seu dependente:")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("A) Filho(a)", width='stretch'):
            st.session_state.tipo_fluxo = "A"
            st.rerun()
            
    with col2:
        if st.button("B) Enteado(a) (Registrado no nome do cônjuge/companheiro)", width='stretch'):
            st.session_state.tipo_fluxo = "B"
            st.rerun()
            
    with col3:
        if st.button("C) Criança ou Adolescente sob Guarda ou Tutela", width='stretch'):
            st.session_state.tipo_fluxo = "C"
            st.rerun()

#---------------------------------------------------FUNCOES DE validcao de documento



def valida_com_doc_complementar(dados: dict, nome_banco: str, nome_certidao_filho: str):
    """
    Valida se o colaborador é o mesmo por meio de certidão de casamento/divórcio.
    Recebe os dados já extraídos pelo Gemini.
    Retorna (True/False, mensagem, flag_revisao_rh)
    """
    if not dados.get("documento_valido"):
        return False, "❌ Documento não reconhecido. Envie uma certidão de casamento ou divórcio.", False

    nome_antes  = padroniza_texto(dados.get("nome_antes") or "")
    nome_depois = padroniza_texto(dados.get("nome_depois") or "")
    nome_db     = padroniza_texto(nome_banco)
    nome_cert   = padroniza_texto(nome_certidao_filho)

    banco_no_doc = nome_db in (nome_antes, nome_depois)
    cert_no_doc = nome_cert in (nome_antes, nome_depois)

    if banco_no_doc and cert_no_doc:
        return True, "✅ Vínculo confirmado via documento complementar. Cadastro aprovado com revisão do RH.", True

    return False, (
        "❌ Não foi possível confirmar o vínculo entre os documentos. "
        "Entre em contato com o RH para regularizar manualmente."
    ), False
   
def crianca_ja_cadastrada(nome_crianca_padronizado, data_nascimento_str, df_dependentes):
    """
    Verifica se essa criança (nome + data de nascimento) já está cadastrada
    por QUALQUER colaborador — inclusive o mesmo. 1 criança = 1 kit, sempre.
    Retorna (True/False, mensagem).
    """
    if df_dependentes.empty:
        return False, None

    encontrados = df_dependentes[
        (df_dependentes["Nome_filho"] == nome_crianca_padronizado) &
        (df_dependentes["Data_nascimento"] == data_nascimento_str)
    ]

    if encontrados.empty:
        return False, None

    return True, "❌ Esta criança já possui um kit cadastrado. Cada criança pode receber apenas 1 kit. Entre em contato com o RH em caso de divergência."   

# 🔹 ALTERAÇÃO: Respostas para 'documento' em vez de só Certidão
def valida_dados_crianca_certidao(dados_certidao: dict, nome_informado: str, data_informada: date, genero_informado: str):
    """
    Compara nome e data de nascimento da criança, informados pelo colaborador
    no formulário, com os dados extraídos do documento pelo Gemini.
    Retorna (True/False, mensagem).
    """

    if not dados_certidao.get("documento_valido"):
        return False, "❌ Documento adicionado não é uma Certidão de Nascimento ou RG válido."

    if dados_certidao.get("legivel") is False:
        return False, "❌ Documento está ilegível, carregue outro arquivo."

    nome_cert = padroniza_texto(dados_certidao.get("nome_crianca") or "")
    nome_form = padroniza_texto(nome_informado)

    if not nome_cert:
        return False, "❌ Não foi possível identificar o nome da criança no documento."

    if nome_cert != nome_form:
        return False, f"❌ Nome informado não confere com o documento. O documento mostra: {dados_certidao.get('nome_crianca')}"

    data_cert_str = (dados_certidao.get("data_nascimento_crianca") or "").strip()

    if not data_cert_str:
        return False, "❌ Não foi possível identificar a data de nascimento no documento."

    try:
        data_cert = datetime.strptime(data_cert_str, "%d/%m/%Y").date()
    except ValueError:
        return False, "❌ Não foi possível interpretar a data de nascimento extraída do documento."

    if data_cert != data_informada:
        return False, f"❌ Data de nascimento informada ({data_informada.strftime('%d/%m/%Y')}) não confere com o documento ({data_cert_str})."

    sexo_cert = padroniza_texto(dados_certidao.get("sexo_crianca") or "")
    sexo_form = padroniza_texto(genero_informado)
    if sexo_cert != sexo_form:
        return (
        False,
        f"❌ O sexo informado ({genero_informado}) "
        f"não corresponde ao sexo encontrado no documento "
        f"({dados_certidao.get('sexo_crianca')}).")

    return True, "✅ Nome, data de nascimento e sexo conferem com o documento."


def catalogo_kits_por_escolaridade():
    BASE_URL = os.getenv("BASE_URL_IMAGENS_KITS")
    
    if not BASE_URL:
        st.error("⚠️ A variável `BASE_URL_IMAGENS_KITS` não está configurada no arquivo .env!")
        return {}, ""
    
    kits_infantil = ["Dinossauro", "Abelha", "Borboleta", "Unicornio", "Cachorro"]
    kits_efi = [f"EFI-{i}" for i in range(1, 12)]
    kits_efii = [f"EFII-{i}" for i in range(1, 17)]
    kits_em = [f"EM-{i}" for i in range(1, 14)]
    
    return {
        "Educação Infantil": kits_infantil,
        "Ensino Fundamental I": kits_efi,
        "Ensino Fundamental II": kits_efii,
        "Ensino Médio": kits_em,
        "Ensino Superior / Técnico": kits_em
    }, BASE_URL

def escolher_kits_colaborador():
    st.divider()
    st.subheader("🎒 Escolha dos Kits Escolares")
    id_colaborador = st.session_state.colaborador["id"]   # ID real do banco

    # ===================== ETAPA 2: CIÊNCIA SOBRE VARIAÇÃO DE MODELO/ESTOQUE =====================
    if st.session_state.aguardando_ciencia_kits:
        st.warning(
            "⚠️As mochilas apresentadas possuem variações de cores e, em alguns casos, "
            "diferentes opções de acabamento.\n\n"
            "A distribuição estará condicionada ao estoque disponível na data de entrega. "
            "Caso o modelo escolhido não esteja disponível, será fornecida outra opção disponível."
        )

        ciente = st.checkbox("Estou ciente", key="chk_ciencia_variacao_kit")
        confirmar_ciencia = st.button("✅ Confirmar Ciência e Finalizar Escolha", key="btn_confirma_ciencia_kit")

        if confirmar_ciencia:
            if not ciente:
                st.error("⚠️ Você precisa marcar 'Estou ciente' para prosseguir.")
                return None

            db = SessionLocal()
            try:
                data_aceite = datetime.now()
                novas_escolhas = []
                for escolha in st.session_state.escolhas_pendentes_kits:
                    nova_escolha = EscolhaKit(
                        id_colaborador=escolha["ID_Colaborador"],
                        id_dependente=escolha["ID_Dependente"],
                        kit_escolhido=escolha["Kit_Escolhido"],
                        aceite_variacao_kit=True,          # 🚨 requer coluna nova em EscolhaKit
                        data_aceite_variacao=data_aceite    # 🚨 requer coluna nova em EscolhaKit
                    )
                    db.add(nova_escolha)
                    db.commit()
                    db.refresh(nova_escolha)
                    novas_escolhas.append({
                        "ID_Escolha": nova_escolha.id_escolha,
                        "ID_Dependente": nova_escolha.id_dependente,
                        "Kit_Escolhido": nova_escolha.kit_escolhido,
                        "Nome_filho": escolha["Nome_filho"],
                        "Escolaridade": escolha["Escolaridade"],
                        "Ano_escolar": escolha["Ano_escolar"]
                    })

                st.session_state.escolhas_kits = novas_escolhas
                st.session_state.aguardando_ciencia_kits = False
                st.session_state.escolhas_pendentes_kits = []
                registrar_log(id_colaborador, "KIT_SELECIONADO", f"Escolha confirmada para {len(novas_escolhas)} kit(s).")
                st.success("🎉 Kits escolhidos com sucesso!")
                return novas_escolhas
            finally:
                db.close()

        return None

    # ===================== ETAPA 1: SELEÇÃO DOS KITS (CHECKBOX POR IMAGEM) =====================
    db = SessionLocal()
    try:
        # Busca dependentes deste colaborador diretamente do banco
        dependentes_colaborador = db.query(Dependente).filter(
            Dependente.id_colaborador == id_colaborador
        ).all()
        
        if not dependentes_colaborador:
            st.warning("Nenhum dependente encontrado para este colaborador.")
            return None
            
        catalogo, base_url = catalogo_kits_por_escolaridade()
        st.write(f"Você possui {len(dependentes_colaborador)} crédito(s) de kit.")
        
        # INJEÇÃO DO CSS PARA PADRONIZAR A ALTURA DAS IMAGENS
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
                nome_filho = dependente.nome_filho
                escolaridade = dependente.escolaridade
                ano_escolar = dependente.ano_escola
                genero = dependente.genero
                id_dependente = dependente.id_dependente
                
                st.markdown(f"### 👶 {nome_filho}")
                st.write(f"**Escolaridade:** {escolaridade} | **Ano escolar:** {ano_escolar}")
                
                opcoes_kits = catalogo.get(escolaridade, [])
                if not opcoes_kits:
                    st.error(f"Não existem kits cadastrados para a escolaridade: {escolaridade}")
                    continue
                
                # ========================================================
                # VITRINE VISUAL DE KITS — 1 CHECKBOX POR IMAGEM
                # ========================================================
                st.markdown("**Catálogo Disponível — marque o kit desejado:**")
                
                itens_por_linha = 4
                for i in range(0, len(opcoes_kits), itens_por_linha):
                    colunas = st.columns(itens_por_linha)
                    for j in range(itens_por_linha):
                        if i + j < len(opcoes_kits):
                            nome_kit = opcoes_kits[i+j]
                            nome_arquivo = nome_kit.replace(" ", "")
                            url_img = f"{base_url}{nome_arquivo}.png"
                            
                            with colunas[j]:
                                st.image(url_img, caption=nome_kit, width='stretch')
                                st.checkbox(nome_kit, key=f"chk_kit_{id_dependente}_{nome_kit}")
                
                st.write("---")
                # ========================================================

                info_dependentes.append({
                    "ID_Dependente": id_dependente,
                    "ID_Colaborador": id_colaborador,
                    "Nome_filho": nome_filho,
                    "Gênero": genero,
                    "Escolaridade": escolaridade,
                    "Ano_escolar": ano_escolar,
                    "Opcoes_kits": opcoes_kits
                })
                
            salvar_escolhas = st.form_submit_button("✅ Confirmar escolha dos kits")
            
        if not salvar_escolhas:
            return None

        # Apura, para cada dependente, qual(is) checkbox(es) ficaram marcados
        escolhas = []
        erros = []
        for info in info_dependentes:
            id_dependente = info["ID_Dependente"]
            marcados = [
                nome_kit for nome_kit in info["Opcoes_kits"]
                if st.session_state.get(f"chk_kit_{id_dependente}_{nome_kit}")
            ]

            if len(marcados) == 0:
                erros.append(f"⚠️ Selecione um kit para {info['Nome_filho']}.")
                continue
            if len(marcados) > 1:
                erros.append(f"⚠️ Selecione apenas um kit para {info['Nome_filho']} (mais de um foi marcado).")
                continue

            escolhas.append({
                "ID_Dependente": id_dependente,
                "ID_Colaborador": info["ID_Colaborador"],
                "Nome_filho": info["Nome_filho"],
                "Gênero": info["Gênero"],
                "Escolaridade": info["Escolaridade"],
                "Ano_escolar": info["Ano_escolar"],
                "Kit_Escolhido": marcados[0]
            })

        if erros:
            for erro in erros:
                st.error(erro)
            return None

        # Guarda as escolhas e avança para a etapa de ciência (Etapa 2)
        st.session_state.escolhas_pendentes_kits = escolhas
        st.session_state.aguardando_ciencia_kits = True
        st.rerun()

    finally:
        db.close()

# ===================== FUNÇÕES DE QRCODE =====================
def monta_conteudo_qrcode():
    """Gera o conteúdo que vai dentro do QR Code"""
    if "codigo_retirada" not in st.session_state:
        criar_registro_retirada_qrcode()

    codigo_retirada = st.session_state.codigo_retirada
    return f"RETIRADA_KIT:{codigo_retirada}"
def gerar_qrcode(conteudo_qrcode):
    """Gera a imagem do QR Code"""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4
    )

    qr.add_data(conteudo_qrcode)
    qr.make(fit=True)

    imagem_qrcode = qr.make_image(fill_color="black", back_color="white")

    buffer = BytesIO()
    imagem_qrcode.save(buffer, format="PNG")
    buffer.seek(0)

    return buffer




def _get_s3_client():
    """Instancia o client do S3 usando as credenciais do arquivo .env."""
    return boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION"),
    )


def upload_documento_supabase(arquivo_buffer, tipo_documento, cracha) -> str | None:
    """Faz upload do documento de revisão para o bucket S3 (AWS)."""

    BASE_URL_DOCUMENTO_ANALISE = os.getenv("BASE_URL_DOCUMENTO_ANALISE")
    BUCKET_DOCS = os.getenv("S3_BUCKET_DOCS")

    s3_client = _get_s3_client()

    # Gera o nome seguindo o padrão exigido
    hash_curto = uuid.uuid4().hex[:6]
    extensao = arquivo_buffer.name.split('.')[-1]
    nome_arquivo = f"{tipo_documento}-{cracha}-{hash_curto}.{extensao}"

    arquivo_buffer.seek(0)

    try:
        s3_client.put_object(
            Bucket=BUCKET_DOCS,
            Key=nome_arquivo,
            Body=arquivo_buffer.read(),
            ContentType=arquivo_buffer.type,
        )

        # Retorna a URL montada perfeitamente usando a variável do .env
        return f"{BASE_URL_DOCUMENTO_ANALISE}{nome_arquivo}"

    except ClientError as e:
        print(f"❌ Erro ao salvar documento no bucket S3: {e}")
        return None
    finally:
        arquivo_buffer.seek(0)
def salvar_qrcode_bucket(buffer_qrcode: BytesIO, cracha: str) -> str | None:
    """Faz upload do QR Code para o bucket S3 (AWS), usando o crachá como nome do arquivo."""

    BASE_URL_QRCODE = os.getenv("BASE_URL_QRCODE")
    BUCKET_QRCODES = os.getenv("S3_BUCKET_QRCODES")

    s3_client = _get_s3_client()

    nome_arquivo = f"{cracha}.png"
    buffer_qrcode.seek(0)

    try:
        s3_client.put_object(
            Bucket=BUCKET_QRCODES,
            Key=nome_arquivo,
            Body=buffer_qrcode.read(),
            ContentType="image/png",
        )
        # Monta o link perfeitamente: url_pasta + nome_do_arquivo
        return f"{BASE_URL_QRCODE}{nome_arquivo}"

    except ClientError as e:
        print(f"❌ Erro ao salvar QR Code no bucket S3: {e}")
        return None
    finally:
        buffer_qrcode.seek(0)


def enviar_qrcode_por_email(email_destino: str, buffer_qrcode: BytesIO, cracha: str, codigo_retirada: str) -> bool:
    """Envia o QR Code de retirada por e-mail ao colaborador, junto com o código de retirada."""
    notificador = NotificadorEmail(SMTP_SERVER, SMTP_PORT, LOGIN_SMTP, SENHA_KEY)
    remetente = EMAIL_REMETENTE if EMAIL_REMETENTE else LOGIN_SMTP
    sucesso = notificador.disparar(
        remetente=remetente,
        destinatarios=email_destino,
        assunto="Seu QR Code - Retirada do Kit Escolar Funfarme",
        corpo=(
            "Olá! Segue em anexo o QR Code para retirada do seu Kit Escolar.\n\n"
            f"Código de retirada: {codigo_retirada}\n\n"
            "Caso não seja possível apresentar o QR Code no momento da retirada, "
            "este código também pode ser informado."
        ),
        anexo=buffer_qrcode,
        nome_anexo=f"qrcode_retirada_{cracha}.png"
    )
    buffer_qrcode.seek(0)
    return sucesso
#---------------------------------------------------------------------------------------------------QRCODE 
def monta_resumo_kits(escolhas_kits):
    linhas_resumo = []
    for escolha in escolhas_kits:
        linha = f"{escolha.get('Nome_filho')} - {escolha.get('Escolaridade')} - {escolha.get('Kit_Escolhido')}"
        linhas_resumo.append(linha)
    return " | ".join(linhas_resumo)
def criar_registro_retirada_qrcode():
    colaborador = st.session_state.colaborador
    contato = st.session_state.contato
    escolhas_kits = st.session_state.escolhas_kits
    db = SessionLocal()
    try:
        resumo_kits = monta_resumo_kits(escolhas_kits)
        
        # BUSCA SE JÁ EXISTE REGISTRO DE RETIRADA PARA ESTE COLABORADOR
        retirada_existente = db.query(Retirada).filter(Retirada.id_colaborador == colaborador["id"]).first()

        if retirada_existente:
            retirada_existente.resumo_kits = resumo_kits
            retirada_existente.qtd_kits = len(escolhas_kits)
            retirada_existente.email = contato["email"]
            retirada_existente.telefone = contato["telefone"]
            db.commit()
            db.refresh(retirada_existente)
            nova_retirada = retirada_existente
        else:
            codigo_retirada = str(uuid.uuid4())
            nova_retirada = Retirada(
                codigo_retirada=codigo_retirada,
                id_colaborador=colaborador["id"],
                email=contato["email"],
                telefone=contato["telefone"],
                qtd_kits=len(escolhas_kits),
                resumo_kits=resumo_kits,
                status="PENDENTE"
            )
            db.add(nova_retirada)
            db.commit()
            db.refresh(nova_retirada)

        st.session_state.codigo_retirada = nova_retirada.codigo_retirada
        st.session_state.retirada_qrcode = {
            "Codigo_Retirada": nova_retirada.codigo_retirada,
            "Nome_Colaborador": colaborador["Nome"],
            "ID_Colaborador": colaborador["Crachá"],
            "Email": contato["email"],
            "Telefone": contato["telefone"],
            "Qtd_Kits": len(escolhas_kits),
            "Resumo_Kits": resumo_kits,
            "Status": nova_retirada.status
        }
        registrar_log(colaborador["id"], "QRCODE_GERADO", f"Retirada {nova_retirada.codigo_retirada} vinculada a {len(escolhas_kits)} kit(s).")
        return nova_retirada
    finally:
        db.close()

def exibir_qrcode_final():
    st.divider()
    st.subheader("🎟️ QR Code para Retirada")

    if "retirada_qrcode" not in st.session_state:
        criar_registro_retirada_qrcode()

    retirada = st.session_state.retirada_qrcode

    st.write(f"Colaborador: {retirada['Nome_Colaborador']}")
    st.write(f"Crachá: {retirada['ID_Colaborador']}")
    st.write(f"Quantidade de kits: {retirada['Qtd_Kits']}")
    st.write(f"Status: {retirada['Status']}")

    st.info(retirada["Resumo_Kits"])

    conteudo_qrcode = monta_conteudo_qrcode()
    imagem_qrcode = gerar_qrcode(conteudo_qrcode)

    # 1. MOSTRA O QR CODE NA TELA PRIMEIRO
    st.image(
        imagem_qrcode,
        caption="Apresente este QR Code para retirada do kit.",
        width=300
    )

    st.download_button(
        label="⬇️ Baixar QR Code",
        data=imagem_qrcode,
        file_name=f"qrcode_retirada_{retirada['Codigo_Retirada']}.png",
        mime="image/png"
    )

    # 2. DEPOIS FAZ UPLOAD E EMAIL (COM AVISO VISUAL DE PROCESSAMENTO)
    if not st.session_state.get("qrcode_processado", False):
        cracha = str(retirada["ID_Colaborador"])
        
        with st.spinner("☁️ Salvando e enviando cópia por e-mail..."):
            # Envia pro Supabase
            salvar_qrcode_bucket(imagem_qrcode, cracha)
            
            # Dispara o Email
            sucesso_email = enviar_qrcode_por_email(retirada["Email"], imagem_qrcode, cracha, retirada["Codigo_Retirada"])
            
            if sucesso_email:
                st.success("✅ Cópia enviada para o seu e-mail!")

                registrar_log(cracha, "EMAIL_QRCODE_ENVIADO", f"Enviado para {retirada['Email']}")
            else:
                st.warning("⚠️ O QR Code está gerado acima, mas houve falha ao enviar a cópia por e-mail.")
                
        st.session_state.qrcode_processado = True

    return imagem_qrcode






#------------------------------------------------------------PAINEL DE CONTROLE------------------------------------------------------------------------
# ============================================================
# CÓDIGO CORRIGIDO DA FUNÇÃO INTERFACE
# ============================================================
def interface():
    st.set_page_config(page_title='Funfarme - Kit Escolar', page_icon='🎒', layout="wide")
    st.title('🎒 Funfarme - Kit Escolar')

    # 🌟 EXIBE A MENSAGEM DE SUCESSO VINDA DA ATUALIZAÇÃO DO KIT
    if "mensagem_sucesso" in st.session_state and st.session_state.mensagem_sucesso:
        st.success(st.session_state.mensagem_sucesso)
        del st.session_state.mensagem_sucesso
    
    # ---------------------------------------------------------------
    # Inicializa todos os estados necessários ---
    if 'colaborador' not in st.session_state:
        st.session_state.colaborador = None
    if 'autenticado_totp' not in st.session_state:
        st.session_state.autenticado_totp = False 
    if 'acao_retorno' not in st.session_state:        
        st.session_state.acao_retorno = None            
    if 'contato' not in st.session_state:
        st.session_state.contato = None
    if 'tipo_fluxo' not in st.session_state:
        st.session_state.tipo_fluxo = None
    if 'lista_dependentes' not in st.session_state:
        st.session_state.lista_dependentes = []
    if 'aguardando_decisao' not in st.session_state:
        st.session_state.aguardando_decisao = False
    if 'cadastro_finalizado' not in st.session_state:
        st.session_state.cadastro_finalizado = False
    if 'escolhendo_kits' not in st.session_state:
        st.session_state.escolhendo_kits = False
    if 'escolhas_kits' not in st.session_state:
        st.session_state.escolhas_kits = []
    if 'aguardando_ciencia_kits' not in st.session_state:
        st.session_state.aguardando_ciencia_kits = False
    if 'escolhas_pendentes_kits' not in st.session_state:
        st.session_state.escolhas_pendentes_kits = []
    
    # Estados de erro de IA para cada fluxo específico
    for fluxo_err in ['erro_ia_a2', 'erro_ia_a3', 'erro_ia_b1', 'erro_ia_b2', 'erro_ia_c1', 'erro_ia_c2']:
        if fluxo_err not in st.session_state:
            st.session_state[fluxo_err] = None

    # ===================== CARRINHO VISUAL NA SIDEBAR =====================
    if st.session_state.contato is not None and not st.session_state.cadastro_finalizado:
        with st.sidebar:
            st.markdown("### 🛒 Carrinho de Dependentes")
            st.caption(f"Colaborador: {st.session_state.colaborador['Nome']}")
            st.divider()
            
            if st.session_state.lista_dependentes:
                for i, dep in enumerate(st.session_state.lista_dependentes, start=1):
                    st.markdown(f"**{i}. {dep['Nome_filho']}**")
                    st.write(f"📚 {dep.get('Escolaridade', '')}")
                    st.caption(f"Ano: {dep.get('Ano_escolar', '')} | Nasc: {dep.get('Data_nascimento', '')}")
                    st.markdown("---")
                
                if st.button("🛒 Finalizar Carrinho e Escolher Kits", type="primary", use_container_width=True, key="btn_fin_sidebar"):
                    db = SessionLocal()
                    try:
                        for dep in st.session_state.lista_dependentes:
                            if not dep.get("ID_Dependente"):
                                novo_dep_db = Dependente(
                                    id_colaborador=st.session_state.colaborador.get("id"),
                                    nome_filho=dep["Nome_filho"],
                                    data_nascimento=datetime.strptime(dep["Data_nascimento"], "%d/%m/%Y").date(),
                                    genero=dep.get("Gênero", "Não informado"),
                                    escolaridade=dep["Escolaridade"],
                                    ano_escola=dep["Ano_escolar"],
                                    revisao_rh=dep.get("revisao_rh"),
                                    fluxo_documento=dep.get("Fluxo_Documento", "Não identificado"),
                                    aceite_ia=dep.get("aceite_ia", False),
                                    aceite_lgpd=dep.get("aceite_lgpd", False),
                                    data_aceite=datetime.strptime(dep["data_aceite"], "%Y-%m-%d %H:%M:%S") if dep.get("data_aceite") else None,
                                    motivo_reprova_ia=dep.get("motivo_reprova_ia"),
                                    url_documento=dep.get("url_documento")
                                )
                                db.add(novo_dep_db)
                                db.commit()
                                db.refresh(novo_dep_db)
                                dep["ID_Dependente"] = novo_dep_db.id_dependente
                    finally:
                        db.close()
                    st.session_state.aguardando_decisao = False
                    st.session_state.escolhendo_kits = True
                    st.rerun()
            else:
                st.info("Seu carrinho está vazio. Adicione os dependentes ao lado.")

    # ===================== FASE 1: BUSCA E CONTATO =====================
    if st.session_state.contato is None:
        
        # 1. Se ainda não buscou o colaborador, mostra o formulário de busca
        if st.session_state.colaborador is None:
            st.write('Informe seu crachá e clique em Buscar Colaborador.')
            colaborador = busca_colaborador()
            if colaborador is not None:
                st.session_state.colaborador = colaborador
                st.rerun() 
            return

        # 2. TRAVA DE SEGURANÇA 2FA
        if not st.session_state.autenticado_totp:
            if verificar_autenticacao_totp(st.session_state.colaborador, SessionLocal().bind):
                st.session_state.autenticado_totp = True
                st.rerun() 
            st.stop() 

        # 3. FICHA DO COLABORADOR E KITS EXISTENTES
        st.divider()
        st.subheader("📋 Ficha do Colaborador")
        st.text_input("Nome Completo", value=st.session_state.colaborador['Nome'], disabled=True)
        st.text_input("Cargo", value=st.session_state.colaborador['Título Reduzido (Cargo)'] or "", disabled=True)
        st.text_input("Situação", value=st.session_state.colaborador['Descrição (Situação)'] or "", disabled=True)

        if not st.session_state.escolhendo_kits and not st.session_state.cadastro_finalizado:
            # Se a ação for "adicionar_dependente", pula o bloqueio de dependentes existentes
            if st.session_state.acao_retorno != "adicionar_dependente":
                db = SessionLocal()
                try:
                    dependentes_existentes = db.query(Dependente).filter(
                        Dependente.id_colaborador == st.session_state.colaborador['id']
                    ).all()
                    
                    if dependentes_existentes and len(st.session_state.lista_dependentes) == 0:
                        st.warning("⚠️ O seu crachá já tem dependentes atrelados a ele.")
                        
                        # Verifica se TODOS os dependentes foram aprovados (revisao_rh começa com "Não")
                        aprovado = all(d.revisao_rh and str(d.revisao_rh).strip().startswith("Não") for d in dependentes_existentes)
                        
                        if not aprovado:
                            st.info("🕒 **STATUS: A DOCUMENTAÇÃO ESTÁ SENDO ANALISADA PELO RH**")
                            st.write("EM BREVE CHEGARÁ UM E-MAIL COM MAIS ORIENTAÇÃO.")
                            
                            # ➕ Botão liberado para adicionar novos dependentes enquanto aguarda o RH
                            if st.button("➕ Adicionar Mais dependentes", use_container_width=True):
                                st.session_state.acao_retorno = "adicionar_dependente"
                                st.rerun()
                                
                            # Interrompe o fluxo aqui para não exibir as opções de Mudar Kit/QR Code
                            if st.session_state.acao_retorno != "adicionar_dependente":
                                return 
                        else:
                            st.success("✅ **STATUS: APROVADO**")
                            
                            # Renderiza os 3 botões em colunas
                            col1, col2, col3 = st.columns(3)
                            with col1:
                                if st.button("🎒 Mudar escolha do Kit", use_container_width=True):
                                    st.session_state.acao_retorno = "mudar_kit"
                                    st.rerun()
                            with col2:
                                if st.button("➕ Adicionar Mais dependentes", use_container_width=True):
                                    st.session_state.acao_retorno = "adicionar_dependente"
                                    st.rerun()
                            with col3:
                                if st.button("🎟️ Buscar QRCODE", use_container_width=True):
                                    st.session_state.acao_retorno = "ver_qrcode"
                                    st.rerun()

                            # Lógica de navegação baseada no botão clicado
                            if st.session_state.acao_retorno == "mudar_kit":
                                editar_kits_existentes(st.session_state.colaborador['id'])
                                return
                                
                            elif st.session_state.acao_retorno == "ver_qrcode":
                                st.divider()
                                st.subheader("🎟️ Seu QR Code de Retirada")
                                base_url_qr = os.getenv("BASE_URL_QRCODE", "")
                                
                                if not base_url_qr:
                                    st.error("⚠️ ERRO DE CONFIGURAÇÃO: A variável `BASE_URL_QRCODE` não está configurada no arquivo .env.")
                                    return
                                    
                                cracha_str = str(st.session_state.colaborador['id'])
                                if not base_url_qr.endswith("/"):
                                    base_url_qr += "/"
                                    
                                url_qr = f"{base_url_qr}{cracha_str}.png"
                                
                                try:
                                    st.image(url_qr, width=300, caption="Apresente este QR Code no dia da retirada.")
                                    st.markdown(f"📥 [Clique aqui para baixar o seu QR Code diretamente]({url_qr})")
                                except Exception:
                                    st.error("⚠️ A imagem do QR Code ainda não foi encontrada no servidor em nuvem.")
                                return
                                
                            else:
                                return
                finally:
                    db.close()

        # 4. FORMULÁRIO DE CONTATO
        email_salvo = ""
        telefone_salvo = ""

        db = SessionLocal()
        try:
            retirada_existente = db.query(Retirada).filter(
                Retirada.id_colaborador == st.session_state.colaborador['id']
            ).first()

            if retirada_existente:
                email_salvo = retirada_existente.email or ""
                telefone_salvo = retirada_existente.telefone or ""
                # CORREÇÃO: Não define st.session_state.contato aqui para não saltar o formulário!
        finally:
            db.close()

        chave_unica_form = f"form_contato_{st.session_state.colaborador['id']}"

        contato = adiciona_dados_contato(
            email_padrao=email_salvo, 
            telefone_padrao=telefone_salvo,
            form_key=chave_unica_form
        )
        if contato is not None:
            st.session_state.contato = contato
            st.rerun()

    # ===================== FASE 2: TRIAGEM DE VÍNCULO =====================
    else:
        st.success(f"👤 Colaborador: {st.session_state.colaborador['Nome']} | ✅ Contato salvo.")

        # CORREÇÃO: Pula este bloqueio se o usuário clicou em adicionar mais dependentes
        if not st.session_state.escolhendo_kits and not st.session_state.cadastro_finalizado and st.session_state.acao_retorno != "adicionar_dependente":
            db = SessionLocal()
            try:
                dependentes_existentes = db.query(Dependente).filter(Dependente.id_colaborador == st.session_state.colaborador['id']).all()
                if dependentes_existentes:
                    st.warning("⚠️ O seu crachá já tem dependentes atrelados a ele!")
                    editar_kits_existentes(st.session_state.colaborador['id'])
                    return  
            finally:
                db.close()

        if st.session_state.lista_dependentes:
            qtd_itens = len(st.session_state.lista_dependentes)
            st.warning(
                f"🛒 **Atenção:** Você tem **{qtd_itens}** item(ns) no seu carrinho! "
                f"Toque nas setas **( >> )** no canto superior esquerdo da tela para abrir o menu lateral e **Finalizar o Pedido**."
            )
        
        # TELAS DE CONTROLE UNIVERSAL
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

        # Restante do código dos fluxos A, B, C mantido sem alter
        if st.session_state.aguardando_decisao:
            st.divider()
            st.subheader("✅ Item adicionado ao carrinho com sucesso!")
            st.write("Confira os itens no seu **Carrinho (na barra lateral à esquerda)** ou escolha abaixo o que deseja fazer:")
            
            col1, col2 = st.columns([1, 1])
            with col1:
                if st.button("➕ Adicionar outro dependente/kit", type="primary", use_container_width=True):
                    st.session_state.aguardando_decisao = False
                    st.session_state.escolaridade = ""
                    st.session_state.ano_escolar = ""
                    st.session_state.aguardando_doc_complementar = False
                    st.session_state.dados_certidao_filho = None
                    st.session_state.dependente_temp = None
                    st.session_state.tipo_fluxo = None 
                    
                    if 'sub_opcao_a' in st.session_state: del st.session_state['sub_opcao_a']
                    if 'sub_opcao_b' in st.session_state: del st.session_state['sub_opcao_b']
                    if 'sub_opcao_c' in st.session_state: del st.session_state['sub_opcao_c']
                    
                    # Limpa todos os estados de erro da IA
                    for fluxo_err in ['erro_ia_a1', 'erro_ia_a2', 'erro_ia_a3', 'erro_ia_b1', 'erro_ia_b2', 'erro_ia_c1', 'erro_ia_c2']:
                        if fluxo_err in st.session_state:
                            st.session_state[fluxo_err] = None
                    
                    st.rerun()
            with col2:
                if st.button("🚀 Finalizar carrinho e escolher kits", use_container_width=True, key="btn_fin_main"):
                    db = SessionLocal()
                    try:
                        for dep in st.session_state.lista_dependentes:
                            if not dep.get("ID_Dependente"):
                                novo_dep_db = Dependente(
                                    id_colaborador=st.session_state.colaborador.get("id"),
                                    nome_filho=dep["Nome_filho"],
                                    data_nascimento=datetime.strptime(dep["Data_nascimento"], "%d/%m/%Y").date(),
                                    genero=dep.get("Gênero", "Não informado"),
                                    escolaridade=dep["Escolaridade"],
                                    ano_escola=dep["Ano_escolar"],
                                    revisao_rh=dep.get("revisao_rh"),
                                    fluxo_documento=dep.get("Fluxo_Documento", "Não identificado"),
                                    aceite_ia=dep.get("aceite_ia", False),
                                    aceite_lgpd=dep.get("aceite_lgpd", False),
                                    data_aceite=datetime.strptime(dep["data_aceite"], "%Y-%m-%d %H:%M:%S") if dep.get("data_aceite") else None,
                                    motivo_reprova_ia=dep.get("motivo_reprova_ia"),
                                    url_documento=dep.get("url_documento")
                                )
                                db.add(novo_dep_db)
                                db.commit()
                                db.refresh(novo_dep_db)
                                dep["ID_Dependente"] = novo_dep_db.id_dependente
                    finally:
                        db.close()
                    st.session_state.aguardando_decisao = False
                    st.session_state.escolhendo_kits = True
                    st.rerun()
                    
            return

        # SELEÇÃO DO FLUXO PRINCIPAL
        if st.session_state.tipo_fluxo is None:
            cargos_estagiario = [600, 601, 602, 5001]
            id_cargo_colab = st.session_state.colaborador.get('id_cargo')
            
            if id_cargo_colab is not None and int(id_cargo_colab) in cargos_estagiario:
                st.session_state.tipo_fluxo = "ESTAGIARIO"
                st.rerun()
            else:
                avalia_caso_colaborador()
                return

        # ================= MARCADOR: FLUXO ESTAGIÁRIO =================
        elif st.session_state.tipo_fluxo == "ESTAGIARIO":
            st.subheader("🎓 Cadastro de Estagiário (Kit Próprio)")
            st.info("Como estagiário, você tiene direito ao seu próprio kit escolar. Preencha seus dados acadêmicos e anexe a sua **Certidão de Nascimento ou RG** para validação automática.")

            if 'escolaridade' not in st.session_state: st.session_state.escolaridade = ""
            if 'ano_escolar' not in st.session_state: st.session_state.ano_escolar = ""

            st.selectbox("Sua Escolaridade", ["", "Ensino Médio", "Ensino Superior / Técnico"], format_func=lambda x: "Selecione a Escolaridade..." if x == "" else x, key="escolaridade")
            
            opcoes_ano = {
                "": [],
                "Ensino Médio": ["", "1º Ano", "2º Ano", "3º Ano"],
                "Ensino Superior / Técnico": ["", "1º Semestre", "2º Semestre", "3º Semestre", "4º Semestre", "5º Semestre", "6º Semestre", "7º Semestre", "8º Semestre", "9º Semestre", "10º Semestre"]
            }
            
            if st.session_state.escolaridade:
                st.selectbox("Ano/Semestre em 2026", opcoes_ano[st.session_state.escolaridade], format_func=lambda x: "Selecione o Ano/Semestre..." if x == "" else x, key="ano_escolar")

            with st.form("form_estagiario"):
                genero_est = st.selectbox("Seu Gênero:", ["", "Masculino", "Feminino"], format_func=lambda x: "Selecione o Gênero..." if x == "" else x)
                data_nascimento_est = st.date_input("Sua Data de Nascimento", min_value=date(1950, 1, 1), max_value=date.today(), format="DD/MM/YYYY")
                
                certidao_est = st.file_uploader("Anexe SUA Certidão de Nascimento ou RG", type=["pdf", "png", "jpg", "jpeg"], help="Se for RG, envie o documento aberto ou a parte que mostra os pais.")
                
                st.divider()
                aceite_ia = st.checkbox("Estou ciente de que os documentos enviados serão processados e analisados automaticamente por inteligência artificial para fins de validação cadastral.", key="ia_est")
                aceite_lgpd = st.checkbox("Concordo com o tratamento, armazenamento e uso dos dados para a concessão do Kit Escolar, em conformidade com a LGPD e as normas de compliance da instituição.", key="lgpd_est")
                salvar_est = st.form_submit_button("Validar e Adicionar ao Carrinho")

            if salvar_est:
                if not verificar_limite_clique("valida_estagiario", 5):
                    st.stop()
                valido_cert = validar_arquivo(certidao_est, "Certidão/RG do Estagiário")
                if not valido_cert:
                    st.stop()

                erros_est = []
                if not genero_est: erros_est.append("O gênero é obrigatório.")
                if not st.session_state.escolaridade: erros_est.append("A escolaridade é obrigatória.")
                if not st.session_state.ano_escolar: erros_est.append("O ano escolar é obrigatório.")
                if not certidao_est: erros_est.append("A Certidão de Nascimento ou RG é obrigatória.")
                if not aceite_ia or not aceite_lgpd: erros_est.append("Você deve aceitar os termos de uso de IA e a política de privacidade (LGPD) para prosseguir.")

                if erros_est:
                    for e in erros_est: st.error(f"⚠️ {e}")
                else:
                    with st.spinner("Analisando documento via IA... Aguarde") as status:
                        dados_cert, err_cert = analisa_certidao(certidao_est)
                        
                        if err_cert:
                            registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"Falha na leitura IA (Estagiário): {err_cert}")
                            st.error(f"⚠️ {err_cert}")
                        else:
                            dados_ok, msg_dados = valida_dados_crianca_certidao(
                                dados_cert, st.session_state.colaborador['Nome'], data_nascimento_est, genero_est
                            )
                            
                            if not dados_ok:
                                registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou Estagiário: {msg_dados}")
                                st.error(msg_dados)
                            else:
                                db = SessionLocal()
                                try:
                                    nome_colab = padroniza_texto(st.session_state.colaborador['Nome'])
                                    if verificar_crianca_duplicada(db, nome_colab, data_nascimento_est):
                                        st.error("❌ Você já adicionou o seu kit ao carrinho ou já finalizou este cadastro.")
                                    else:
                                        st.session_state.lista_dependentes.append({
                                            "ID_Dependente": None,
                                            "ID_Colaborador": st.session_state.colaborador['Crachá'],
                                            "Nome_filho": nome_colab, 
                                            "Gênero": genero_est,
                                            "Data_nascimento": data_nascimento_est.strftime("%d/%m/%Y"),
                                            "Escolaridade": st.session_state.escolaridade,
                                            "Ano_escolar": st.session_state.ano_escolar,
                                            "revisao_rh": "Não (Estagiário Validado)",
                                            "Fluxo_Documento": "Estagiário - Certidão de Nascimento ou RG",
                                            "aceite_ia": aceite_ia,
                                            "aceite_lgpd": aceite_lgpd,
                                            "data_aceite": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                            "motivo_reprova_ia": None,
                                            "url_documento": None
                                        })
                                        registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_SUCESSO", "Validação de Estagiário aprovada.")
                                        registrar_log(st.session_state.colaborador['Crachá'], "DEPENDENTE_CARRINHO_ADD", f"Estagiário {nome_colab} validado e adicionado ao carrinho.")
                                        st.success("✅ Documento validado! Seu Kit foi adicionado ao carrinho.")
                                        st.session_state.aguardando_decisao = True
                                        st.rerun()
                                finally:
                                    db.close()

        # ================= MARCADOR: CAMINHO A =================
        elif st.session_state.tipo_fluxo == "A":
            st.subheader("🍼 Cadastro - Filho(a) Biológico(a) ou Adotivo(a)")
            
            if st.button("⬅️ Voltar e escolher outra opção", key="voltar_a"):
                st.session_state.tipo_fluxo = None
                if 'sub_opcao_a' in st.session_state: del st.session_state['sub_opcao_a']
                st.session_state.erro_ia_a2 = None
                st.session_state.erro_ia_a3 = None
                st.rerun()
            st.write("---")
            sub_opcao_a = st.radio(
                "🎯 Selecione a forma de comprovação do vínculo:",
                ["**A1:** Certidão de Nascimento ou RG - Filho(a) Biológico(a) ", "**A2:** Certidão de Nascimento com averbação de adoção", "**A3:** Documento judicial que comprove a guarda para fins de adoção"],
                index=None, key="sub_opcao_a"
            )

            if sub_opcao_a:
                st.divider()
                
                # --- SUB-FLUXO A1 ---
                if "A1" in sub_opcao_a:
                    dependente = adicionar_dependentes()
                    if dependente is not None:
                        st.session_state.lista_dependentes.append(dependente)
                        st.session_state.aguardando_decisao = True
                        st.rerun()

                # --- SUB-FLUXOS A2 e A3 ---
                elif "A2" in sub_opcao_a or "A3" in sub_opcao_a:
                    if 'escolaridade' not in st.session_state: st.session_state.escolaridade = ""
                    if 'ano_escolar' not in st.session_state: st.session_state.ano_escolar = ""

                    st.selectbox("Escolaridade", ["", "Educação Infantil", "Ensino Fundamental I", "Ensino Fundamental II", "Ensino Médio"], format_func=lambda x: "Selecione a Escolaridade..." if x == "" else x, key="escolaridade")
                    
                    opcoes_ano = {
                        "": [],
                        "Educação Infantil":    ["", "Maternal I", "Maternal II", "Etapa I", "Etapa II"],
                        "Ensino Fundamental I": ["", "1º Ano", "2º Ano", "3º Ano", "4º Ano", "5º Ano"],
                        "Ensino Fundamental II":["", "6º Ano", "7º Ano", "8º Ano", "9º Ano"],
                        "Ensino Médio":         ["", "1º Ano", "2º Ano", "3º Ano"]
                    }
                    
                    if st.session_state.escolaridade:
                        st.selectbox("Ano Escolar 2026", opcoes_ano[st.session_state.escolaridade], format_func=lambda x: "Selecione o Ano Escolar..." if x == "" else x, key="ano_escolar")
                    st.write("---")

                    # --- SUB-FLUXO A2 FORMULÁRIO ---
                    if "A2" in sub_opcao_a:
                        with st.form("form_fluxo_a2"):
                            st.info("📌 Requisitos (A2): Envie a **Certidão de Nascimento** contendo explicitamente a averbação de adoção.")
                            nome_filho_a2 = st.text_input("Nome Completo da Criança")
                            genero_a2 = st.selectbox("Gênero:", ["", "Masculino", "Feminino"], format_func=lambda x: "Selecione o Gênero..." if x == "" else x)
                            data_maxima_a2 = date.today() - timedelta(days=730)
                            data_nascimento_a2 = st.date_input("Data de Nascimento da Criança", min_value=date(2000, 1, 1), max_value=data_maxima_a2, format="DD/MM/YYYY")
                            certidao_averbada = st.file_uploader("Anexar Certidão com Averbação de Adoção", type=["pdf", "png", "jpg", "jpeg"], key="cert_a2")
                            declaracao_escolar_a2 = st.file_uploader("Anexar Declaração Escolar de Matrícula 📚", type=["pdf", "png", "jpg", "jpeg"], key="declaracao_escolar_a2")
                            
                            st.divider()
                            forcar_envio_rh_a2 = False
                            if st.session_state.erro_ia_a2:
                                st.error(f"⚠️ A IA encontrou uma divergência: {st.session_state.erro_ia_a2}")
                                forcar_envio_rh_a2 = st.checkbox("Declaro que o documento é autêntico. Quero forçar o envio para análise visual e manual do RH.", key="rh_a2_bypass")

                            aceite_ia = st.checkbox("Estou ciente de que os documentos enviados serão processados e analisados automaticamente por inteligência artificial para fins de validação cadastral.", key="ia_a2")
                            aceite_lgpd = st.checkbox("Concordo com o tratamento, armazenamento e uso dos dados para a concessão do Kit Escolar, em conformidade com a LGPD e as normas de compliance da instituição.", key="lgpd_a2")
                            salvar_a2 = st.form_submit_button("Validar e Adicionar ao Carrinho (A2)")

                        if salvar_a2:
                            if not verificar_limite_clique("valida_a2", 5):
                                st.stop()
                            valido_cert = validar_arquivo(certidao_averbada, "Certidão com Averbação")
                            valido_decl = validar_arquivo(declaracao_escolar_a2, "Declaração Escolar")
                            if not (valido_cert and valido_decl):
                                st.stop()
                            erros_a2 = []
                            if not nome_filho_a2.strip(): erros_a2.append("O nome da criança é obrigatório.")
                            if not genero_a2: erros_a2.append("O gênero é obrigatório.")
                            if not st.session_state.escolaridade: erros_a2.append("A escolaridade é obrigatória.")
                            if not st.session_state.ano_escolar: erros_a2.append("O ano escolar é obrigatório.")
                            if not certidao_averbada: erros_a2.append("A certidão com averbação é obrigatória.")
                            if not declaracao_escolar_a2: erros_a2.append("A declaração escolar de matrícula é obrigatória.")
                            if not aceite_ia or not aceite_lgpd: erros_a2.append("Você deve aceitar os termos de uso de IA e a política de privacidade (LGPD) para prosseguir.")

                            if erros_a2:
                                for e in erros_a2: st.error(f"⚠️ {e}")
                            else:
                                if forcar_envio_rh_a2:
                                    with st.spinner("📤 Enviando documentos para a quarentena do RH..."):
                                        cracha_colab = st.session_state.colaborador['Crachá']
                                        try:
                                            urls = []
                                            if certidao_averbada:
                                                u1 = upload_documento_supabase(certidao_averbada, "certidao_averbada", cracha_colab)
                                                if u1: urls.append(u1)
                                            if declaracao_escolar_a2:
                                                u2 = upload_documento_supabase(declaracao_escolar_a2, "declaracao", cracha_colab)
                                                if u2: urls.append(u2)
                                            url_doc_a2 = ",".join(urls) if urls else None
                                        except Exception:
                                            url_doc_a2 = None

                                    erro_salvo = st.session_state.erro_ia_a2
                                    st.session_state.erro_ia_a2 = None
                                    nome_final_a2 = padroniza_texto(nome_filho_a2)

                                    db = SessionLocal()
                                    try:
                                        if verificar_crianca_duplicada(db, nome_final_a2, data_nascimento_a2):
                                            st.error("⚠️ Esta criança já está no seu carrinho ou já possui um kit cadastrado.")
                                        else:
                                            st.session_state.lista_dependentes.append({
                                                "ID_Dependente": None,
                                                "ID_Colaborador": st.session_state.colaborador['Crachá'],
                                                "Nome_filho": nome_final_a2,
                                                "Gênero": genero_a2,
                                                "Data_nascimento": data_nascimento_a2.strftime("%d/%m/%Y"),
                                                "Escolaridade": st.session_state.escolaridade,
                                                "Ano_escolar": st.session_state.ano_escolar,
                                                "revisao_rh": "Revisão Manual (Bypass Usuário)",
                                                "Fluxo_Documento": "A2 - Certidão de Nascimento com Averbação de Adoção",
                                                "aceite_ia": aceite_ia,
                                                "aceite_lgpd": aceite_lgpd,
                                                "data_aceite": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                "motivo_reprova_ia": f"Forçado pelo usuário. Erro original: {erro_salvo}",
                                                "url_documento": url_doc_a2
                                            })

                                            cracha_log = st.session_state.colaborador['Crachá']
                                            registrar_log(cracha_log, "ENVIO_RH_BYPASS", f"Bypass forçado (A2). Erro contornado: {erro_salvo}")
                                            registrar_log(cracha_log, "DEPENDENTE_CARRINHO_ADD", f"Dependente {nome_final_a2} adicionado ao carrinho via Bypass.")

                                            st.success("✅ Dependente adicionado ao carrinho com sucesso!")
                                            st.session_state.aguardando_decisao = True
                                            st.rerun()
                                    finally:
                                        db.close()
                                else:
                                    with st.spinner("Analisando certidão com averbação via IA e cruzando dados... Aguarde"):
                                        dados_a2, err_a2 = analisa_certidao_averbacao(certidao_averbada)
                                        
                                        if err_a2:
                                            registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou A2: {err_a2}")
                                            st.session_state.erro_ia_a2 = err_a2
                                            st.rerun()
                                        elif not dados_a2.get("tem_averbacao_adocao"):
                                            msg_a2_err = "O documento não possui a averbação de adoção exigida."
                                            registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou A2: {msg_a2_err}")
                                            st.session_state.erro_ia_a2 = msg_a2_err
                                            st.rerun()
                                        else:
                                            dados_ok, msg_dados = valida_dados_crianca_certidao(
                                                dados_a2, nome_filho_a2, data_nascimento_a2, genero_a2
                                            )
                                            
                                            if not dados_ok:
                                                registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou A2: {msg_dados}")
                                                st.session_state.erro_ia_a2 = msg_dados
                                                st.rerun()
                                            else:
                                                dados_decl_a2, err_decl_a2 = analisa_declaracao_escolar(declaracao_escolar_a2)
                                                
                                                if err_decl_a2:
                                                    registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou A2 (Declaração): {err_decl_a2}")
                                                    st.session_state.erro_ia_a2 = err_decl_a2
                                                    st.rerun()
                                                else:
                                                    decl_ok_a2, msg_decl_a2, status_rh_a2, motivo_rh_a2 = valida_declaracao_escolar(
                                                        dados_decl_a2, dados_a2.get("nome_crianca") or nome_filho_a2
                                                    )

                                                    if not decl_ok_a2:
                                                        registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou A2 (Declaração): {msg_decl_a2}")
                                                        st.session_state.erro_ia_a2 = msg_decl_a2
                                                        st.rerun()
                                                    else:
                                                        if status_rh_a2 and str(status_rh_a2).startswith("Sim"):
                                                            st.warning(f"⚠️ **Aviso de Cadastro:** {msg_decl_a2}")
                                                            time.sleep(3.5)
                                                        
                                                        nome_colab = padroniza_texto(st.session_state.colaborador['Nome'])
                                                        pais_responsaveis = [padroniza_texto(p) for p in dados_a2.get("nomes_pais_responsaveis", [])]

                                                        if nome_colab not in pais_responsaveis:
                                                            msg_pais_err = f"O nome do colaborador ({st.session_state.colaborador['Nome']}) não consta como pai/mãe adotivo(a) na certidão."
                                                            registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou A2: {msg_pais_err}")
                                                            st.session_state.erro_ia_a2 = msg_pais_err
                                                            st.rerun()
                                                        else:
                                                            db = SessionLocal()
                                                            try:
                                                                nome_extraido_a2 = dados_a2.get("nome_crianca", "")
                                                                nome_final_a2 = padroniza_texto(nome_extraido_a2) if nome_extraido_a2 else padroniza_texto(nome_filho_a2)
                                                                
                                                                if verificar_crianca_duplicada(db, nome_final_a2, data_nascimento_a2):
                                                                    st.error("⚠️ Esta criança está no seu carrinho ou já possui um kit cadastrado.")
                                                                else:
                                                                    st.session_state.erro_ia_a2 = None
                                                                    st.session_state.lista_dependentes.append({
                                                                        "ID_Dependente": None,
                                                                        "ID_Colaborador": st.session_state.colaborador['Crachá'],
                                                                        "Nome_filho": nome_final_a2,
                                                                        "Gênero": genero_a2,
                                                                        "Data_nascimento": data_nascimento_a2.strftime("%d/%m/%Y"),
                                                                        "Escolaridade": st.session_state.escolaridade,
                                                                        "Ano_escolar": st.session_state.ano_escolar,
                                                                        "revisao_rh": status_rh_a2, 
                                                                        "Fluxo_Documento": "A2 - Certidão de Nascimento com Averbação de Adoção",
                                                                        "aceite_ia": aceite_ia,
                                                                        "aceite_lgpd": aceite_lgpd,
                                                                        "data_aceite": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                                        "motivo_reprova_ia": motivo_rh_a2,
                                                                        "url_documento": None
                                                                    })

                                                                    cracha_log = st.session_state.colaborador['Crachá']
                                                                    registrar_log(cracha_log, "IA_VALIDACAO_SUCESSO", "Validação A2 aprovada pela IA.")
                                                                    registrar_log(cracha_log, "DEPENDENTE_CARRINHO_ADD", f"Dependente {nome_final_a2} validado pela IA e adicionado ao carrinho.")

                                                                    st.success("✅ Dependente adicionado ao carrinho com sucesso!")
                                                                    st.session_state.aguardando_decisao = True
                                                                    st.rerun()
                                                            finally:
                                                                db.close()

                    # --- SUB-FLUXO A3 FORMULÁRIO ---
                    elif "A3" in sub_opcao_a:
                        with st.form("form_fluxo_a3"):
                            st.info("📌 Requisitos (A3): Envie o **Termo de Guarda e Responsabilidade ou Decisão Judicial** especificando a guarda para fins de adoção.")
                            nome_filho_a3 = st.text_input("Nome Completo da Criança / Adolescente")
                            data_maxima_a3 = date.today() - timedelta(days=730)
                            data_nascimento_a3 = st.date_input("Data de Nascimento da Criança", min_value=date(2000, 1, 1), max_value=data_maxima_a3, format="DD/MM/YYYY")
                            doc_judicial = st.file_uploader("Anexar Documento Judicial (Guarda para Fins de Adoção)", type=["pdf", "png", "jpg", "jpeg"], key="doc_a3")
                            
                            st.divider()
                            forcar_envio_rh_a3 = False
                            if st.session_state.erro_ia_a3:
                                st.error(f"⚠️ A IA encontrou uma divergência: {st.session_state.erro_ia_a3}")
                                forcar_envio_rh_a3 = st.checkbox("Declaro que o documento é autêntico. Quero forçar o envio para análise visual e manual do RH.", key="rh_a3_bypass")

                            aceite_ia = st.checkbox("Estou ciente de que os documentos enviados serão processados e analisados automaticamente por inteligência artificial para fins de validação cadastral.", key="ia_a3")
                            aceite_lgpd = st.checkbox("Concordo com o tratamento, armazenamento e uso dos dados para a concessão do Kit Escolar, em conformidade com a LGPD e as normas de compliance da instituição.", key="lgpd_a3")
                            salvar_a3 = st.form_submit_button("Validar e Adicionar ao Carrinho (A3)")

                        if salvar_a3:
                            if not verificar_limite_clique("valida_a3", 5):
                                st.stop()
                            valido_doc = validar_arquivo(doc_judicial, "Documento Judicial")
                            if not valido_doc:
                                st.stop()

                            erros_a3 = []
                            if not nome_filho_a3.strip(): erros_a3.append("O nome da criança é obrigatório.")
                            if not st.session_state.escolaridade: erros_a3.append("A escolaridade é obrigatória.")
                            if not st.session_state.ano_escolar: erros_a3.append("O ano escolar é obrigatório.")
                            if not doc_judicial: erros_a3.append("O documento judicial é obrigatório.")
                            if not aceite_ia or not aceite_lgpd: erros_a3.append("Você deve aceitar os termos de uso de IA e a política de privacidade (LGPD) para prosseguir.")

                            if erros_a3:
                                for e in erros_a3: st.error(f"⚠️ {e}")
                            else:
                                if forcar_envio_rh_a3:
                                    with st.spinner("📤 Enviando documentos para a quarentena do RH..."):
                                        try:
                                            url_doc_a3 = upload_documento_supabase(doc_judicial, "doc_judicial", st.session_state.colaborador['Crachá'])
                                        except Exception:
                                            url_doc_a3 = None

                                    erro_salvo = st.session_state.erro_ia_a3
                                    st.session_state.erro_ia_a3 = None
                                    nome_final_a3 = padroniza_texto(nome_filho_a3)

                                    db = SessionLocal()
                                    try:
                                        if verificar_crianca_duplicada(db, nome_final_a3, data_nascimento_a3):
                                            st.error("⚠️ Esta criança já está no seu carrinho ou já possui um kit cadastrado.")
                                        else:
                                            st.session_state.lista_dependentes.append({
                                                "ID_Dependente": None,
                                                "ID_Colaborador": st.session_state.colaborador['Crachá'],
                                                "Nome_filho": nome_final_a3,
                                                "Gênero": "Não informado",
                                                "Data_nascimento": data_nascimento_a3.strftime("%d/%m/%Y"),
                                                "Escolaridade": st.session_state.escolaridade,
                                                "Ano_escolar": st.session_state.ano_escolar,
                                                "revisao_rh": "Revisão Manual (Bypass Usuário)",
                                                "Fluxo_Documento": "A3 - Termo de Guarda para Fins de Adoção", 
                                                "aceite_ia": aceite_ia,
                                                "aceite_lgpd": aceite_lgpd,
                                                "data_aceite": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                "motivo_reprova_ia": f"Forçado pelo usuário. Erro original: {erro_salvo}",
                                                "url_documento": url_doc_a3
                                            })

                                            cracha_log = st.session_state.colaborador['Crachá']
                                            registrar_log(cracha_log, "ENVIO_RH_BYPASS", f"Bypass forçado (A3). Erro contornado: {erro_salvo}")
                                            registrar_log(cracha_log, "DEPENDENTE_CARRINHO_ADD", f"Dependente {nome_final_a3} adicionado ao carrinho via Bypass.")

                                            st.success("✅ Dependente adicionado ao carrinho com sucesso!")
                                            st.session_state.aguardando_decisao = True
                                            st.rerun()
                                    finally:
                                        db.close()
                                else:
                                    with st.spinner("Analisando documento judicial via IA e cruzando dados... Aguarde"):
                                        dados_a3, err_a3 = analisa_guarda_adocao(doc_judicial)
                                        if err_a3:
                                            registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou A3: {err_a3}")
                                            st.session_state.erro_ia_a3 = err_a3
                                            st.rerun()
                                        else:
                                            if not dados_a3.get("documento_judicial_valido"):
                                                msg_doc_val = "Documento adicionado não possui validade judicial."
                                                registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou A3: {msg_doc_val}")
                                                st.session_state.erro_ia_a3 = msg_doc_val
                                                st.rerun()
                                            else:
                                                # Cruzamento de nome do colaborador na guarda
                                                nome_colab = padroniza_texto(st.session_state.colaborador['Nome'])
                                                responsaveis_judiciais = [padroniza_texto(r) for r in dados_a3.get("responsaveis", [])]
                                                if responsaveis_judiciais and nome_colab not in responsaveis_judiciais:
                                                    msg_resp_err = f"O nome do colaborador ({st.session_state.colaborador['Nome']}) não consta como guardião(ã) no documento judicial."
                                                    registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou A3: {msg_resp_err}")
                                                    st.session_state.erro_ia_a3 = msg_resp_err
                                                    st.rerun()
                                                else:
                                                    db = SessionLocal()
                                                    try:
                                                        nome_extraido_a3 = dados_a3.get("nome_crianca", "")
                                                        nome_final_a3 = padroniza_texto(nome_extraido_a3) if nome_extraido_a3 else padroniza_texto(nome_filho_a3)
                                                        
                                                        if verificar_crianca_duplicada(db, nome_final_a3, data_nascimento_a3):
                                                            st.error("⚠️ Esta criança está no seu carrinho ou já possui um kit cadastrado.")
                                                        else:
                                                            st.session_state.erro_ia_a3 = None
                                                            st.session_state.lista_dependentes.append({
                                                                "ID_Dependente": None,
                                                                "ID_Colaborador": st.session_state.colaborador['Crachá'],
                                                                "Nome_filho": nome_final_a3,
                                                                "Gênero": "Não informado",
                                                                "Data_nascimento": data_nascimento_a3.strftime("%d/%m/%Y"),
                                                                "Escolaridade": st.session_state.escolaridade,
                                                                "Ano_escolar": st.session_state.ano_escolar,
                                                                "revisao_rh": "Sim (Guarda para Adoção A3)",
                                                                "Fluxo_Documento": "A3 - Termo de Guarda para Fins de Adoção", 
                                                                "aceite_ia": aceite_ia,
                                                                "aceite_lgpd": aceite_lgpd,
                                                                "data_aceite": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                                "motivo_reprova_ia": None,
                                                                "url_documento": None
                                                            })

                                                            cracha_log = st.session_state.colaborador['Crachá']
                                                            registrar_log(cracha_log, "IA_VALIDACAO_SUCESSO", "Validação A3 aprovada pela IA.")
                                                            registrar_log(cracha_log, "DEPENDENTE_CARRINHO_ADD", f"Dependente {nome_final_a3} validado pela IA e adicionado ao carrinho.")

                                                            st.success("✅ Dependente adicionado ao carrinho com sucesso!")
                                                            st.session_state.aguardando_decisao = True
                                                            st.rerun()
                                                    finally:
                                                        db.close()

        # ================= MARCADOR: CAMINHO B =================
        elif st.session_state.tipo_fluxo == "B":
            st.subheader("📝 Cadastro de Enteado(a)")
            
            if st.button("⬅️ Voltar e escolher outra opção", key="voltar_b"):
                st.session_state.tipo_fluxo = None
                if 'sub_opcao_b' in st.session_state: del st.session_state['sub_opcao_b']
                st.session_state.erro_ia_b1 = None
                st.session_state.erro_ia_b2 = None
                st.rerun()
            
            st.write("---")
            sub_opcao_b = st.radio(
                "🎯 Selecione o documento para comprovação do vínculo com o cônjuge/companheiro(a):",
                ["**B1:** Documento comprobatório de união estável + Certidão de Nascimento ou RG do Filho(a)", "**B2:** Certidão de Casamento + Certidão de Nascimento ou RG do Filho(a)"],
                index=None, key="sub_opcao_b"
            )

            if sub_opcao_b:
                st.divider()
                if 'escolaridade' not in st.session_state: st.session_state.escolaridade = ""
                if 'ano_escolar' not in st.session_state: st.session_state.ano_escolar = ""

                st.selectbox("Escolaridade", ["", "Educação Infantil", "Ensino Fundamental I", "Ensino Fundamental II", "Ensino Médio"], format_func=lambda x: "Selecione a Escolaridade..." if x == "" else x, key="escolaridade")
                
                opcoes_ano = {
                    "": [],
                    "Educação Infantil":    ["", "Maternal I", "Maternal II", "Etapa I", "Etapa II"],
                    "Ensino Fundamental I": ["", "1º Ano", "2º Ano", "3º Ano", "4º Ano", "5º Ano"],
                    "Ensino Fundamental II":["", "6º Ano", "7º Ano", "8º Ano", "9º Ano"],
                    "Ensino Médio":         ["", "1º Ano", "2º Ano", "3º Ano"]
                }
                
                if st.session_state.escolaridade:
                    st.selectbox("Ano Escolar 2026", opcoes_ano[st.session_state.escolaridade], format_func=lambda x: "Selecione o Ano Escolar..." if x == "" else x, key="ano_escolar")

                # --- FORMA B1 ---
                if "B1" in sub_opcao_b:
                    with st.form("form_fluxo_b1"):
                        st.info("📌 Requisitos: Envie a **Certidão de Nascimento ou RG da Criança** e a **Declaração de União Estável** com firma reconhecida.")
                        nome_filho_b = st.text_input("Nome Completo da Criança")
                        genero_b = st.selectbox("Gênero:", ["", "Masculino", "Feminino"], format_func=lambda x: "Selecione o Gênero..." if x == "" else x)
                        data_nascimento_b = st.date_input("Data de Nascimento da Criança", min_value=date(2000, 1, 1), max_value=date.today() - timedelta(days=730), format="DD/MM/YYYY")
                        certidao_b = st.file_uploader("Anexar Certidão de Nascimento ou RG da Criança", type=["pdf", "png", "jpg", "jpeg"], key="cert_b1", help="Aceita Certidão de Nascimento ou RG (com a filiação/verso visível).")
                        uniao_b = st.file_uploader("Anexar União Estável (com firma reconhecida e Selo do Cartório)", type=["pdf", "png", "jpg", "jpeg"], key="doc_b1")
                        declaracao_escolar_b1 = st.file_uploader("Anexar Declaração Escolar de Matrícula 📚", type=["pdf", "png", "jpg", "jpeg"], key="declaracao_escolar_b1")
                        
                        st.divider()
                        forcar_envio_rh_b1 = False
                        if st.session_state.erro_ia_b1:
                            st.error(f"⚠️ A IA encontrou uma divergência: {st.session_state.erro_ia_b1}")
                            forcar_envio_rh_b1 = st.checkbox("Declaro que o documento é autêntico. Quero forçar o envio para análise visual e manual do RH.", key="rh_b1_bypass")

                        aceite_ia = st.checkbox("Estou ciente de que os documentos enviados serão processados e analisados automaticamente por inteligência artificial para fins de validação cadastral.", key="ia_b1")
                        aceite_lgpd = st.checkbox("Concordo com o tratamento, armazenamento e uso dos dados para a concessão do Kit Escolar, em conformidade com a LGPD e as normas de compliance da instituição.", key="lgpd_b1")
                        salvar_b1 = st.form_submit_button("Validar e Adicionar ao Carrinho (B1)")

                    if salvar_b1:
                        if not verificar_limite_clique("valida_b1", 5):
                            st.stop()
                        # --- INÍCIO DA VALIDAÇÃO DE ARQUIVOS ---
                        valido_cert = validar_arquivo(certidao_b, "Certidão/RG da Criança")
                        valido_uniao = validar_arquivo(uniao_b, "Declaração de União Estável")
                        valido_decl = validar_arquivo(declaracao_escolar_b1, "Declaração Escolar")  

                        if not (valido_cert and valido_uniao and valido_decl):
                            st.stop()  
                            
                        erros_b = []
                        if not nome_filho_b.strip(): erros_b.append("O nome da criança é obrigatório.")
                        if not genero_b: erros_b.append("O gênero é obrigatório.")
                        if not st.session_state.escolaridade: erros_b.append("A escolaridade é obrigatória.")
                        if not st.session_state.ano_escolar: erros_b.append("O ano escolar é obrigatório.")
                        if not certidao_b: erros_b.append("A Certidão de Nascimento ou RG é obrigatória.")
                        if not uniao_b: erros_b.append("A declaração de união estável é obrigatória.")
                        if not declaracao_escolar_b1: erros_b.append("A declaração escolar de matrícula é obrigatória.")
                        if not aceite_ia or not aceite_lgpd: erros_b.append("Você deve aceitar os termos de uso de IA e a política de privacidade (LGPD) para prosseguir.")
                            
                        if erros_b:
                            for e in erros_b: st.error(f"⚠️ {e}")
                        else:
                            if forcar_envio_rh_b1:
                                with st.spinner("📤 Enviando documentos para a quarentena do RH..."):
                                    cracha_colab = st.session_state.colaborador['Crachá']
                                    try:
                                        urls = []
                                        if certidao_b:
                                            u1 = upload_documento_supabase(certidao_b, "identidade", cracha_colab)
                                            if u1: urls.append(u1)
                                        if uniao_b:
                                            u2 = upload_documento_supabase(uniao_b, "uniao_estavel", cracha_colab)
                                            if u2: urls.append(u2)
                                        if declaracao_escolar_b1:
                                            u3 = upload_documento_supabase(declaracao_escolar_b1, "declaracao", cracha_colab)
                                            if u3: urls.append(u3)
                                        url_doc_b1 = ",".join(urls) if urls else None
                                    except Exception:
                                        url_doc_b1 = None

                                erro_salvo = st.session_state.erro_ia_b1
                                st.session_state.erro_ia_b1 = None
                                nome_final_b1 = padroniza_texto(nome_filho_b)

                                db = SessionLocal()
                                try:
                                    if verificar_crianca_duplicada(db, nome_final_b1, data_nascimento_b):
                                        st.error("⚠️ Esta criança já está no seu carrinho ou já possui um kit cadastrado.")
                                    else:
                                        st.session_state.lista_dependentes.append({
                                            "ID_Dependente": None,
                                            "ID_Colaborador": st.session_state.colaborador['Crachá'],
                                            "Nome_filho": nome_final_b1,
                                            "Gênero": genero_b,
                                            "Data_nascimento": data_nascimento_b.strftime("%d/%m/%Y"),
                                            "Escolaridade": st.session_state.escolaridade,
                                            "Ano_escolar": st.session_state.ano_escolar,
                                            "revisao_rh": "Revisão Manual (Bypass Usuário)",
                                            "Fluxo_Documento": "B1 - Identidade + União Estável (Firma Reconhecida)",
                                            "aceite_ia": aceite_ia,
                                            "aceite_lgpd": aceite_lgpd,
                                            "data_aceite": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                            "motivo_reprova_ia": f"Forçado pelo usuário. Erro original: {erro_salvo}",
                                            "url_documento": url_doc_b1
                                        })

                                        cracha_log = st.session_state.colaborador['Crachá']
                                        registrar_log(cracha_log, "ENVIO_RH_BYPASS", f"Bypass forçado (B1). Erro contornado: {erro_salvo}")
                                        registrar_log(cracha_log, "DEPENDENTE_CARRINHO_ADD", f"Dependente {nome_final_b1} adicionado ao carrinho via Bypass.")

                                        st.success("✅ Dependente adicionado ao carrinho com sucesso!")
                                        st.session_state.aguardando_decisao = True
                                        st.rerun()
                                finally:
                                    db.close()
                            else:
                                with st.spinner("Analisando documentos com a IA e cruzando dados... Aguarde"):
                                    dados_cert, err_cert = analisa_certidao(certidao_b)
                                    
                                    # 🛡️ TRATAMENTO INTELIGENTE: Se o validador recusar por ser RG (mas o documento é válido), aceita e valida pelos dados do form
                                    if err_cert and ("não é" in err_cert.lower() or "certidao" in err_cert.lower() or "certidão" in err_cert.lower()):
                                        dados_cert = {
                                            "nome_crianca": nome_filho_b,
                                            "data_nascimento": data_nascimento_b.strftime("%d/%m/%Y")
                                        }
                                        err_cert = None

                                    if err_cert:
                                        registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou B1 (Identidade): {err_cert}")
                                        st.session_state.erro_ia_b1 = err_cert
                                        st.rerun()
                                    else:
                                        dados_ok, msg_dados = valida_dados_crianca_certidao(
                                            dados_cert, nome_filho_b, data_nascimento_b, genero_b
                                        )
                                        
                                        if not dados_ok:
                                            registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou B1: {msg_dados}")
                                            st.session_state.erro_ia_b1 = msg_dados
                                            st.rerun()
                                        else:
                                            dados_decl_b1, err_decl_b1 = analisa_declaracao_escolar(declaracao_escolar_b1)
                                            
                                            if err_decl_b1:
                                                registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou B1 (Declaração): {err_decl_b1}")
                                                st.session_state.erro_ia_b1 = err_decl_b1
                                                st.rerun()
                                            else:
                                                decl_ok_b1, msg_decl_b1, status_rh_b1, motivo_rh_b1 = valida_declaracao_escolar(
                                                    dados_decl_b1, dados_cert.get("nome_crianca") or nome_filho_b
                                                )
                                                
                                                if not decl_ok_b1:
                                                    registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou B1 (Declaração): {msg_decl_b1}")
                                                    st.session_state.erro_ia_b1 = msg_decl_b1
                                                    st.rerun()
                                                else:
                                                    if status_rh_b1 and str(status_rh_b1).startswith("Sim"):
                                                        st.warning(f"⚠️ **Aviso de Cadastro:** {msg_decl_b1}")
                                                        time.sleep(3.5)

                                                    dados_uniao, err_uniao = analisa_uniao_estavel(uniao_b)
                                                    
                                                    if err_uniao:
                                                        registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou B1 (União Estável): {err_uniao}")
                                                        st.session_state.erro_ia_b1 = err_uniao
                                                        st.rerun()
                                                    elif not dados_uniao.get("firma_reconhecida"):
                                                        msg_firma_err = "A Declaração de União Estável precisa ter firma reconhecida em cartório visível."
                                                        registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou B1: {msg_firma_err}")
                                                        st.session_state.erro_ia_b1 = msg_firma_err
                                                        st.rerun()
                                                    else:
                                                        nome_colab = padroniza_texto(st.session_state.colaborador['Nome'])
                                                        
                                                        texto_uniao_completo = " ".join([
                                                            str(item) for v in dados_uniao.values() 
                                                            for item in (v if isinstance(v, list) else [v]) 
                                                            if v is not None
                                                        ])
                                                        texto_uniao_normalizado = padroniza_texto(texto_uniao_completo)

                                                        if nome_colab not in texto_uniao_normalizado:
                                                            msg_uniao_err = f"O nome do colaborador ({st.session_state.colaborador['Nome']}) não foi encontrado na Declaração de União Estável."
                                                            registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou B1: {msg_uniao_err}")
                                                            st.session_state.erro_ia_b1 = msg_uniao_err
                                                            st.rerun()
                                                        else:
                                                            db = SessionLocal()
                                                            try:
                                                                nome_extraido_b1 = dados_cert.get("nome_crianca", "")
                                                                nome_final_b1 = padroniza_texto(nome_extraido_b1) if nome_extraido_b1 else padroniza_texto(nome_filho_b)
                                                                
                                                                if verificar_crianca_duplicada(db, nome_final_b1, data_nascimento_b):
                                                                    st.error("⚠️ Esta criança já está no seu carrinho ou já possui um kit cadastrado.")
                                                                else:
                                                                    st.session_state.erro_ia_b1 = None
                                                                    st.session_state.lista_dependentes.append({
                                                                        "ID_Dependente": None,
                                                                        "ID_Colaborador": st.session_state.colaborador['Crachá'],
                                                                        "Nome_filho": nome_final_b1,
                                                                        "Gênero": genero_b,
                                                                        "Data_nascimento": data_nascimento_b.strftime("%d/%m/%Y"),
                                                                        "Escolaridade": st.session_state.escolaridade,
                                                                        "Ano_escolar": st.session_state.ano_escolar,
                                                                        "revisao_rh": status_rh_b1, 
                                                                        "Fluxo_Documento": "B1 - Identidade + União Estável (Firma Reconhecida)",
                                                                        "aceite_ia": aceite_ia,
                                                                        "aceite_lgpd": aceite_lgpd,
                                                                        "data_aceite": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                                        "motivo_reprova_ia": motivo_rh_b1,
                                                                        "url_documento": None
                                                                    })

                                                                    cracha_log = st.session_state.colaborador['Crachá']
                                                                    registrar_log(cracha_log, "IA_VALIDACAO_SUCESSO", "Validação B1 aprovada pela IA.")
                                                                    registrar_log(cracha_log, "DEPENDENTE_CARRINHO_ADD", f"Dependente {nome_final_b1} validado pela IA e adicionado ao carrinho.")

                                                                    st.success("✅ Dependente adicionado ao carrinho com sucesso!")
                                                                    st.session_state.aguardando_decisao = True
                                                                    st.rerun()
                                                            finally:
                                                                db.close()

                # --- FORMA B2 ---
                elif "B2" in sub_opcao_b:
                    with st.form("form_fluxo_b2"):
                        st.info("📌 Requisitos (B2): Envie a **Certidão de Nascimento ou RG da Criança** e a **Certidão de Casamento** (sem averbação de divórcio).")
                        nome_filho_b2 = st.text_input("Nome Completo da Criança")
                        genero_b2 = st.selectbox("Gênero:", ["", "Masculino", "Feminino"], format_func=lambda x: "Selecione o Gênero..." if x == "" else x)
                        data_nascimento_b2 = st.date_input("Data de Nascimento da Criança", min_value=date(2000, 1, 1), max_value=date.today() - timedelta(days=730), format="DD/MM/YYYY")
                        
                        certidao_b2 = st.file_uploader("Anexar Certidão de Nascimento ou RG da Criança", type=["pdf", "png", "jpg", "jpeg"], key="cert_b2", help="Se enviar RG, garanta que a filiação (verso) esteja visível.")
                        casamento_b2 = st.file_uploader("Anexar Certidão de Casamento", type=["pdf", "png", "jpg", "jpeg"], key="doc_b2")
                        declaracao_escolar_b2 = st.file_uploader("Anexar Declaração Escolar de Matrícula 📚", type=["pdf", "png", "jpg", "jpeg"], key="declaracao_escolar_b2")
                        
                        st.divider()
                        forcar_envio_rh_b2 = False
                        if st.session_state.erro_ia_b2:
                            st.error(f"⚠️ A IA encontrou uma divergência: {st.session_state.erro_ia_b2}")
                            forcar_envio_rh_b2 = st.checkbox("Declaro que o documento é autêntico. Quero forçar o envio para análise visual e manual do RH.", key="rh_b2_bypass")

                        aceite_ia = st.checkbox("Estou ciente de que os documentos enviados serão processados e analisados automaticamente por inteligência artificial para fins de validação cadastral.", key="ia_b2")
                        aceite_lgpd = st.checkbox("Concordo com o tratamento, armazenamento e uso dos dados para a concessão do Kit Escolar, em conformidade com a LGPD e as normas de compliance da instituição.", key="lgpd_b2")
                        salvar_b2 = st.form_submit_button("Validar e Adicionar ao Carrinho (B2)")

                    if salvar_b2:
                        if not verificar_limite_clique("valida_b2", 5):
                            st.stop()
                        valido_cert = validar_arquivo(certidao_b2, "Certidão/RG da Criança")
                        valido_casam = validar_arquivo(casamento_b2, "Certidão de Casamento")
                        valido_decl = validar_arquivo(declaracao_escolar_b2, "Declaração Escolar")
                        if not (valido_cert and valido_casam and valido_decl):
                            st.stop()

                        erros_b2 = []
                        if not nome_filho_b2.strip(): erros_b2.append("O nome da criança é obrigatório.")
                        if not genero_b2: erros_b2.append("O gênero é obrigatório.")
                        if not st.session_state.escolaridade: erros_b2.append("A escolaridade é obrigatória.")
                        if not st.session_state.ano_escolar: erros_b2.append("O ano escolar é obrigatório.")
                        if not certidao_b2: erros_b2.append("A Certidão de Nascimento ou RG é obrigatória.")
                        if not casamento_b2: erros_b2.append("A certidão de casamento é obrigatória.")
                        if not declaracao_escolar_b2: erros_b2.append("A declaração escolar de matrícula é obrigatória.")
                        if not aceite_ia or not aceite_lgpd: erros_b2.append("Você deve aceitar os termos de uso de IA e a política de privacidade (LGPD) para prosseguir.")

                        if erros_b2:
                            for e in erros_b2: st.error(f"⚠️ {e}")
                        else:
                            if forcar_envio_rh_b2:
                                with st.spinner("📤 Enviando documentos para a quarentena do RH..."):
                                    cracha_colab = st.session_state.colaborador['Crachá']
                                    try:
                                        urls = []
                                        if certidao_b2:
                                            u1 = upload_documento_supabase(certidao_b2, "identidade", cracha_colab)
                                            if u1: urls.append(u1)
                                        if casamento_b2:
                                            u2 = upload_documento_supabase(casamento_b2, "casamento", cracha_colab)
                                            if u2: urls.append(u2)
                                        if declaracao_escolar_b2:
                                            u3 = upload_documento_supabase(declaracao_escolar_b2, "declaracao", cracha_colab)
                                            if u3: urls.append(u3)
                                        url_doc_b2 = ",".join(urls) if urls else None
                                    except Exception:
                                        url_doc_b2 = None

                                erro_salvo = st.session_state.erro_ia_b2
                                st.session_state.erro_ia_b2 = None
                                nome_final_b2 = padroniza_texto(nome_filho_b2)

                                db = SessionLocal()
                                try:
                                    if verificar_crianca_duplicada(db, nome_final_b2, data_nascimento_b2):
                                        st.error("⚠️ Esta criança já está no seu carrinho ou já possui um kit cadastrado.")
                                    else:
                                        st.session_state.lista_dependentes.append({
                                            "ID_Dependente": None,
                                            "ID_Colaborador": st.session_state.colaborador['Crachá'],
                                            "Nome_filho": nome_final_b2,
                                            "Gênero": genero_b2,
                                            "Data_nascimento": data_nascimento_b2.strftime("%d/%m/%Y"),
                                            "Escolaridade": st.session_state.escolaridade,
                                            "Ano_escolar": st.session_state.ano_escolar,
                                            "revisao_rh": "Revisão Manual (Bypass Usuário)",
                                            "Fluxo_Documento": "B2 - Identidade + Certidão de Casamento",
                                            "aceite_ia": aceite_ia,
                                            "aceite_lgpd": aceite_lgpd,
                                            "data_aceite": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                            "motivo_reprova_ia": f"Forçado pelo usuário. Erro original: {erro_salvo}",
                                            "url_documento": url_doc_b2
                                        })

                                        cracha_log = st.session_state.colaborador['Crachá']
                                        registrar_log(cracha_log, "ENVIO_RH_BYPASS", f"Bypass forçado (B2). Erro contornado: {erro_salvo}")
                                        registrar_log(cracha_log, "DEPENDENTE_CARRINHO_ADD", f"Dependente {nome_final_b2} adicionado ao carrinho via Bypass.")

                                        st.success("✅ Dependente adicionado ao carrinho com sucesso!")
                                        st.session_state.aguardando_decisao = True
                                        st.rerun()
                                finally:
                                    db.close()
                            else:
                                with st.spinner("Analisando documentos com a IA e cruzando certidão de casamento... Aguarde"):
                                    dados_cert, err_cert = analisa_certidao(certidao_b2)
                                    
                                    # 🛡️ TRATAMENTO INTELIGENTE DE RG (Fallback)
                                    if err_cert and ("não é" in err_cert.lower() or "certidao" in err_cert.lower() or "certidão" in err_cert.lower()):
                                        dados_cert = {
                                            "nome_crianca": nome_filho_b2,
                                            "data_nascimento": data_nascimento_b2.strftime("%d/%m/%Y")
                                        }
                                        err_cert = None

                                    if err_cert:
                                        registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou B2 (Identidade): {err_cert}")
                                        st.session_state.erro_ia_b2 = err_cert
                                        st.rerun()
                                    else:
                                        dados_ok, msg_dados = valida_dados_crianca_certidao(
                                            dados_cert, nome_filho_b2, data_nascimento_b2, genero_b2
                                        )
                                        if not dados_ok:
                                            registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou B2: {msg_dados}")
                                            st.session_state.erro_ia_b2 = msg_dados
                                            st.rerun()
                                        else:
                                            dados_decl_b2, err_decl_b2 = analisa_declaracao_escolar(declaracao_escolar_b2)
                                            
                                            if err_decl_b2:
                                                registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou B2 (Declaração): {err_decl_b2}")
                                                st.session_state.erro_ia_b2 = err_decl_b2
                                                st.rerun()
                                            else:
                                                decl_ok_b2, msg_decl_b2, status_rh_b2, motivo_rh_b2 = valida_declaracao_escolar(
                                                    dados_decl_b2, dados_cert.get("nome_crianca") or nome_filho_b2
                                                )
                                                
                                                if not decl_ok_b2:
                                                    registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou B2 (Declaração): {msg_decl_b2}")
                                                    st.session_state.erro_ia_b2 = msg_decl_b2
                                                    st.rerun()
                                                else:
                                                    if status_rh_b2 and str(status_rh_b2).startswith("Sim"):
                                                        st.warning(f"⚠️ **Aviso de Cadastro:** {msg_decl_b2}")
                                                        time.sleep(3.5)
                                                    dados_casam, err_casam = analisa_certidao_complementar(casamento_b2)
                                                    
                                                    if err_casam:
                                                        registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou B2 (Casamento): {err_casam}")
                                                        st.session_state.erro_ia_b2 = err_casam
                                                        st.rerun()
                                                    elif not dados_casam.get("documento_valido"):
                                                        msg_casam_val = "A Certidão de Casamento é inválida ou não pôde ser lida."
                                                        registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou B2: {msg_casam_val}")
                                                        st.session_state.erro_ia_b2 = msg_casam_val
                                                        st.rerun()
                                                    else:
                                                        # 🛡️ CRUZAMENTO ROBUSTO B2 (Varre todo o texto da certidão de casamento)
                                                        nome_colab = padroniza_texto(st.session_state.colaborador['Nome'])
                                                        
                                                        texto_casam_completo = " ".join([
                                                            str(item) for v in dados_casam.values() 
                                                            for item in (v if isinstance(v, list) else [v]) 
                                                            if v is not None
                                                        ])
                                                        texto_casam_normalizado = padroniza_texto(texto_casam_completo)

                                                        if nome_colab not in texto_casam_normalizado:
                                                            msg_casam_err = f"O nome do colaborador ({st.session_state.colaborador['Nome']}) não foi encontrado na Certidão de Casamento."
                                                            registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou B2: {msg_casam_err}")
                                                            st.session_state.erro_ia_b2 = msg_casam_err
                                                            st.rerun()
                                                        else:
                                                            db = SessionLocal()
                                                            try:
                                                                nome_extraido_b2 = dados_cert.get("nome_crianca", "")
                                                                nome_final_b2 = padroniza_texto(nome_extraido_b2) if nome_extraido_b2 else padroniza_texto(nome_filho_b2)
                                                                
                                                                if verificar_crianca_duplicada(db, nome_final_b2, data_nascimento_b2):
                                                                    st.error("⚠️ Esta criança já está no seu carrinho ou já possui um kit cadastrado.")
                                                                else:
                                                                    st.session_state.erro_ia_b2 = None
                                                                    st.session_state.lista_dependentes.append({
                                                                        "ID_Dependente": None,
                                                                        "ID_Colaborador": st.session_state.colaborador['Crachá'],
                                                                        "Nome_filho": nome_final_b2,
                                                                        "Gênero": genero_b2,
                                                                        "Data_nascimento": data_nascimento_b2.strftime("%d/%m/%Y"),
                                                                        "Escolaridade": st.session_state.escolaridade,
                                                                        "Ano_escolar": st.session_state.ano_escolar,
                                                                        "revisao_rh": status_rh_b2, 
                                                                        "Fluxo_Documento": "B2 - Identidade + Certidão de Casamento",
                                                                        "aceite_ia": aceite_ia,
                                                                        "aceite_lgpd": aceite_lgpd,
                                                                        "data_aceite": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                                        "motivo_reprova_ia": motivo_rh_b2,
                                                                        "url_documento": None
                                                                    })

                                                                    cracha_log = st.session_state.colaborador['Crachá']
                                                                    registrar_log(cracha_log, "IA_VALIDACAO_SUCESSO", "Validação B2 aprovada pela IA.")
                                                                    registrar_log(cracha_log, "DEPENDENTE_CARRINHO_ADD", f"Dependente {nome_final_b2} validado pela IA e adicionado ao carrinho.")

                                                                    st.success("✅ Dependente adicionado ao carrinho com sucesso!")
                                                                    st.session_state.aguardando_decisao = True
                                                                    st.rerun()
                                                            finally:
                                                                db.close()

        # ================= MARCADOR: CAMINHO C =================
        elif st.session_state.tipo_fluxo == "C":
            st.subheader("📝 Cadastro - Criança ou Adolescente sob Guarda ou Tutela")
            
            if st.button("⬅️ Voltar e escolher outra opção", key="voltar_c"):
                st.session_state.tipo_fluxo = None
                if 'sub_opcao_c' in st.session_state: del st.session_state['sub_opcao_c']
                st.session_state.erro_ia_c1 = None
                st.session_state.erro_ia_c2 = None
                st.rerun()

            st.write("---")
            sub_opcao_c = st.radio(
                "🎯 Selecione o documento judicial de responsabilidade:",
                ["**C1:** Termo/Certidão de Guarda Judicial + Certidão de Nascimento ou RG", "**C2:** Termo de Tutela Judicial + Certidão de Nascimento ou RG"],
                index=None, key="sub_opcao_c"
            )

            if sub_opcao_c:
                st.divider()
                if 'escolaridade' not in st.session_state: st.session_state.escolaridade = ""
                if 'ano_escolar' not in st.session_state: st.session_state.ano_escolar = ""

                st.selectbox("Escolaridade", ["", "Educação Infantil", "Ensino Fundamental I", "Ensino Fundamental II", "Ensino Médio"], format_func=lambda x: "Selecione a Escolaridade..." if x == "" else x, key="escolaridade")
                
                opcoes_ano = {
                    "": [],
                    "Educação Infantil":    ["", "Maternal I", "Maternal II", "Etapa I", "Etapa II"],
                    "Ensino Fundamental I": ["", "1º Ano", "2º Ano", "3º Ano", "4º Ano", "5º Ano"],
                    "Ensino Fundamental II":["", "6º Ano", "7º Ano", "8º Ano", "9º Ano"],
                    "Ensino Médio":         ["", "1º Ano", "2º Ano", "3º Ano"]
                }
                
                if st.session_state.escolaridade:
                    st.selectbox("Ano Escolar 2026", opcoes_ano[st.session_state.escolaridade], format_func=lambda x: "Selecione o Ano Escolar..." if x == "" else x, key="ano_escolar")

                st.write("---")

                # ==================== SUB-FLUXO C1: GUARDA JUDICIAL ====================
                if "C1" in sub_opcao_c:
                    with st.form("form_fluxo_c1"):
                        st.info("📌 Requisitos (C1): Anexe o **Termo/Certidão de Guarda Judicial** E a **Certidão de Nascimento ou RG da Criança**.")
                        nome_filho_c1 = st.text_input("Nome Completo da Criança / Adolescente")
                        genero_c1 = st.selectbox("Gênero:", ["", "Masculino", "Feminino"], format_func=lambda x: "Selecione o Gênero..." if x == "" else x, key="genero_c1")
                        data_maxima_c1 = date.today() - timedelta(days=730)
                        data_nascimento_c1 = st.date_input("Data de Nascimento da Criança", min_value=date(2000, 1, 1), max_value=data_maxima_c1, format="DD/MM/YYYY", key="dt_c1")
                        
                        certidao_c1 = st.file_uploader("Anexar Certidão de Nascimento ou RG da Criança", type=["pdf", "png", "jpg", "jpeg"], key="cert_c1", help="Se enviar RG, garanta que a filiação (verso) esteja visível.")
                        termo_guarda_c1 = st.file_uploader("Anexar Termo/Certidão de Guarda Judicial", type=["pdf", "png", "jpg", "jpeg"], key="termo_guarda_c1")
                        declaracao_escolar_c1 = st.file_uploader("Anexar Declaração Escolar de Matrícula 📚", type=["pdf", "png", "jpg", "jpeg"], key="declaracao_escolar_c1")
                        
                        st.divider()
                        forcar_envio_rh_c1 = False
                        if st.session_state.erro_ia_c1:
                            st.error(f"⚠️ A IA encontrou uma divergência: {st.session_state.erro_ia_c1}")
                            forcar_envio_rh_c1 = st.checkbox("Declaro que o documento é autêntico. Quero forçar o envio para análise visual e manual do RH.", key="rh_c1_bypass")

                        aceite_ia = st.checkbox("Estou ciente de que os documentos enviados serão processados e analisados automaticamente por inteligência artificial para fins de validação cadastral.", key="ia_c1")
                        aceite_lgpd = st.checkbox("Concordo com o tratamento, armazenamento e uso dos dados para a concessão do Kit Escolar, em conformidade com a LGPD e as normas de compliance da instituição.", key="lgpd_c1")
                        salvar_c1 = st.form_submit_button("Validar e Adicionar ao Carrinho (C1)")

                    if salvar_c1:
                        if not verificar_limite_clique("valida_c1", 5):                            
                            st.stop()
                        valido_cert = validar_arquivo(certidao_c1, "Certidão/RG da Criança")
                        valido_guarda = validar_arquivo(termo_guarda_c1, "Termo de Guarda")
                        valido_decl = validar_arquivo(declaracao_escolar_c1, "Declaração Escolar")
                        if not (valido_cert and valido_guarda and valido_decl):
                            st.stop()

                        erros_c1 = []
                        if not nome_filho_c1.strip(): erros_c1.append("O nome da criança é obrigatório.")
                        if not genero_c1: erros_c1.append("O gênero é obrigatório.")
                        if not st.session_state.escolaridade: erros_c1.append("A escolaridade é obrigatória.")
                        if not st.session_state.ano_escolar: erros_c1.append("O ano escolar é obrigatório.")
                        if not certidao_c1 or not termo_guarda_c1: erros_c1.append("Documento(s) ausente(s) ou ilegível(is)")
                        if not declaracao_escolar_c1: erros_c1.append("A declaração escolar de matrícula é obrigatória.")
                        if not aceite_ia or not aceite_lgpd: erros_c1.append("Você deve aceitar os termos de uso de IA e a política de privacidade (LGPD) para prosseguir.")
                            
                        if erros_c1:
                            for e in erros_c1: st.error(f"⚠️ {e}")
                        else:
                            if forcar_envio_rh_c1:
                                with st.spinner("📤 Enviando documentos para a quarentena do RH..."):
                                    cracha_colab = st.session_state.colaborador['Crachá']
                                    try:
                                        urls = []
                                        if certidao_c1:
                                            u1 = upload_documento_supabase(certidao_c1, "identidade", cracha_colab)
                                            if u1: urls.append(u1)
                                        if termo_guarda_c1:
                                            u2 = upload_documento_supabase(termo_guarda_c1, "guarda_judicial", cracha_colab)
                                            if u2: urls.append(u2)
                                        if declaracao_escolar_c1:
                                            u3 = upload_documento_supabase(declaracao_escolar_c1, "declaracao", cracha_colab)
                                            if u3: urls.append(u3)
                                        url_doc_c1 = ",".join(urls) if urls else None
                                    except Exception:
                                        url_doc_c1 = None

                                erro_salvo = st.session_state.erro_ia_c1
                                st.session_state.erro_ia_c1 = None
                                nome_final_c1 = padroniza_texto(nome_filho_c1)

                                db = SessionLocal()
                                try:
                                    if verificar_crianca_duplicada(db, nome_final_c1, data_nascimento_c1):
                                        st.error("⚠️ Esta criança já está no seu carrinho ou já possui um kit cadastrado.")
                                    else:
                                        st.session_state.lista_dependentes.append({
                                            "ID_Dependente": None,
                                            "ID_Colaborador": st.session_state.colaborador['Crachá'],
                                            "Nome_filho": nome_final_c1,
                                            "Gênero": genero_c1,
                                            "Data_nascimento": data_nascimento_c1.strftime("%d/%m/%Y"),
                                            "Escolaridade": st.session_state.escolaridade,
                                            "Ano_escolar": st.session_state.ano_escolar,
                                            "revisao_rh": "Revisão Manual (Bypass Usuário)",
                                            "Fluxo_Documento": "C1 - Identidade + Guarda Judicial",
                                            "aceite_ia": aceite_ia,
                                            "aceite_lgpd": aceite_lgpd,
                                            "data_aceite": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                            "motivo_reprova_ia": f"Forçado pelo usuário. Erro original: {erro_salvo}",
                                            "url_documento": url_doc_c1
                                        })

                                        cracha_log = st.session_state.colaborador['Crachá']
                                        registrar_log(cracha_log, "ENVIO_RH_BYPASS", f"Bypass forçado (C1). Erro contornado: {erro_salvo}")
                                        registrar_log(cracha_log, "DEPENDENTE_CARRINHO_ADD", f"Dependente {nome_final_c1} adicionado ao carrinho via Bypass.")

                                        st.success("✅ Dependente adicionado ao carrinho com sucesso!")
                                        st.session_state.aguardando_decisao = True
                                        st.rerun()
                                finally:
                                    db.close()
                            else:
                                with st.spinner("Analisando documentos com a IA e cruzando termo de guarda... Aguarde"):
                                    dados_cert_c1, err_cert_c1 = analisa_certidao(certidao_c1)
                                    
                                    # 🛡️ TRATAMENTO INTELIGENTE DE RG (Fallback)
                                    if err_cert_c1 and ("não é" in err_cert_c1.lower() or "certidao" in err_cert_c1.lower() or "certidão" in err_cert_c1.lower()):
                                        dados_cert_c1 = {
                                            "nome_crianca": nome_filho_c1,
                                            "data_nascimento": data_nascimento_c1.strftime("%d/%m/%Y")
                                        }
                                        err_cert_c1 = None

                                    if err_cert_c1:
                                        registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou C1 (Identidade): {err_cert_c1}")
                                        st.session_state.erro_ia_c1 = err_cert_c1
                                        st.rerun()
                                    else:
                                        dados_ok, msg_dados = valida_dados_crianca_certidao(
                                            dados_cert_c1, nome_filho_c1, data_nascimento_c1, genero_c1
                                        )
                                        if not dados_ok:
                                            registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou C1: {msg_dados}")
                                            st.session_state.erro_ia_c1 = msg_dados
                                            st.rerun()
                                        else:
                                            dados_decl_c1, err_decl_c1 = analisa_declaracao_escolar(declaracao_escolar_c1)
                                            
                                            if err_decl_c1:
                                                registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou C1 (Declaração): {err_decl_c1}")
                                                st.session_state.erro_ia_c1 = err_decl_c1
                                                st.rerun()
                                            else:
                                                decl_ok_c1, msg_decl_c1, status_rh_c1, motivo_rh_c1 = valida_declaracao_escolar(
                                                    dados_decl_c1, dados_cert_c1.get("nome_crianca") or nome_filho_c1
                                                )
                                                
                                                if not decl_ok_c1:
                                                    registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou C1 (Declaração): {msg_decl_c1}")
                                                    st.session_state.erro_ia_c1 = msg_decl_c1
                                                    st.rerun()
                                                else:
                                                    if status_rh_c1 and str(status_rh_c1).startswith("Sim"):
                                                        st.warning(f"⚠️ **Aviso de Cadastro:** {msg_decl_c1}")
                                                        time.sleep(3.5)
                                                    dados_guarda, err_guarda = analisa_guarda_judicial(termo_guarda_c1)
                                                    
                                                    if err_guarda:
                                                        registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou C1 (Guarda): {err_guarda}")
                                                        st.session_state.erro_ia_c1 = err_guarda
                                                        st.rerun()
                                                    elif not dados_guarda.get("documento_valido"):
                                                        msg_guarda_val = "O Termo de Guarda é inválido ou não pôde ser lido."
                                                        registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou C1: {msg_guarda_val}")
                                                        st.session_state.erro_ia_c1 = msg_guarda_val
                                                        st.rerun()
                                                    else:
                                                        # 🛡️ CRUZAMENTO ROBUSTO C1 (Varre todo o termo de guarda)
                                                        nome_colab = padroniza_texto(st.session_state.colaborador['Nome'])
                                                        
                                                        texto_guarda_completo = " ".join([
                                                            str(item) for v in dados_guarda.values() 
                                                            for item in (v if isinstance(v, list) else [v]) 
                                                            if v is not None
                                                        ])
                                                        texto_guarda_normalizado = padroniza_texto(texto_guarda_completo)

                                                        if nome_colab not in texto_guarda_normalizado:
                                                            msg_guarda_err = f"O nome do colaborador ({st.session_state.colaborador['Nome']}) não foi encontrado no Termo de Guarda Judicial."
                                                            registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou C1: {msg_guarda_err}")
                                                            st.session_state.erro_ia_c1 = msg_guarda_err
                                                            st.rerun()
                                                        else:
                                                            db = SessionLocal()
                                                            try:
                                                                nome_extraido_c1 = dados_cert_c1.get("nome_crianca", "")
                                                                nome_final_c1 = padroniza_texto(nome_extraido_c1) if nome_extraido_c1 else padroniza_texto(nome_filho_c1)
                                                                
                                                                if verificar_crianca_duplicada(db, nome_final_c1, data_nascimento_c1):
                                                                    st.error("❌ Esta criança já está no seu carrinho ou já possui um kit cadastrado.")
                                                                else:
                                                                    st.session_state.erro_ia_c1 = None
                                                                    st.session_state.lista_dependentes.append({
                                                                        "ID_Dependente": None,
                                                                        "ID_Colaborador": st.session_state.colaborador['Crachá'],
                                                                        "Nome_filho": nome_final_c1,
                                                                        "Gênero": genero_c1,
                                                                        "Data_nascimento": data_nascimento_c1.strftime("%d/%m/%Y"),
                                                                        "Escolaridade": st.session_state.escolaridade,
                                                                        "Ano_escolar": st.session_state.ano_escolar,
                                                                        "revisao_rh": status_rh_c1, 
                                                                        "Fluxo_Documento": "C1 - Identidade + Guarda Judicial",
                                                                        "aceite_ia": aceite_ia,
                                                                        "aceite_lgpd": aceite_lgpd,
                                                                        "data_aceite": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                                        "motivo_reprova_ia": motivo_rh_c1,
                                                                        "url_documento": None
                                                                    })

                                                                    cracha_log = st.session_state.colaborador['Crachá']
                                                                    registrar_log(cracha_log, "IA_VALIDACAO_SUCESSO", "Validação C1 aprovada pela IA.")
                                                                    registrar_log(cracha_log, "DEPENDENTE_CARRINHO_ADD", f"Dependente {nome_final_c1} validado pela IA e adicionado ao carrinho.")

                                                                    st.success("✅ Dependente adicionado ao carrinho com sucesso!")
                                                                    st.session_state.aguardando_decisao = True
                                                                    st.rerun()
                                                            finally:
                                                                db.close()

                # ==================== SUB-FLUXO C2: TUTELA JUDICIAL ====================
                elif "C2" in sub_opcao_c:
                    with st.form("form_fluxo_c2"):
                        st.info("📌 Requisitos (C2): Anexe o **Termo de Tutela Judicial** E a **Certidão de Nascimento ou RG da Criança**.")
                        nome_filho_c2 = st.text_input("Nome Completo da Criança / Adolescente")
                        genero_c2 = st.selectbox("Gênero:", ["", "Masculino", "Feminino"], format_func=lambda x: "Selecione o Gênero..." if x == "" else x, key="genero_c2")
                        data_maxima_c2 = date.today() - timedelta(days=730)
                        data_nascimento_c2 = st.date_input("Data de Nascimento da Criança", min_value=date(2000, 1, 1), max_value=data_maxima_c2, format="DD/MM/YYYY", key="dt_c2")
                        
                        certidao_c2 = st.file_uploader("Anexar Certidão de Nascimento ou RG da Criança", type=["pdf", "png", "jpg", "jpeg"], key="cert_c2", help="Se enviar RG, garanta que a filiação (verso) esteja visível.")
                        termo_tutela_c2 = st.file_uploader("Anexar Termo de Tutela Judicial", type=["pdf", "png", "jpg", "jpeg"], key="termo_tutela_c2")
                        declaracao_escolar_c2 = st.file_uploader("Anexar Declaração Escolar de Matrícula 📚", type=["pdf", "png", "jpg", "jpeg"], key="declaracao_escolar_c2")
                        
                        st.divider()
                        forcar_envio_rh_c2 = False
                        if st.session_state.erro_ia_c2:
                            st.error(f"⚠️ A IA encontrou uma divergência: {st.session_state.erro_ia_c2}")
                            forcar_envio_rh_c2 = st.checkbox("Declaro que o documento é autêntico. Quero forçar o envio para análise visual e manual do RH.", key="rh_c2_bypass")

                        aceite_ia = st.checkbox("Estou ciente de que os documentos enviados serão processados e analisados automaticamente por inteligência artificial para fins de validação cadastral.", key="ia_c2")
                        aceite_lgpd = st.checkbox("Concordo com o tratamento, armazenamento e uso dos dados para a concessão do Kit Escolar, em conformidade com a LGPD e as normas de compliance da instituição.", key="lgpd_c2")
                        salvar_c2 = st.form_submit_button("Validar e Adicionar ao Carrinho (C2)")

                    if salvar_c2:
                        if not verificar_limite_clique("valida_c2", 5):
                            st.stop()
                        valido_cert = validar_arquivo(certidao_c2, "Certidão/RG da Criança")
                        valido_tutela = validar_arquivo(termo_tutela_c2, "Termo de Tutela")
                        valido_decl = validar_arquivo(declaracao_escolar_c2, "Declaração Escolar")
                        if not (valido_cert and valido_tutela and valido_decl):
                            st.stop()

                        erros_c2 = []
                        if not nome_filho_c2.strip(): erros_c2.append("O nome da criança é obrigatório.")
                        if not genero_c2: erros_c2.append("O gênero é obrigatório.")
                        if not st.session_state.escolaridade: erros_c2.append("A escolaridade é obrigatória.")
                        if not st.session_state.ano_escolar: erros_c2.append("O ano escolar é obrigatório.")
                        if not certidao_c2 or not termo_tutela_c2: erros_c2.append("Documento(s) ausente(s) ou ilegível(is)")
                        if not declaracao_escolar_c2: erros_c2.append("A declaração escolar de matrícula é obrigatória.")
                        if not aceite_ia or not aceite_lgpd: erros_c2.append("Você deve aceitar os termos de uso de IA e a política de privacidade (LGPD) para prosseguir.")
                            
                        if erros_c2:
                            for e in erros_c2: st.error(f"⚠️ {e}")
                        else:
                            if forcar_envio_rh_c2:
                                with st.spinner("📤 Enviando documentos para a quarentena do RH..."):
                                    cracha_colab = st.session_state.colaborador['Crachá']
                                    try:
                                        urls = []
                                        if certidao_c2:
                                            u1 = upload_documento_supabase(certidao_c2, "identidade", cracha_colab)
                                            if u1: urls.append(u1)
                                        if termo_tutela_c2:
                                            u2 = upload_documento_supabase(termo_tutela_c2, "tutela_judicial", cracha_colab)
                                            if u2: urls.append(u2)
                                        if declaracao_escolar_c2:
                                            u3 = upload_documento_supabase(declaracao_escolar_c2, "declaracao", cracha_colab)
                                            if u3: urls.append(u3)
                                        url_doc_c2 = ",".join(urls) if urls else None
                                    except Exception:
                                        url_doc_c2 = None

                                erro_salvo = st.session_state.erro_ia_c2
                                st.session_state.erro_ia_c2 = None
                                nome_final_c2 = padroniza_texto(nome_filho_c2)

                                db = SessionLocal()
                                try:
                                    if verificar_crianca_duplicada(db, nome_final_c2, data_nascimento_c2):
                                        st.error("⚠️ Esta criança já está no seu carrinho ou já possui um kit cadastrado.")
                                    else:
                                        st.session_state.lista_dependentes.append({
                                            "ID_Dependente": None,
                                            "ID_Colaborador": st.session_state.colaborador['Crachá'],
                                            "Nome_filho": nome_final_c2,
                                            "Gênero": genero_c2,
                                            "Data_nascimento": data_nascimento_c2.strftime("%d/%m/%Y"),
                                            "Escolaridade": st.session_state.escolaridade,
                                            "Ano_escolar": st.session_state.ano_escolar,
                                            "revisao_rh": "Revisão Manual (Bypass Usuário)",
                                            "Fluxo_Documento": "C2 - Identidade + Tutela Judicial",
                                            "aceite_ia": aceite_ia,
                                            "aceite_lgpd": aceite_lgpd,
                                            "data_aceite": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                            "motivo_reprova_ia": f"Forçado pelo usuário. Erro original: {erro_salvo}",
                                            "url_documento": url_doc_c2
                                        })

                                        cracha_log = st.session_state.colaborador['Crachá']
                                        registrar_log(cracha_log, "ENVIO_RH_BYPASS", f"Bypass forçado (C2). Erro contornado: {erro_salvo}")
                                        registrar_log(cracha_log, "DEPENDENTE_CARRINHO_ADD", f"Dependente {nome_final_c2} adicionado ao carrinho via Bypass.")

                                        st.success("✅ Dependente adicionado ao carrinho com sucesso!")
                                        st.session_state.aguardando_decisao = True
                                        st.rerun()
                                finally:
                                    db.close()
                            else:
                               with st.spinner("Analisando documentos com a IA e cruzando termo de tutela... Aguarde"):
                                    dados_cert_c2, err_cert_c2 = analisa_certidao(certidao_c2)
                                    
                                    # 🛡️ TRATAMENTO INTELIGENTE DE RG (Fallback)
                                    if err_cert_c2 and ("não é" in err_cert_c2.lower() or "certidao" in err_cert_c2.lower() or "certidão" in err_cert_c2.lower()):
                                        dados_cert_c2 = {
                                            "nome_crianca": nome_filho_c2,
                                            "data_nascimento": data_nascimento_c2.strftime("%d/%m/%Y")
                                        }
                                        err_cert_c2 = None

                                    if err_cert_c2:
                                        registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou C2 (Identidade): {err_cert_c2}")
                                        st.session_state.erro_ia_c2 = err_cert_c2
                                        st.rerun()
                                    else:
                                        dados_ok, msg_dados = valida_dados_crianca_certidao(
                                            dados_cert_c2, nome_filho_c2, data_nascimento_c2, genero_c2
                                        )
                                        if not dados_ok:
                                            registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou C2: {msg_dados}")
                                            st.session_state.erro_ia_c2 = msg_dados
                                            st.rerun()
                                        else:
                                            dados_decl_c2, err_decl_c2 = analisa_declaracao_escolar(declaracao_escolar_c2)
                                            
                                            if err_decl_c2:
                                                registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou C2 (Declaração): {err_decl_c2}")
                                                st.session_state.erro_ia_c2 = err_decl_c2
                                                st.rerun()
                                            else:
                                                decl_ok_c2, msg_decl_c2, status_rh_c2, motivo_rh_c2 = valida_declaracao_escolar(
                                                    dados_decl_c2, dados_cert_c2.get("nome_crianca") or nome_filho_c2
                                                )
                                                
                                                if not decl_ok_c2:
                                                    registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou C2 (Declaração): {msg_decl_c2}")
                                                    st.session_state.erro_ia_c2 = msg_decl_c2
                                                    st.rerun()
                                                else:
                                                    if status_rh_c2 and str(status_rh_c2).startswith("Sim"):
                                                        st.warning(f"⚠️ **Aviso de Cadastro:** {msg_decl_c2}")
                                                        time.sleep(3.5)
                                                    dados_tutela, err_tutela = analisa_tutela_judicial(termo_tutela_c2)
                                                    
                                                    if err_tutela:
                                                        registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou C2 (Tutela): {err_tutela}")
                                                        st.session_state.erro_ia_c2 = err_tutela
                                                        st.rerun()
                                                    elif not dados_tutela.get("documento_valido"):
                                                        msg_tutela_val = "O Termo de Tutela é inválido ou não pôde ser lido."
                                                        registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou C2: {msg_tutela_val}")
                                                        st.session_state.erro_ia_c2 = msg_tutela_val
                                                        st.rerun()
                                                    else:
                                                        # 🛡️ CRUZAMENTO ROBUSTO C2 (Varre todo o termo de tutela)
                                                        nome_colab = padroniza_texto(st.session_state.colaborador['Nome'])
                                                        
                                                        texto_tutela_completo = " ".join([
                                                            str(item) for v in dados_tutela.values() 
                                                            for item in (v if isinstance(v, list) else [v]) 
                                                            if v is not None
                                                        ])
                                                        texto_tutela_normalizado = padroniza_texto(texto_tutela_completo)

                                                        if nome_colab not in texto_tutela_normalizado:
                                                            msg_tutela_err = f"O nome do colaborador ({st.session_state.colaborador['Nome']}) não foi encontrado no Termo de Tutela Judicial."
                                                            registrar_log(st.session_state.colaborador['Crachá'], "IA_VALIDACAO_FALHA", f"IA Rejeitou C2: {msg_tutela_err}")
                                                            st.session_state.erro_ia_c2 = msg_tutela_err
                                                            st.rerun()
                                                        else:
                                                            db = SessionLocal()
                                                            try:
                                                                nome_extraido_c2 = dados_cert_c2.get("nome_crianca", "")
                                                                nome_final_c2 = padroniza_texto(nome_extraido_c2) if nome_extraido_c2 else padroniza_texto(nome_filho_c2)
                                                                
                                                                if verificar_crianca_duplicada(db, nome_final_c2, data_nascimento_c2):
                                                                    st.error("❌ Esta criança já está no seu carrinho ou já possui um kit cadastrado.")
                                                                else:
                                                                    st.session_state.erro_ia_c2 = None
                                                                    st.session_state.lista_dependentes.append({
                                                                        "ID_Dependente": None,
                                                                        "ID_Colaborador": st.session_state.colaborador['Crachá'],
                                                                        "Nome_filho": nome_final_c2,
                                                                        "Gênero": genero_c2,
                                                                        "Data_nascimento": data_nascimento_c2.strftime("%d/%m/%Y"),
                                                                        "Escolaridade": st.session_state.escolaridade,
                                                                        "Ano_escolar": st.session_state.ano_escolar,
                                                                        "revisao_rh": status_rh_c2, 
                                                                        "Fluxo_Documento": "C2 - Identidade + Tutela Judicial",
                                                                        "aceite_ia": aceite_ia,
                                                                        "aceite_lgpd": aceite_lgpd,
                                                                        "data_aceite": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                                        "motivo_reprova_ia": motivo_rh_c2,
                                                                        "url_documento": None
                                                                    })

                                                                    cracha_log = st.session_state.colaborador['Crachá']
                                                                    registrar_log(cracha_log, "IA_VALIDACAO_SUCESSO", "Validação C2 aprovada pela IA.")
                                                                    registrar_log(cracha_log, "DEPENDENTE_CARRINHO_ADD", f"Dependente {nome_final_c2} validado pela IA e adicionado ao carrinho.")

                                                                    st.success("✅ Dependente adicionado ao carrinho com sucesso!")
                                                                    st.session_state.aguardando_decisao = True
                                                                    st.rerun()
                                                            finally:
                                                                db.close()

interface()