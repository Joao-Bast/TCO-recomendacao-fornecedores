"""Validação do sistema com a base sintética (scripts/gerar_dados_sinteticos.py).

A base tem tendências e perfis de fornecedor conhecidos. Os testes conferem se o
modelo encontra a tendência certa, se não inventa tendência no controle negativo,
se escolhe famílias de modelo coerentes com cada padrão e se o ranking de TCO
reproduz o ranking esperado pelo gabarito."""
import contextlib
import io
from functools import lru_cache
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from etl.processar_upload import COLUNAS_ESPERADAS
from modelo import previsao_preco
from modelo.previsao_preco import analisar_tendencia
from scoring.calcular_tco import montar_ranking
from scripts.gerar_dados_sinteticos import FORNECEDORES, PEDIDOS_POR_FORNECEDOR, PRODUTOS, gerar

ITENS_COM_TENDENCIA = [p["nome"] for p in PRODUTOS if p["tem_tendencia"]]
ITENS_COM_DEGRAU_OU_SAZONALIDADE = ["Item E", "Item F", "Item G"]
MODELOS_DE_ARVORE = {"Random Forest", "Gradient Boosting"}
TOLERANCIA_PONTOS_PERCENTUAIS = 8.0


def agregar_por_fornecedor(pedidos):
    """Mesma agregação da consulta SQL de calcular_ranking (uma linha por fornecedor)."""
    linhas = []
    for nome, grupo in pedidos.groupby("Nome_Fornecedor"):
        linhas.append(SimpleNamespace(
            fornecedor=nome,
            total_pedidos=len(grupo),
            preco_medio=grupo["Preco_Unitario"].mean(),
            frete_por_unidade_medio=(grupo["Custo_Frete"] / grupo["Quantidade"]).mean(),
            taxa_atraso=(grupo["Pedido_Sofreu_Atraso"] == "SIM").mean(),
            taxa_avaria=(grupo["Pedido_Sofreu_Avaria"] == "SIM").mean(),
        ))
    return linhas


def spearman(ordem_esperada, ordem_obtida):
    """Correlação de postos entre dois rankings (1 = idênticos)."""
    posicao = {nome: i for i, nome in enumerate(ordem_esperada)}
    n = len(ordem_esperada)
    soma = sum((posicao[nome] - i) ** 2 for i, nome in enumerate(ordem_obtida))
    return 1 - 6 * soma / (n * (n * n - 1))


@lru_cache(maxsize=1)
def resultados():
    """Gera a base e roda o sistema em cada produto, uma única vez para todos os testes."""
    df, gabarito = gerar()
    por_item = {}
    for esperado in gabarito["produtos"]:
        pedidos = df[df["Produto"] == esperado["nome"]]
        historico = pd.DataFrame({
            "data_compra": pd.to_datetime(pedidos["Data_Compra"]),
            "quantidade": pedidos["Quantidade"].astype(float),
            "preco_unitario": pedidos["Preco_Unitario"].astype(float),
        }).reset_index(drop=True)

        with patch.object(previsao_preco, "carregar_dados_produto", lambda nome, sessao, h=historico: h), \
                contextlib.redirect_stdout(io.StringIO()):
            tendencia = analisar_tendencia(esperado["nome"], "sessao-sintetica")

        ranking = montar_ranking(agregar_por_fornecedor(pedidos), tendencia["preco_referencia"], top_n=None)
        por_item[esperado["nome"]] = {
            "esperado": esperado,
            "tendencia": tendencia,
            "ordem_obtida": [item["fornecedor"] for item in ranking],
            "ordem_esperada": [item["fornecedor"] for item in esperado["ranking_esperado"]],
            "pedidos": pedidos,
        }
    return df, por_item


# ----------------------------------------------------------- a base em si

def test_gerador_e_reprodutivel():
    primeira, _ = gerar()
    segunda, _ = gerar()

    pd.testing.assert_frame_equal(primeira, segunda)


def test_todos_os_fornecedores_vendem_todos_os_produtos():
    df, _ = resultados()

    contagem = df.groupby(["Produto", "Nome_Fornecedor"]).size()
    assert len(df) == len(PRODUTOS) * len(FORNECEDORES) * PEDIDOS_POR_FORNECEDOR
    assert len(contagem) == len(PRODUTOS) * len(FORNECEDORES)
    assert (contagem == PEDIDOS_POR_FORNECEDOR).all()


def test_base_esta_no_formato_da_planilha_modelo():
    df, _ = resultados()

    assert COLUNAS_ESPERADAS <= set(df.columns)
    assert df["ID_Pedido"].is_unique
    assert set(df["Unidade_Medida"]) == {"KG", "LITROS"}


def test_atraso_e_coerente_com_os_prazos():
    df, _ = resultados()

    atrasou = df["Tempo_Entrega_Real_Dias_Uteis"] > df["Prazo_Entrega_Prometido_Dias_Uteis"]
    assert ((df["Pedido_Sofreu_Atraso"] == "SIM") == atrasou).all()


# ------------------------------------------------------ tendência do preço

@pytest.mark.parametrize("item", ITENS_COM_TENDENCIA)
def test_modelo_encontra_a_tendencia_injetada(item):
    _, por_item = resultados()
    esperado = por_item[item]["esperado"]["variacao_esperada_percentual"]
    tendencia = por_item[item]["tendencia"]

    assert tendencia["confiavel"] is True
    assert (tendencia["variacao_percentual"] > 0) == (esperado > 0)
    assert tendencia["variacao_percentual"] == pytest.approx(esperado, abs=TOLERANCIA_PONTOS_PERCENTUAIS)


def test_controle_negativo_nao_ganha_tendencia_inventada():
    _, por_item = resultados()
    tendencia = por_item["Item J"]["tendencia"]

    assert tendencia["confiavel"] is False or abs(tendencia["variacao_percentual"]) < 5


@pytest.mark.parametrize("item", ITENS_COM_DEGRAU_OU_SAZONALIDADE)
def test_saltos_e_sazonalidade_sao_vencidos_por_modelos_de_arvore(item):
    """Uma reta não acompanha um salto nem um ciclo anual: a seleção automática
    precisa escolher Random Forest ou Gradient Boosting nesses casos."""
    _, por_item = resultados()

    assert por_item[item]["tendencia"]["modelo"] in MODELOS_DE_ARVORE


# ------------------------------------------------------------ ranking de TCO

@pytest.mark.parametrize("item", [p["nome"] for p in PRODUTOS])
def test_ranking_reproduz_o_ranking_esperado(item):
    _, por_item = resultados()
    resultado = por_item[item]

    assert spearman(resultado["ordem_esperada"], resultado["ordem_obtida"]) >= 0.75


@pytest.mark.parametrize("item", [p["nome"] for p in PRODUTOS])
def test_o_mais_barato_nao_e_o_melhor(item):
    """A tese do projeto: o menor preço não garante o menor custo total."""
    _, por_item = resultados()
    resultado = por_item[item]

    precos = resultado["pedidos"].groupby("Nome_Fornecedor")["Preco_Unitario"].mean()
    mais_barato = precos.idxmin()

    assert mais_barato == "Fornecedor 1"
    assert resultado["ordem_obtida"][0] != mais_barato


@pytest.mark.parametrize("item", [p["nome"] for p in PRODUTOS])
def test_extremos_do_ranking(item):
    _, por_item = resultados()
    ordem = por_item[item]["ordem_obtida"]

    assert "Fornecedor 3" in ordem[:3]       # lote grande e confiável: sempre entre os melhores
    assert ordem[-1] == "Fornecedor 10"      # caro, atrasa e avaria: sempre o último