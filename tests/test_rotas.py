"""Testes das rotas do Flask (sem banco): contrato HTTP e isolamento por sessão."""
import io

import pytest

import app as app_modulo


@pytest.fixture
def cliente():
    app_modulo.app.config["TESTING"] = True
    return app_modulo.app.test_client()


def definir_sessao(cliente, sessao_id="sessao-teste"):
    with cliente.session_transaction() as sessao:
        sessao["sessao_id"] = sessao_id


def enviar_planilha(cliente):
    return cliente.post(
        "/upload",
        data={"arquivo": (io.BytesIO(b"conteudo"), "dados.xlsx")},
        content_type="multipart/form-data",
    )


# ------------------------------------------------------------------- páginas

def test_pagina_inicial_abre(cliente):
    resposta = cliente.get("/")

    assert resposta.status_code == 200
    assert "Recomendação" in resposta.get_data(as_text=True)


def test_planilha_modelo_e_baixada_como_anexo(cliente):
    resposta = cliente.get("/planilha-modelo")

    assert resposta.status_code == 200
    assert "attachment" in resposta.headers["Content-Disposition"]
    assert resposta.data[:2] == b"PK"   # .xlsx é um zip
    resposta.close()


# -------------------------------------------------------------------- upload

def test_upload_sem_arquivo_e_recusado(cliente):
    resposta = cliente.post("/upload")

    assert resposta.status_code == 400
    assert "Nenhum arquivo" in resposta.get_json()["erro"]


def test_upload_valido_devolve_contagens_e_abre_a_sessao(cliente, monkeypatch):
    monkeypatch.setattr(app_modulo, "processar_planilha_upload", lambda arquivo, sessao: (sessao, 3, 1, None))

    resposta = enviar_planilha(cliente)

    assert resposta.status_code == 200
    dados = resposta.get_json()
    assert dados["total_validos"] == 3
    assert dados["total_rejeitados"] == 1
    assert dados["sessao_id"]


def test_upload_com_planilha_invalida_devolve_400_com_o_motivo(cliente, monkeypatch):
    def recusa(arquivo, sessao):
        raise ValueError("Colunas obrigatórias ausentes na planilha: {'Preco_Unitario'}")

    monkeypatch.setattr(app_modulo, "processar_planilha_upload", recusa)

    resposta = enviar_planilha(cliente)

    assert resposta.status_code == 400
    assert "Preco_Unitario" in resposta.get_json()["erro"]


def test_erro_inesperado_no_upload_vira_500_com_mensagem(cliente, monkeypatch):
    def quebra(arquivo, sessao):
        raise RuntimeError("falha qualquer")

    monkeypatch.setattr(app_modulo, "processar_planilha_upload", quebra)

    resposta = enviar_planilha(cliente)

    assert resposta.status_code == 500
    assert "Erro ao processar a planilha" in resposta.get_json()["erro"]


# ------------------------------------------------------------------- produtos

def test_produtos_sem_upload_previo_e_recusado(cliente):
    resposta = cliente.get("/produtos")

    assert resposta.status_code == 400


def test_produtos_usa_a_sessao_aberta_pelo_upload(cliente, monkeypatch):
    vistos = []
    monkeypatch.setattr(app_modulo, "processar_planilha_upload", lambda arquivo, sessao: (sessao, 1, 0, None))
    monkeypatch.setattr(app_modulo, "listar_produtos_cadastrados", lambda sessao: vistos.append(sessao) or ["ACIDO X"])

    sessao_do_upload = enviar_planilha(cliente).get_json()["sessao_id"]
    resposta = cliente.get("/produtos")

    assert resposta.status_code == 200
    assert resposta.get_json() == {"produtos": ["ACIDO X"]}
    assert vistos == [sessao_do_upload]


def test_cada_usuario_enxerga_so_a_propria_sessao(monkeypatch):
    vistos = {}
    monkeypatch.setattr(app_modulo, "processar_planilha_upload", lambda arquivo, sessao: (sessao, 1, 0, None))

    def listar(sessao):
        vistos.setdefault("consultas", []).append(sessao)
        return []

    monkeypatch.setattr(app_modulo, "listar_produtos_cadastrados", listar)

    maria = app_modulo.app.test_client()
    joao = app_modulo.app.test_client()
    visitante = app_modulo.app.test_client()

    sessao_maria = enviar_planilha(maria).get_json()["sessao_id"]
    sessao_joao = enviar_planilha(joao).get_json()["sessao_id"]
    maria.get("/produtos")
    joao.get("/produtos")

    assert sessao_maria != sessao_joao
    assert vistos["consultas"] == [sessao_maria, sessao_joao]
    assert visitante.get("/produtos").status_code == 400


