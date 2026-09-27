# config/database.py
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

# Só existe quando rodando no Cloud Run (formato: projeto:regiao:instancia)
INSTANCE_CONNECTION_NAME = os.getenv("INSTANCE_CONNECTION_NAME")


def get_engine():
    if INSTANCE_CONNECTION_NAME:
        # Rodando no Cloud Run: conecta via socket Unix do Cloud SQL,
        # sem precisar de IP público liberado nem host/porta.
        socket_path = f"/cloudsql/{INSTANCE_CONNECTION_NAME}"
        database_url = (
            f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@/{DB_NAME}"
            f"?host={socket_path}"
        )
    else:
        # Rodando localmente: conexão TCP normal, com o driver explícito
        # (psycopg2), em vez de depender do driver padrão do SQLAlchemy —
        # que mudou de versão para versão e causou esse mesmo bug.
        db_host = os.getenv("DB_HOST")
        db_port = os.getenv("DB_PORT")
        database_url = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{db_host}:{db_port}/{DB_NAME}"

    return create_engine(database_url)