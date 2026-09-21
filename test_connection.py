from sqlalchemy import text
from config.database import get_engine

engine = get_engine()

with engine.connect() as conn:
    resultado = conn.execute(text("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public'
    """))
    tabelas = [row[0] for row in resultado]

print("Conexão bem-sucedida!")
print("Tabelas encontradas:", tabelas)