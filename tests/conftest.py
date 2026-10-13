"""Configuração compartilhada dos testes."""
import os
import sys
from pathlib import Path

# Chave mínima exigida pelo Flask para assinar o cookie de sessão.
# Só vale para os testes e precisa ser definida ANTES de importar o app.
os.environ.setdefault("FLASK_SECRET_KEY", "chave-apenas-para-testes")

RAIZ = Path(__file__).resolve().parent.parent
for caminho in (RAIZ, Path(__file__).resolve().parent):
    if str(caminho) not in sys.path:
        sys.path.insert(0, str(caminho))

import pytest  # noqa: E402
from banco_falso import BancoFalso  # noqa: E402
from etl import processar_upload  # noqa: E402


@pytest.fixture
def banco(monkeypatch):
    """Troca a conexão real por um banco falso em memória."""
    falso = BancoFalso()
    monkeypatch.setattr(processar_upload, "get_engine", lambda: falso)
    return falso