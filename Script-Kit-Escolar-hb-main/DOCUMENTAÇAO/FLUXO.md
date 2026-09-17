[ 1. BUSCA DE CRACHÁ & AUTENTICAÇÃO 2FA ]
   │
   ├─► Colaborador digita o número do crachá e clica em "Buscar".
   ├─► Sistema consulta o banco de dados Amazon RDS/PostgreSQL.
   │      ├─ Valida status (descarte para "Desligado", "Aposentadoria p/Invalidez").
   │      └─ Valida cargo no filtro de cargos bloqueados (`CARGOS_REJEITADO`).
   │
   ├─► 🛡️ Trava de Segurança 2FA (TOTP):
          └─ Validação por Cpf e Data nascimento
   │      └─ Se o colaborador não estiver autenticado, exige a validação do token TOTP antes de prosseguir.
   │
   ├─► 🎓 Roteamento por Cargo:
   │      └─ Se o cargo for Estagiário (IDs 600, 601, 602, 5001) ──► Encaminha direto para o [FLUXO ESTAGIÁRIO].
   └─► Se válido e autenticado: Exibe a Ficha do Colaborador (Nome, Cargo, Situação).


[ 2. GESTÃO DE CADASTROS EXISTENTES & CONTATO ]
   │
   ├─► Verificação de Dependentes Existentes no Banco:
   │      ├─ STATUS EM ANÁLISE RH: Se houver dependente pendente de revisão ──► Exibe aviso e libera apenas o botão "➕ Adicionar Mais dependentes".
   │      └─ STATUS APROVADO: Se todos estiverem validados ──► Exibe 3 opções:
   │            ├─ 🎒 "Mudar escolha do Kit" (editar_kits_existentes)
   │            ├─ ➕ "Adicionar Mais dependentes"
   │            └─ 🎟️ "Buscar QR CODE" (Renderiza/Baixa o QR Code direto do S3)
   │
   └─► Formulário de Contato (se for novo cadastro):
          ├─ Campo E-mail + Confirmação de E-mail (exige e-mail pessoal: Gmail, Hotmail, Yahoo, etc.).
          ├─ Número de Telefone WhatsApp (validação de DDD e 11 dígitos).
          └─ Ao salvar, oculta as buscas anteriores e ativa o Carrinho de Dependentes na Sidebar.


[ 3. REGRAS GERAIS DE VALIDAÇÃO DE ARQUIVOS E IA ]
   │
   ├─► ⏱️ Rate Limiter: Bloqueia cliques excessivos (máximo 5 tentativas por ação).
   ├─► 📁 Validação de Anexos: Garante limite máximo por arquivo (MB) e extensões permitidas (PDF, PNG, JPG, JPEG).
   ├─► 🤖 IA Gemini Multi-Modelo (Fallback & Backoff):
   │      ├─ Tenta processar o documento no modelo principal do Gemini.
   │      └─ Em caso de erro 503/429 ou indisponibilidade, realiza retries automáticos e alterna para o modelo de backup.
   │
   └─► 🚨 Mecanismo de Quarentena/Bypass RH:
          ├─ Se a IA apontar divergência ou rejeitar o documento, o sistema habilita a caixa de seleção de Bypass.
          ├─ Caso marcado pelo usuário, realiza upload automático dos documentos para o Bucket S3 da AWS.
          └─ Salva a solicitação com `revisao_rh = "Revisão Manual (Bypass Usuário)"` e registra em log de auditoria.


