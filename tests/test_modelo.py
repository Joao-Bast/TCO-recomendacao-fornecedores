"""Validação do modelo de previsão de preço (linha de IA): regra dos 70 pedidos,
critério de confiabilidade, reprodutibilidade e formato do retorno."""
import json

import numpy as np
import pandas as pd
import pytest

from modelo import previsao_preco
from modelo.previsao_preco import (
    MINIMO_PEDIDOS_PARA_TREINAR,
    preparar_features,
    prever_preco_atual,
    treinar_e_avaliar,
)

SESSAO = "sessao-teste"


def historico(n, tendencia=0.0, ruido=0.0, semente=0):
    """Histórico sintético: preço = 20 + tendência × dias + ruído, um pedido a cada 3 dias."""
    rng = np.random.default_rng(semente)
    dias = np.arange(n) * 3
    preco = 20 + tendencia * dias
    if ruido:
        preco = preco + rng.normal(0, ruido, n)
    return pd.DataFrame({
        "data_compra": pd.date_range("2024-01-01", periods=n, freq="3D"),
        "quantidade": rng.integers(1, 50, n).astype(float),
        "preco_unitario": preco,
    })


def usar_historico(monkeypatch, df):
    """Faz o modelo ler este histórico em vez de consultar o banco."""
    monkeypatch.setattr(previsao_preco, "carregar_dados_produto", lambda produto, sessao: df)


class ModeloQueDiz999:
    """Modelo de mentira: se a previsão dele for usada por engano, o 999 denuncia."""

    def predict(self, entrada):
        return np.array([999.0])


def test_features_tem_as_colunas_esperadas():
    X, y, data_inicial = preparar_features(historico(10))

    assert list(X.columns) == ["dias_desde_inicio", "quantidade", "mes"]
    assert X["dias_desde_inicio"].iloc[0] == 0
    assert X["dias_desde_inicio"].iloc[1] == 3
    assert X["mes"].iloc[0] == 1
    assert data_inicial == pd.Timestamp("2024-01-01")


def test_abaixo_do_minimo_usa_a_media_e_nem_tenta_treinar(monkeypatch):
    df = historico(MINIMO_PEDIDOS_PARA_TREINAR - 1, tendencia=0.5, ruido=1.0)
    usar_historico(monkeypatch, df)

    def nao_deveria_treinar(*args, **kwargs):
        raise AssertionError("o modelo não deveria ser treinado com tão poucos pedidos")

    monkeypatch.setattr(previsao_preco, "avaliar_modelo_cv", nao_deveria_treinar)

    previsao, r2, confiavel = prever_preco_atual("PRODUTO", SESSAO)

    assert previsao == pytest.approx(df["preco_unitario"].mean())
    assert r2 is None
    assert confiavel is False


def test_no_minimo_exato_o_modelo_ja_treina(monkeypatch):
    usar_historico(monkeypatch, historico(MINIMO_PEDIDOS_PARA_TREINAR, tendencia=0.3, ruido=1.0))

    modelo, _, _, r2, nome_modelo = treinar_e_avaliar("PRODUTO", SESSAO)

    assert modelo is not None
    assert nome_modelo in {"Regressão Linear", "Random Forest", "Gradient Boosting"}
    assert r2 is not None


def test_tendencia_clara_e_reconhecida_e_a_previsao_acompanha_a_alta(monkeypatch):
    df = historico(100, tendencia=0.4, ruido=1.0)
    usar_historico(monkeypatch, df)

    previsao, r2, confiavel = prever_preco_atual("PRODUTO", SESSAO)

    assert confiavel is True
    assert r2 > 0.5
    # preço em alta: a previsão para "hoje" fica acima da média do histórico inteiro
    assert previsao > df["preco_unitario"].mean()


@pytest.mark.parametrize("r2, deve_confiar", [
    (-0.5, False),
    (-0.01, False),
    (0.0, False),    # empatar com a média histórica não basta: precisa ser maior que zero
    (0.01, True),
    (0.9, True),
])
def test_so_confia_no_modelo_quando_o_r2_e_positivo(monkeypatch, r2, deve_confiar):
    df = historico(100, tendencia=0.2, ruido=1.0)
    monkeypatch.setattr(
        previsao_preco,
        "treinar_e_avaliar",
        lambda produto, sessao: (ModeloQueDiz999(), df["data_compra"].min(), df, r2, "Falso"),
    )

    previsao, r2_devolvido, confiavel = prever_preco_atual("PRODUTO", SESSAO)

    assert confiavel is deve_confiar
    assert r2_devolvido == pytest.approx(r2)
    if deve_confiar:
        assert previsao == pytest.approx(999.0)
    else:
        assert previsao == pytest.approx(df["preco_unitario"].mean())


def test_retorno_usa_tipos_nativos_e_vira_json(monkeypatch):
    """O numpy.bool_ já quebrou o jsonify do Flask: o retorno precisa ser bool/float puros."""
    usar_historico(monkeypatch, historico(100, tendencia=0.4, ruido=1.0))

    previsao, r2, confiavel = prever_preco_atual("PRODUTO", SESSAO)

    assert type(previsao) is float
    assert type(r2) is float
    assert type(confiavel) is bool
    json.dumps({"previsao": previsao, "r2": r2, "confiavel": confiavel})


def test_retorno_sem_treino_tambem_usa_tipos_nativos(monkeypatch):
    usar_historico(monkeypatch, historico(10, tendencia=0.4, ruido=1.0))

    previsao, r2, confiavel = prever_preco_atual("PRODUTO", SESSAO)

    assert type(previsao) is float
    assert r2 is None
    assert type(confiavel) is bool
    json.dumps({"previsao": previsao, "r2": r2, "confiavel": confiavel})


def test_mesmos_dados_dao_sempre_o_mesmo_resultado(monkeypatch):
    """Reprodutibilidade: sementes fixas e ordem dos dados estável."""
    usar_historico(monkeypatch, historico(100, tendencia=0.2, ruido=3.0, semente=1))

    _, _, _, r2_primeiro, modelo_primeiro = treinar_e_avaliar("PRODUTO", SESSAO)
    _, _, _, r2_segundo, modelo_segundo = treinar_e_avaliar("PRODUTO", SESSAO)

    assert r2_primeiro == r2_segundo
    assert modelo_primeiro == modelo_segundo