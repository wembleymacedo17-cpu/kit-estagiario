# OracleConnector.py
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
import pandas as pd
from typing import Optional
from sqlalchemy.engine import URL
# ====================== CONFIGURAÇÃO ======================
# Carrega as variáveis do arquivo .env
load_dotenv()

class OracleConnector:
    """
    Classe simples para conectar no Oracle (VETORH)
    """
    
    def __init__(self):
        """Inicializa a conexão com as credenciais do .env"""
        self.user = os.getenv('ORACLE_USER')
        self.password = os.getenv('ORACLE_PASSWORD')
        self.host = os.getenv('ORACLE_HOST')
        self.port = os.getenv('ORACLE_PORT')
        self.service_name = os.getenv('ORACLE_SERVICE_NAME')
        self.schema = os.getenv('ORACLE_SCHEMA')
        
        self.engine = None  # Vai guardar a conexão
        
    def conectar(self):
        """Cria a conexão com o Oracle usando SQLAlchemy"""
        try:
            # URL.create monta a string do jeito correto e faz o escape seguro da senha com '#'
            connection_url = URL.create(
                "oracle+oracledb",
                username=self.user,
                password=self.password,
                host=self.host,
                port=self.port,
                query={"service_name": self.service_name} # Passa explicitamente como Serviço
            )
            
            self.engine = create_engine(
                connection_url,
                echo=False  # Mude para True se quiser ver os logs SQL
            )
            
            # Testa a conexão de verdade
            with self.engine.connect() as conns:
                pass
                
            print("✅ Conexão com Oracle realizada com sucesso!")
            return True
            
        except Exception as e:
            print(f"❌ Erro ao conectar no Oracle: {e}")
            return False

    def executar_query(self, query: str, chunksize: Optional[int] = None) -> pd.DataFrame:
        """
        Executa uma query SQL e retorna um DataFrame do Pandas
        
        Parâmetros:
            query (str): Query SQL completa
            chunksize (int): Se a tabela for muito grande, use chunksize (ex: 50000)
        """
        if not self.engine:
            print("⚠️  Você precisa chamar .conectar() primeiro!")
            return pd.DataFrame()
        
        try:
            print("🔄 Executando query...")
            df = pd.read_sql(
                query, 
                self.engine, 
                chunksize=chunksize
            )
            
            # Se estiver usando chunksize, concatena tudo
            if isinstance(df, pd.DataFrame):
                print(f"✅ Query executada com sucesso! Total de linhas: {len(df)}")
                return df
            else:
                # Tratamento para chunks
                df_list = list(df)
                df_final = pd.concat(df_list, ignore_index=True)
                print(f"✅ Query executada com chunks! Total de linhas: {len(df_final)}")
                return df_final
                
        except Exception as e:
            print(f"❌ Erro ao executar query: {e}")
            return pd.DataFrame()

    def fechar_conexao(self):
        """Fecha a conexão (boa prática)"""
        if self.engine:
            self.engine.dispose()
            print("🔌 Conexão encerrada.")