[ 4. TRIAGEM DE VÍNCULO & FLUXOS DE CADASTRO ]

   ├──► FLUXO ESTAGIÁRIO: Kit Próprio
   │      ├─ Formulário: Escolaridade, Ano/Semestre, Gênero e Data de Nascimento.
   │      ├─ Documento: Anexa Certidão de Nascimento ou RG.
   │      └─ IA Gemini valida identidade e insere o kit no carrinho sem necessidade de vinculo de filiação.

   ├──► OPÇÃO A: Filho(a) Biológico(a) ou Adotivo(a)
   │      ├─ A1: Certidão de Nascimento/RG + Declaração Escolar de Matrícula 📚
   │      │     └─ IA Gemini + Regras validam: Validade/legibilidade, dados da criança e nome do colaborador na filiação + dados e autenticidade da Declaração Escolar.
   │      ├─ A2: Certidão de Nascimento com Averbação de Adoção + Declaração Escolar 📚
   │      │     └─ IA Gemini + Regras validam: Averbação explícita de adoção, dados da criança, Declaração Escolar e colaborador como pai/mãe adotivo(a).
   │      └─ A3: Termo de Guarda para Fins de Adoção (Judicial) + Declaração Escolar 📚
   │            └─ IA Gemini + Regras validam: Origem judicial, finalidade explícita de adoção, dados da criança, Declaração Escolar e colaborador como guardião.

   ├──► OPÇÃO B: Enteado(a) (Registrado no nome do cônjuge/companheiro)
   │      ├─ B1: União Estável (Firma Reconhecida) + Certidão/RG Criança + Declaração Escolar 📚
   │      │     └─ IA Gemini + Regras validam: Firma reconhecida/selo de cartório, Declaração Escolar, dados da criança e presença do colaborador na União Estável.
   │      └─ B2: Certidão de Casamento + Certidão/RG Criança + Declaração Escolar 📚
   │            └─ IA Gemini + Regras validam: Casamento válido sem divórcio, Declaração Escolar, dados da criança e cruzamento de nomes no documento de casamento.

   └──► OPÇÃO C: Criança ou Adolescente sob Guarda ou Tutela Judicial
          ├─ C1: Termo/Certidão de Guarda Judicial + Certidão/RG Criança + Declaração Escolar 📚
          │     └─ IA Gemini + Regras validam: Autenticidade judicial, Declaração Escolar, dados da criança e colaborador nomeado como GUARDIÃO.
          └─ C2: Termo de Tutela Judicial + Certidão/RG Criança + Declaração Escolar 📚
                └─ IA Gemini + Regras validam: Autenticidade judicial, Declaração Escolar, dados da criança e colaborador nomeado como TUTOR.


[ 5. CARRINHO DE DEPENDENTES E DECISÃO ]
   │
   ├─► Verificação de Criança Duplicada: Bloqueia cadastros repetidos (Nome + Data de Nascimento) no carrinho e no BD.
   ├─► Após adicionar o dependente:
   │      ├─ "Adicionar outro dependente/kit" ──► Retorna à triagem.
   │      └─ "Finalizar carrinho e escolher kits" ──► Grava os dependentes no banco (`dependentes`) e avança para a escolha dos kits.


[ 6. ESCOLHA DOS KITS ESCOLARES & TERMO DE ESTOQUE ]
   │
   ├─► Vitrine Visual de Kits:
   │      ├─ Carrega o catálogo específico da escolaridade de cada filho (Infantil, Fundamental I/II, Médio).
   │      └─ Apresenta galeria de imagens dinâmicas (4 por linha) com seleção via checkbox único por dependente.
   │
   └─► Termo de Ciência e Variação de Estoque:
          ├─ Apresenta aviso sobre possíveis variações de cores/acabamento conforme estoque na data da entrega.
          ├─ Requer confirmação obrigatória ("Estou ciente").
          └─ Salva o registro na tabela `escolha_kit` (`aceite_variacao_kit=True`).


[ 7. GERAÇÃO DE QR CODE, AWS S3 & ENVIO DE E-MAIL ]
   │
   ├─► Gera um código único de retirada (`codigo_retirada` via UUID) e registra na tabela `retiradas`.
   ├─► Renderiza a imagem do QR Code na tela e disponibiliza botão de download em PNG.
   ├─► Upload em Nuvem (AWS S3):
   │      └─ Salva a imagem do QR Code no bucket S3 configurado (`S3_BUCKET_QRCODES`).
   ├─► Disparo por E-mail:
   │      └─ Dispara e-mail automático via SMTP (`NotificadorEmail`) contendo o QR Code em anexo e o código de retirada.
   └─► Log de Auditoria: Registra todas as ações e eventos críticos no histórico do sistema (`registrar_log`).