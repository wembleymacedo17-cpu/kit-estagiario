CREATE TABLE colaboradores (
    id BIGSERIAL PRIMARY KEY,
    cracha BIGINT UNIQUE NOT NULL,
    nome TEXT NOT NULL,
    descricao_situacao TEXT,
    titulo_reduzido_cargo TEXT,
    data_demissao DATE,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    situacao INTEGER
);
------------------------------------------------------------------------------------------------------------

CREATE TABLE dependentes (
    id_dependente SERIAL PRIMARY KEY,
    id_colaborador BIGINT NOT NULL,
    nome_filho TEXT NOT NULL,
    data_nascimento DATE,
    genero VARCHAR(50),
    escolaridade VARCHAR(100),
    ano_escola VARCHAR(50),
    data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    revisao_rh VARCHAR(50),
    
    -- Chave Estrangeira: Liga o dependente ao colaborador na tabela base
    CONSTRAINT fk_colaborador_dependente
        FOREIGN KEY(id_colaborador) 
        REFERENCES colaboradores(id) 
        ON DELETE CASCADE
);

-- Índice Único: Regra de Duplicidade (impede cadastrar o mesmo filho duas vezes)
CREATE UNIQUE INDEX idx_dependente_unico 
ON dependentes (id_colaborador, nome_filho);

-------------------------------------------------------------------------------------------------------------------------------

CREATE TABLE escolhas_kits (
    id_escolha SERIAL PRIMARY KEY,
    id_colaborador BIGINT NOT NULL,
    id_dependente BIGINT NOT NULL,
    kit_escolhido VARCHAR(150) NOT NULL,
    data_escolha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Chave Estrangeira: Liga a escolha ao colaborador
    CONSTRAINT fk_escolha_colaborador
        FOREIGN KEY(id_colaborador) 
        REFERENCES colaboradores(id)
        ON DELETE CASCADE,
        
    -- Chave Estrangeira: Liga a escolha ao dependente exato
    CONSTRAINT fk_escolha_dependente
        FOREIGN KEY(id_dependente) 
        REFERENCES dependentes(id_dependente)
        ON DELETE CASCADE,
        
    -- Regra de Duplicidade: Um dependente só pode ter uma única escolha de kit vinculada a ele
    CONSTRAINT uk_dependente_kit UNIQUE (id_dependente)
);

----------------------------------------------------------------------------------------------------------------------------------------------------------------
CREATE TABLE retiradas (
    id_retirada SERIAL PRIMARY KEY,
    codigo_retirada VARCHAR(255) UNIQUE NOT NULL, 
    id_colaborador BIGINT NOT NULL,
    email VARCHAR(150),
    telefone VARCHAR(20),
    qtd_kits INTEGER NOT NULL,
    resumo_kits TEXT,
    status VARCHAR(20) DEFAULT 'PENDENTE' NOT NULL,
    data_geracao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    data_entrega TIMESTAMP, 
    
    -- Chave Estrangeira: Liga a retirada ao colaborador solicitante
    CONSTRAINT fk_retirada_colaborador
        FOREIGN KEY(id_colaborador) 
        REFERENCES colaboradores(id)
        ON DELETE CASCADE,
        
    -- Regra de Status: O banco de dados SÓ aceita esses dois valores (bloqueando erros da aplicação)
    CONSTRAINT chk_status_retirada 
        CHECK (status IN ('PENDENTE', 'ENTREGUE'))
);-------------------------------------------------------------------------------------------------------