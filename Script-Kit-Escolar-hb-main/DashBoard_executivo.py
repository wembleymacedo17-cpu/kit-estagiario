import streamlit as st
import pandas as pd
from datetime import datetime
import plotly.express as px 
import io
from database import engine

# Configuração da página
st.set_page_config(page_title="Painel Analitico - Kit Escolar", layout="wide")

# ========================== CARREGAMENTO E TRATAMENTO DE DADOS ==========================
@st.cache_data(ttl=300)
def carregar_dados():
    df_dep = pd.read_sql("SELECT * FROM dependentes", con=engine)
    df_colab = pd.read_sql("SELECT * FROM colaboradores", con=engine)
    df_kits = pd.read_sql("SELECT * FROM escolhas_kits", con=engine)
    return df_dep, df_colab, df_kits

df_dependentes, df_colaborador, df_escolhas_kits = carregar_dados()

# 1. Tratamento de Idades (Dependentes)
df_dependentes['data_nascimento'] = pd.to_datetime(df_dependentes['data_nascimento'], errors='coerce')
hoje = pd.to_datetime('today')
df_dependentes['idade'] = (hoje - df_dependentes['data_nascimento']).dt.days // 365
limites_dep = [0, 4, 7, 11, 15, 19]
rotulos_dep = ['0-3 anos', '4-6 anos', '7-10 anos', '11-14 anos', '15-18 anos']
df_dependentes['faixa_etaria'] = pd.cut(df_dependentes['idade'], bins=limites_dep, labels=rotulos_dep, right=False)

# 2. Tratamento de Idades (Colaboradores)
df_colaborador['data_nascimento'] = pd.to_datetime(df_colaborador['data_nascimento'], errors='coerce')
df_colaborador['idade'] = (hoje - df_colaborador['data_nascimento']).dt.days // 365
limites_colab = [16, 25, 35, 45, 55, 65, 100]
rotulos_colab = ['16-24 anos', '25-34 anos', '35-44 anos', '45-54 anos', '55-64 anos', '65+ anos']
df_colaborador['faixa_etaria'] = pd.cut(df_colaborador['idade'], bins=limites_colab, labels=rotulos_colab, right=False)

# 3. Agrupamento de Fluxos
def mapear_fluxo(texto):
    if not isinstance(texto, str): return 'Não Identificado'
    if any(x in texto for x in ['A1', 'A2', 'A3']): return 'Filho(a) Biológico(a) / Adotivo(a)'
    if any(x in texto for x in ['B1', 'B2']): return 'Enteado(a)'
    if any(x in texto for x in ['C1', 'C2']): return 'Guarda ou Tutela Judicial'
    if 'Estagiário' in texto: return 'Estagiário (Kit Próprio)'
    return 'Outros'

df_dependentes['fluxo_agrupado'] = df_dependentes['fluxo_documento'].apply(mapear_fluxo)


# ========================== BARRA LATERAL ÚNICA (MODELO 2) ==========================
if 'pagina_ativa' not in st.session_state:
    st.session_state.pagina_ativa = "Visão Geral"

st.sidebar.image("https://cdn-icons-png.flaticon.com/512/2942/2942269.png", width=80)
st.sidebar.title("Navegação")

botoes_menu = [
    ("📊 Visão Geral", "Visão Geral"),
    ("👥 Análise Demográfica", "Análise Demográfica"),
    ("📂 Canais e Escolaridade", "Canais e Escolaridade"),
    ("💰 Logística e Finanças", "Logística e Finanças"),
    ("🤖 Eficiência IA", "Eficiência IA")
]

for label, nome_pagina in botoes_menu:
    tipo_btn = "primary" if st.session_state.pagina_ativa == nome_pagina else "secondary"
    if st.sidebar.button(label, use_container_width=True, type=tipo_btn, key=f"btn_nav_{nome_pagina}"):
        st.session_state.pagina_ativa = nome_pagina
        st.rerun()

pagina_selecionada = st.session_state.pagina_ativa

st.sidebar.markdown("---")
st.sidebar.header("🔍 Filtros Globais")

generos_disponiveis = ["Todos"] + list(df_dependentes['genero'].dropna().unique())
# KEY ÚNICA PARA EVITAR ERRO DE DUPLICAÇÃO
genero_selecionado = st.sidebar.selectbox(
    "Filtrar por Gênero", 
    options=generos_disponiveis, 
    key="sb_filtro_genero_global"
)

