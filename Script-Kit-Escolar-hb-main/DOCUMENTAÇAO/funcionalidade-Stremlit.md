1. Textos e Títulos (Organização visual)
st.title('Monitoramento do ETL') = Serve para escrever o título principal e maior da sua página.

st.header('Extração de Dados') = Serve para criar um cabeçalho de seção (tamanho grande, mas menor que o title).

st.subheader('Registros da tabela') = Serve para criar um subtítulo (tamanho médio), ótimo para dividir etapas de processamento.

st.write('Pipeline finalizado com sucesso.') = É o "faz-tudo". Serve para escrever textos, imprimir variáveis do Python ou até mesmo renderizar dicionários.

st.markdown('Atenção: Verifique os logs.') = Serve para escrever textos aceitando formatação Markdown (negrito, listas, links).

2. Exibição de Dados (O coração do seu trabalho)
st.dataframe(df_pandas) = Serve para exibir um DataFrame (ou o resultado de uma query) de forma interativa. O usuário pode rolar a barra e ordenar as colunas.

st.table(df_pandas.head(10)) = Serve para exibir os dados de forma estática. É mais limpo, ideal para tabelas muito pequenas ou resumos.

st.metric(label="Linhas Duplicadas Removidas", value="1.250", delta="-50") = Serve para exibir indicadores de performance (KPIs) com estilo de dashboard.

3. Entradas e Interação (Widgets)
st.button('Executar Limpeza') = Serve para criar um botão clicável. Se o usuário clicar, ele retorna True no Python e dispara uma ação.

st.selectbox('Selecione o Banco de Dados', ['PostgreSQL', 'Oracle']) = Serve para criar um menu suspenso (dropdown) para o usuário escolher uma única opção.

st.text_input('Digite a query SQL:') = Serve para criar uma caixa de texto livre para o usuário digitar informações.

st.file_uploader('Faça o upload do arquivo CSV') = Serve para criar uma área onde o usuário pode arrastar e soltar arquivos para o Python processar.

st.checkbox('Mostrar dados brutos') = Serve para criar uma caixa de seleção (liga/desliga).

4. Layout (Deixando profissional)
st.sidebar.title('Filtros') = Ao adicionar .sidebar antes de qualquer comando (ex: st.sidebar.selectbox), você joga aquele elemento para uma barra lateral escura, deixando o centro da tela limpo para os dados.

col1, col2 = st.columns(2) = Serve para dividir a tela verticalmente. Você pode colocar a tabela do banco de dados na col1 e um gráfico na col2, lado a lado.

aba1, aba2 = st.tabs(['Geral', 'Detalhes da coluna order_time']) = Serve para criar abas de navegação dentro da mesma tela, excelente para organizar muita informação sem poluir o visual.

5. Status e Feedback (Para os usuários da ferramenta)
st.success('Dados validados com sucesso!') = Serve para exibir uma faixa verde de sucesso.

st.error('Falha na conexão.') = Serve para exibir uma faixa vermelha de erro.

st.warning('Atenção: Valores nulos detectados.') = Serve para exibir uma faixa amarela de alerta.

st.spinner('Carregando dados do S3...') = Serve para exibir um ícone de carregamento ("rodinha" girando) enquanto o seu código Python estiver rodando uma tarefa pesada.




Entradas de Texto e Número
st.text_input('Nome do Cliente') = Serve para o usuário digitar um texto curto (ex: nome, CPF, nome da tabela).

st.text_area('Descreva o problema') = Serve para criar uma caixa de texto maior, ideal para comentários ou digitar queries SQL longas.

st.number_input('Idade do Cliente', min_value=18, max_value=100) = Serve para o usuário digitar apenas números (inteiros ou decimais).

Entradas de Data e Hora
st.date_input('Data de Nascimento') = Serve para abrir um calendário onde o usuário escolhe uma data (como a order_time).

st.time_input('Horário da execução') = Serve para o usuário selecionar uma hora específica.

Escolhas e Seleções
st.selectbox('Selecione o Estado', ['SP', 'RJ', 'MG']) = Serve para criar um menu suspenso onde o usuário escolhe apenas uma opção.

st.radio('Gênero', ['Masculino', 'Feminino']) = Serve para exibir "bolinhas" de seleção. O usuário também só pode escolher uma opção, mas todas ficam visíveis na tela.

st.multiselect('Cursos de interesse', ['Power BI', 'Databricks', 'Python']) = Serve para o usuário escolher várias opções de uma lista, criando tags.

st.checkbox('Aceito os termos e condições') = Serve para uma caixa de marcação simples de Sim/Não (True/False).