Mesma observação de antes: nunca confiar 100% na resposta do modelo para decisão crítica sem validação humana — para um sistema de benefícios, recomendo manter um fallback de revisão manual quando o modelo não tiver certeza
Trate erros de rede/timeout — formulário não pode travar se a API cair
Sanitize a resposta antes de fazer json.loads, modelos às vezes retornam texto extra junto do JSON












--------------------------------------------------- passo a passo api
1. Acesso ao Google AI Studio
O Google AI Studio é a interface de desenvolvimento mais rápida para prototipar e obter suas credenciais.

Acesse aistudio.google.com e faça login com sua conta Google.

Aceite os termos de serviço caso seja seu primeiro acesso.

2. Geração da API Key
Para que seu código Python consiga se comunicar com a API, você precisa de uma chave de autenticação.

No menu lateral esquerdo do AI Studio, clique em "Get API key".

Clique no botão "Create API key".

Você pode criar uma chave vinculada a um projeto existente do Google Cloud (se já tiver um configurado para sua infraestrutura) ou deixar que o AI Studio crie um novo projeto automaticamente.

Ação imediata: Copie essa chave e guarde-a em um arquivo .env no seu projeto local (ex: GEMINI_API_KEY="sua_chave_aqui"). Nunca coloque essa chave diretamente hardcoded no seu arquivo .py.

3. Escolha do Modelo Adequado
Para leitura de PDFs e extração de entidades (nomes), a escolha do modelo impacta custo e velocidade:

Gemini 1.5 Flash: É a escolha recomendada para começar. É extremamente rápido, mais barato e excelente em tarefas de extração direta de documentos.

Gemini 1.5 Pro: Use este apenas se o PDF for extremamente complexo (muitas páginas de texto denso, formatação muito confusa) e o Flash apresentar alucinações.

4. Prototipação do Prompt (O "Test Drive")
Antes de ir para o Python, simule exatamente o que seu script fará usando a interface do AI Studio. Isso economiza horas de debug.

Clique em "Create New Prompt" e escolha "Chat prompt" ou "Freeform prompt".

Faça o upload de um PDF de teste usando o botão de "+" (Insert).

Escreva as System Instructions (Instruções do Sistema). Como você fará uma validação no banco de dados posteriormente, é crucial forçar o modelo a responder em um formato estruturado, como JSON, em vez de texto livre.

Exemplo de Prompt:

"Analise o documento anexado. Localize o nome do pai e o nome da mãe. Retorne EXCLUSIVAMENTE um objeto JSON no seguinte formato: {"nome_pai": "Nome Encontrado ou null", "nome_mae": "Nome Encontrado ou null"}. Não adicione markdown ou explicações adicionais."

Execute o teste na interface e ajuste as palavras do prompt até que o JSON retorne perfeitamente e sem lixo no texto.

5. Configuração do Formato de Resposta (Structured Output)
No canto direito da tela do AI Studio, nas configurações do modelo (Model Settings), procure por "Response MIME Type" ou configurações de esquema (Schema).

Altere de text/plain para application/json.

Ao garantir isso na interface, você saberá exatamente quais parâmetros passar na função do SDK do Python (response_mime_type="application/json") para blindar seu pipeline contra respostas fora do padrão.