from database import engine
with engine.connect() as conn:
    print("✅ Conectado com sucesso!")