# Aplicando os filtros no DataFrame base
df_filtrado = df_dependentes.copy()
if genero_selecionado != "Todos":
    df_filtrado = df_filtrado[df_filtrado['genero'] == genero_selecionado]

st.sidebar.markdown("---")
if st.sidebar.button("🔄 Atualizar Dados", use_container_width=True, key="btn_refresh_global"):
    st.cache_data.clear()
    st.rerun()

# Cálculos Globais
qtd_dependentes = len(df_filtrado)
qtd_colaboradores_com_dep = df_filtrado['id_colaborador'].nunique()

def calcular_documentos(fluxo):
    if pd.isna(fluxo): return 2
    return 3 if 'B1' in str(fluxo) else 2

docs_analisados = df_filtrado['fluxo_documento'].apply(calcular_documentos).sum()


# ========================== PÁGINA 1: VISÃO GERAL ==========================
if pagina_selecionada == "Visão Geral":
    st.title("📊 Visão Geral do Projeto")
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Dependentes Cadastrados", qtd_dependentes)
    col2.metric("Colaboradores Participantes", qtd_colaboradores_com_dep)
    col3.metric("Média Dep. / Colaborador", f"{(qtd_dependentes / qtd_colaboradores_com_dep):.2f}" if qtd_colaboradores_com_dep else "0")
    col4.metric("Documentos Analisados", docs_analisados)
    
    st.divider()
    
    df_filtrado['data_cadastro_dia'] = pd.to_datetime(df_filtrado['data_cadastro']).dt.date
    df_cadastros_dia = df_filtrado.groupby('data_cadastro_dia')['id_dependente'].count().reset_index()
    df_cadastros_dia.columns = ['Data', 'Cadastros']
    
    fig_linha = px.line(
        df_cadastros_dia.sort_values('Data'), x='Data', y='Cadastros', markers=True,
        title="Volume Diário de Cadastros (Evolução Temporal)"
    )
    fig_linha.update_traces(line_color='#1F77B4', line_width=3)
    st.plotly_chart(fig_linha, use_container_width=True)


# ========================== PÁGINA 2: ANÁLISE DEMOGRÁFICA ==========================
elif pagina_selecionada == "Análise Demográfica":
    st.title("👥 Análise Demográfica")
    
    visao = st.radio("Selecione o público para análise:", ["Dependentes", "Colaboradores"], horizontal=True, key="radio_visao_demo")
    st.divider()
    
    if visao == "Dependentes":
        col1, col2 = st.columns(2)
        with col1:
            df_genero = df_filtrado['genero'].value_counts().reset_index()
            df_genero.columns = ['Gênero', 'Quantidade']
            fig_genero = px.pie(df_genero, names='Gênero', values='Quantidade', hole=0.4, title="Distribuição por Gênero")
            st.plotly_chart(fig_genero, use_container_width=True)
            
            meninos = df_filtrado[df_filtrado['genero'] == 'Masculino'].shape[0]
            meninas = df_filtrado[df_filtrado['genero'] == 'Feminino'].shape[0]
            st.info(f"👦 **Masculino:** {meninos} cadastros | 👧 **Feminino:** {meninas} cadastros")
            
        with col2:
            df_faixa = df_filtrado['faixa_etaria'].value_counts().reset_index()
            df_faixa.columns = ['Faixa Etária', 'Quantidade']
            fig_faixa = px.bar(df_faixa.sort_values('Faixa Etária'), x='Faixa Etária', y='Quantidade', title="Faixa Etária (Dependentes)", text='Quantidade')
            fig_faixa.update_traces(textposition='outside')
            st.plotly_chart(fig_faixa, use_container_width=True)
            
    else:
        colab_participantes = df_colaborador[df_colaborador['cracha'].isin(df_filtrado['id_colaborador'])]
        
        df_faixa_colab = colab_participantes['faixa_etaria'].value_counts().reset_index()
        df_faixa_colab.columns = ['Faixa Etária', 'Quantidade']
        fig_faixa_colab = px.bar(df_faixa_colab.sort_values('Faixa Etária'), x='Faixa Etária', y='Quantidade', title="Faixa Etária (Colaboradores Participantes)", text='Quantidade')
        fig_faixa_colab.update_traces(textposition='outside')
        st.plotly_chart(fig_faixa_colab, use_container_width=True)