# -------------------------------------------------------------------- ranking

def test_ranking_sem_sessao_e_recusado(cliente):
    resposta = cliente.post("/ranking", json={"produto": "ACIDO X"})

    assert resposta.status_code == 400


def test_ranking_sem_produto_e_recusado(cliente):
    definir_sessao(cliente)

    assert cliente.post("/ranking", json={}).status_code == 400
    assert cliente.post("/ranking").status_code == 400


TODOS = [
    {"fornecedor": "A", "tco_estimado": 10.0, "total_pedidos": 30},
    {"fornecedor": "B", "tco_estimado": 12.0, "total_pedidos": 25},
    {"fornecedor": "C", "tco_estimado": 15.0, "total_pedidos": 12},
    {"fornecedor": "D", "tco_estimado": 21.0, "total_pedidos": 8},
]

TENDENCIA = {
    "preco_referencia": 43.11,
    "media_historica": 26.0,
    "variacao_percentual": 65.8,
    "confiavel": True,
    "modelo": "Random Forest",
    "r2": 0.42,
    "motivo": None,
    "total_pedidos": 105,
    "minimo_pedidos": 70,
    "data_referencia": "2026-07-27",
    "serie_mensal": [{"mes": "2026-07", "preco_medio": 42.0, "pedidos": 3}],
}


def test_ranking_valido_devolve_o_resultado_da_sessao(cliente, monkeypatch):
    chamadas = []

    def falso(produto, sessao, top_n=3):
        chamadas.append((produto, sessao, top_n))
        return TODOS, TENDENCIA

    monkeypatch.setattr(app_modulo, "calcular_ranking", falso)
    definir_sessao(cliente, "sessao-da-maria")

    resposta = cliente.post("/ranking", json={"produto": "ACIDO X"})

    assert resposta.status_code == 200
    dados = resposta.get_json()
    assert dados["produto"] == "ACIDO X"
    assert dados["tendencia"] == TENDENCIA
    assert dados["ranking"] == TODOS[:3]
    # o modelo é consultado uma vez só, pedindo todos os fornecedores
    assert chamadas == [("ACIDO X", "sessao-da-maria", None)]


def test_ranking_completo_traz_todos_com_posicao_e_so_o_tco(cliente, monkeypatch):
    todos = TODOS
    monkeypatch.setattr(app_modulo, "calcular_ranking", lambda produto, sessao, top_n=3: (todos, TENDENCIA))
    definir_sessao(cliente)

    dados = cliente.post("/ranking", json={"produto": "ACIDO X"}).get_json()

    assert dados["ranking_completo"] == [
        {"posicao": 1, "fornecedor": "A", "tco_estimado": 10.0},
        {"posicao": 2, "fornecedor": "B", "tco_estimado": 12.0},
        {"posicao": 3, "fornecedor": "C", "tco_estimado": 15.0},
        {"posicao": 4, "fornecedor": "D", "tco_estimado": 21.0},
    ]


def test_ranking_de_produto_inexistente_devolve_400(cliente, monkeypatch):
    def sem_pedidos(produto, sessao, top_n=3):
        raise ValueError("Nenhum pedido encontrado")

    monkeypatch.setattr(app_modulo, "calcular_ranking", sem_pedidos)
    definir_sessao(cliente)

    resposta = cliente.post("/ranking", json={"produto": "INEXISTENTE"})

    assert resposta.status_code == 400
    assert "Nenhum pedido" in resposta.get_json()["erro"]


def test_erro_inesperado_no_ranking_vira_500_com_mensagem(cliente, monkeypatch):
    def quebra(produto, sessao, top_n=3):
        raise RuntimeError("falha qualquer")

    monkeypatch.setattr(app_modulo, "calcular_ranking", quebra)
    definir_sessao(cliente)

    resposta = cliente.post("/ranking", json={"produto": "ACIDO X"})

    assert resposta.status_code == 500
    assert "Erro ao calcular ranking" in resposta.get_json()["erro"]