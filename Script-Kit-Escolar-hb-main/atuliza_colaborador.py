from conector_Postgre import SupabaseConnector
from conector_oracle import OracleConnector
from query import COLABORADOR
import pandas as pd 
from sqlalchemy import text 

oracle = OracleConnector()
oracle.conectar()
df = oracle.executar_query(COLABORADOR)
oracle.fechar_conexao()

postgre = SupabaseConnector()
engine_db = postgre.engine  # Pega a engine criada

try:
    print("📥 Subindo carga para a tabela temporária 'stg_colaboradores'...")
    
    # 1. Joga os dados novos do Oracle numa tabela temporária (Staging)
    df.to_sql(
        name='stg_colaboradores',
        con=engine_db,
        if_exists='replace',
        index=False,
        chunksize=10000,
        method='multi'
    )
    
    # 2. Executa o UPSERT: 
    query_upsert = """
        INSERT INTO colaboradores (
            cracha, 
            nome, 
            cpf,
            data_nascimento,
            descricao_situacao, 
            id_cargo,
            titulo_reduzido_cargo, 
            data_demissao,
            totp_secret, 
            totp_ativo
        )
        SELECT 
            s.cracha, 
            s.nome, 
            s.cpf,
            CAST(s.data_nascimento AS DATE),
            s.descricao_situacao, 
            s.id_cargo,
            s.titulo_reduzido_cargo, 
            CAST(s.data_demissao AS DATE),
            NULL AS totp_secret,
            FALSE AS totp_ativo
        FROM stg_colaboradores s
        ON CONFLICT (cracha) DO UPDATE SET
            nome = EXCLUDED.nome,
            cpf = EXCLUDED.cpf,
            data_nascimento = EXCLUDED.data_nascimento,
            descricao_situacao = EXCLUDED.descricao_situacao,
            id_cargo = EXCLUDED.id_cargo,
            titulo_reduzido_cargo = EXCLUDED.titulo_reduzido_cargo,
            data_demissao = EXCLUDED.data_demissao;
            
        DROP TABLE IF EXISTS stg_colaboradores
    """
    
    with engine_db.begin() as conn:
        conn.execute(text(query_upsert))
        
    print("✅ Carga sincronizada com sucesso preservando os dados de TOTP dos colaboradores!")

except Exception as e:
    print(f"❌ Erro ao sincronizar os colaboradores: {e}")
    raise e

finally:
    postgre.fechar_conexao()