# ========================== PÁGINA 3: CANAIS E ESCOLARIDADE ==========================
elif pagina_selecionada == "Canais e Escolaridade":
    st.title("📂 Canais de Entrada e Perfil Escolar")
    
    col1, col2 = st.columns(2)
    
    with col1:
        df_fluxo = df_filtrado['fluxo_agrupado'].value_counts().reset_index()
        df_fluxo.columns = ['Vínculo Legal', 'Quantidade']
        fig_fluxo = px.bar(df_fluxo, x='Quantidade', y='Vínculo Legal', orientation='h', title="Cadastros por Vínculo (Fluxo)", text='Quantidade')
        fig_fluxo.update_traces(textposition='outside')
        st.plotly_chart(fig_fluxo, use_container_width=True)

    with col2:
        df_escolaridade = df_filtrado['escolaridade'].value_counts().reset_index()
        df_escolaridade.columns = ['Escolaridade', 'Quantidade']
        fig_escolaridade = px.bar(df_escolaridade, x='Escolaridade', y='Quantidade', title="Distribuição por Escolaridade", text='Quantidade')
        fig_escolaridade.update_traces(textposition='outside')
        st.plotly_chart(fig_escolaridade, use_container_width=True)
        
        df_mochilas_excel = df_escolhas_kits['kit_escolhido'].value_counts().reset_index()
        df_mochilas_excel.columns = ['Modelo da Mochila', 'Quantidade Solicitada']
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df_mochilas_excel.to_excel(writer, index=False, sheet_name='Mochilas')
        download_excel = buffer.getvalue()
        
        st.download_button(
            label="📥 Estratificar Dados (Baixar Excel)",
            data=download_excel,
            file_name="producao_mochilas.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            key="btn_download_excel"
        )
        
    st.divider()
    
    df_dep_cargo = pd.merge(df_filtrado, df_colaborador[['cracha', 'titulo_reduzido_cargo']], left_on='id_colaborador', right_on='cracha', how='left')
    df_cargos_rank = df_dep_cargo['titulo_reduzido_cargo'].value_counts().reset_index().head(10)
    df_cargos_rank.columns = ['Cargo', 'Dependentes']
    
    fig_cargos = px.bar(
        df_cargos_rank, 
        x='Dependentes', 
        y='Cargo', 
        orientation='h', 
        title="Top 10 Cargos dos Colaboradores Participantes", 
        text='Dependentes'
    )
    fig_cargos.update_layout(yaxis={'categoryorder': 'total ascending'})
    fig_cargos.update_traces(textposition='outside', textangle=0, textfont_size=14, cliponaxis=False)
    st.plotly_chart(fig_cargos, use_container_width=True)


# ========================== PÁGINA 4: LOGÍSTICA E FINANÇAS ==========================
elif pagina_selecionada == "Logística e Finanças":
    st.title("💰 Logística e Simulação Financeira")
    
    st.subheader("Simulador de Valores Unitários")
    col_sim1, col_sim2, col_sim3, col_sim4 = st.columns(4)
    preco_infantil = col_sim1.number_input("Infantil (R$)", min_value=0.0, value=80.0, step=5.0, key="num_infantil")
    preco_fund_1 = col_sim2.number_input("Fundamental I (R$)", min_value=0.0, value=110.0, step=5.0, key="num_f1")
    preco_fund_2 = col_sim3.number_input("Fundamental II (R$)", min_value=0.0, value=130.0, step=5.0, key="num_f2")
    preco_medio = col_sim4.number_input("Ensino Médio (R$)", min_value=0.0, value=150.0, step=5.0, key="num_medio")

    df_kits_completo = pd.merge(df_filtrado, df_escolhas_kits[['id_dependente', 'kit_escolhido']], on='id_dependente', how='inner')
    mapa_precos = {'Educação Infantil': preco_infantil, 'Ensino Fundamental I': preco_fund_1, 'Ensino Fundamental II': preco_fund_2, 'Ensino Médio': preco_medio}
    df_kits_completo['valor_unitario'] = df_kits_completo['escolaridade'].map(mapa_precos).fillna(0.0)

    custo_total = df_kits_completo['valor_unitario'].sum()
    total_kits = len(df_kits_completo)
    
    st.divider()
    col_f1, col_f2, col_f3 = st.columns(3)
    col_f1.metric("Custo Total Projetado", f"R$ {custo_total:,.2f}")
    col_f2.metric("Total de Mochilas Atribuídas", total_kits)
    col_f3.metric("Ticket Médio por Mochila", f"R$ {(custo_total / total_kits if total_kits else 0):,.2f}")

    col_g1, col_g2 = st.columns(2)
    with col_g1:
        df_gastos_nivel = df_kits_completo.groupby('escolaridade')['valor_unitario'].sum().reset_index()
        fig_gastos = px.bar(df_gastos_nivel, x='escolaridade', y='valor_unitario', title="Custo por Nível Escolar (R$)", text='valor_unitario')
        fig_gastos.update_traces(texttemplate='R$ %{text:,.2f}', textposition='outside')
        st.plotly_chart(fig_gastos, use_container_width=True)

    with col_g2:
        df_ranking = df_kits_completo['kit_escolhido'].value_counts().reset_index()
        df_ranking.columns = ['Modelo', 'Qtd']
        fig_ranking = px.bar(df_ranking, x='Qtd', y='Modelo', orientation='h', title="Ranking de Modelos Solicitados", text='Qtd')
        fig_ranking.update_layout(yaxis={'categoryorder': 'total ascending'})
        fig_ranking.update_traces(textposition='outside', textangle=0, cliponaxis=False)
        st.plotly_chart(fig_ranking, use_container_width=True)


# ========================== PÁGINA 5: EFICIÊNCIA IA ==========================
elif pagina_selecionada == "Eficiência IA":
    st.title("🤖 Eficiência do Sistema e Validações")
    
    def classificar_status(linha):
        rev = str(linha.get('revisao_rh', '')).strip()
        if rev.startswith('Não') or pd.isna(linha.get('revisao_rh')):
            return 'Processado pelo Sistema'
        else:
            return 'Analisado pelo RH'

    df_filtrado['status_processamento'] = df_filtrado.apply(classificar_status, axis=1)

    qtd_sistema = df_filtrado[df_filtrado['status_processamento'] == 'Processado pelo Sistema'].shape[0]
    
    taxa_sistema = (qtd_sistema / qtd_dependentes * 100) if qtd_dependentes > 0 else 0.0

    col1, col2, col3 = st.columns(3)
    col1.metric("Automação (Processado pelo Sistema)", f"{taxa_sistema:.1f}%")
    col2.metric("Intervenção (Analisado pelo RH)", f"{(100 - taxa_sistema):.1f}%")
    col3.metric("Mínimo de Docs Processados (OCR)", docs_analisados)
    
    st.divider()
    
    col_g1, col_g2 = st.columns(2)
    with col_g1:
        df_status = df_filtrado['status_processamento'].value_counts().reset_index()
        df_status.columns = ['Status', 'Quantidade']
        fig_status = px.pie(
            df_status, names='Status', values='Quantidade', hole=0.4, 
            title="Automação vs Esforço Manual", color='Status', 
            color_discrete_map={'Processado pelo Sistema': '#2ECC71', 'Analisado pelo RH': '#E74C3C'}
        )
        st.plotly_chart(fig_status, use_container_width=True)

    with col_g2:
        df_reprovacoes = df_filtrado[df_filtrado['motivo_reprova_ia'].notnull() & (df_filtrado['motivo_reprova_ia'] != '')]['motivo_reprova_ia'].value_counts().reset_index()
        df_reprovacoes.columns = ['Motivo Completo', 'Quantidade']
        
        df_reprovacoes['Motivo Resumido'] = df_reprovacoes['Motivo Completo'].apply(lambda x: str(x)[:65] + '...' if len(str(x)) > 65 else str(x))
        
        if not df_reprovacoes.empty:
            fig_reprova = px.bar(
                df_reprovacoes, 
                x='Quantidade', 
                y='Motivo Resumido', 
                orientation='h', 
                title="Principais Gatilhos de Quarentena", 
                text='Quantidade',
                custom_data=['Motivo Completo']
            )
            fig_reprova.update_layout(yaxis={'categoryorder': 'total ascending'})
            
            fig_reprova.update_traces(
                textposition='outside', 
                textangle=0, 
                textfont_size=14, 
                cliponaxis=False,
                hovertemplate="<b>Motivo Completo:</b> %{customdata[0]}<br><b>Quantidade:</b> %{x}"
            )
            st.plotly_chart(fig_reprova, use_container_width=True)
        else:
            st.info("Nenhuma reprovação registrada nos filtros atuais.")