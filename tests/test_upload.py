"""Testes do ETL de upload: conversões de valores, contrato com a planilha-modelo
e o fluxo completo (planilha -> validação -> inserção) contra um banco falso."""
import io
import uuid
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from etl.processar_upload import (
    COLUNAS_ESPERADAS,
    normalizar_unidade,
    para_int_opcional,
    para_numero,
    parse_data,
    processar_planilha_upload,
)

COLUNAS = [
    "ID_Pedido", "Nome_Fornecedor", "Produto", "Unidade_Medida", "Data_Compra",
    "Quantidade", "Preco_Unitario", "Custo_Frete",
    "Prazo_Entrega_Prometido_Dias_Uteis", "Tempo_Entrega_Real_Dias_Uteis",
    "Pedido_Sofreu_Atraso", "Pedido_Sofreu_Avaria",
]


def linha(**alteracoes):
    """Uma linha de pedido válida; cada teste altera só o que quer exercitar."""
    base = {
        "ID_Pedido": 1,
        "Nome_Fornecedor": "Fornecedor A",
        "Produto": "ACIDO X",
        "Unidade_Medida": "L",
        "Data_Compra": "15/01/2025",
        "Quantidade": 10,
        "Preco_Unitario": 25.5,
        "Custo_Frete": 12.0,
        "Prazo_Entrega_Prometido_Dias_Uteis": 5,
        "Tempo_Entrega_Real_Dias_Uteis": 6,
        "Pedido_Sofreu_Atraso": "NAO",
        "Pedido_Sofreu_Avaria": "NAO",
    }
    base.update(alteracoes)
    return base


def planilha(*linhas, colunas=None):
    """Monta um .xlsx em memória, como o usuário enviaria pelo site."""
    df = pd.DataFrame(list(linhas), columns=colunas or COLUNAS)
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)
    return buffer


# ---------------------------------------------------------------- conversões

@pytest.mark.parametrize("entrada, esperado", [
    ("KG", "KG"),
    ("kg", "KG"),
    ("  Kg ", "KG"),
    ("Quilos", "KG"),
    ("L", "LITROS"),
    ("lt", "LITROS"),
    ("Litro", "LITROS"),
    ("LITROS", "LITROS"),
    ("TON", None),
    ("", None),
    (None, None),
    (float("nan"), None),
])
def test_normalizar_unidade(entrada, esperado):
    assert normalizar_unidade(entrada) == esperado


@pytest.mark.parametrize("entrada, esperado", [
    (10, 10.0),
    (2.5, 2.5),
    ("12,5", 12.5),
    (" 7 ", 7.0),
    ("abc", None),
    (None, None),
    (float("nan"), None),
    (True, None),
])
def test_para_numero_aceita_numeros_e_rejeita_o_resto(entrada, esperado):
    assert para_numero(entrada) == esperado


def test_para_numero_rejeita_data_no_lugar_do_numero():
    """Foi o que derrubou o upload: células de preço que o Excel converteu em data."""
    assert para_numero(datetime(2026, 7, 22)) is None
    assert para_numero(pd.Timestamp("2026-07-22")) is None


@pytest.mark.parametrize("entrada, esperado", [
    (5, 5),
    (5.0, 5),
    ("7", 7),
    (None, None),
    (float("nan"), None),
    ("abc", None),
])
def test_para_int_opcional(entrada, esperado):
    assert para_int_opcional(entrada) == esperado


@pytest.mark.parametrize("entrada, esperado", [
    ("15/01/2025", date(2025, 1, 15)),
    ("03/04/2025", date(2025, 4, 3)),        # formato brasileiro: dia/mês
    (datetime(2025, 1, 15), date(2025, 1, 15)),
    ("32/13/2025", None),                    # data impossível
    ("abc", None),
    ("15/01/1999", None),                    # fora do intervalo aceito
    (None, None),
    (float("nan"), None),                    # célula vazia vinda do Excel
])
def test_parse_data(entrada, esperado):
    assert parse_data(entrada) == esperado


# -------------------------------------------------- contrato com a planilha

def test_planilha_modelo_tem_todas_as_colunas_que_o_etl_exige():
    caminho = Path(__file__).resolve().parent.parent / "static" / "planilha_modelo.xlsx"
    colunas = set(pd.read_excel(caminho, nrows=0).columns)
    assert COLUNAS_ESPERADAS <= colunas


# --------------------------------------------------------------- fluxo completo

def test_planilha_suja_separa_validos_de_rejeitados(banco):
    arquivo = planilha(
        linha(ID_Pedido=1, Pedido_Sofreu_Atraso="SIM"),             # válida ("L" vira LITROS)
        linha(ID_Pedido=None, Nome_Fornecedor="Fornecedor B"),       # válida: ID é opcional
        linha(ID_Pedido=3, Custo_Frete="12,5"),                      # válida: vírgula decimal
        linha(ID_Pedido=4, Preco_Unitario=datetime(2026, 7, 22)),    # data no lugar do preço
        linha(ID_Pedido=5, Data_Compra="32/13/2025"),                # data impossível
        linha(ID_Pedido=6, Unidade_Medida="TON"),                    # unidade desconhecida
        linha(ID_Pedido=1),                                          # ID repetido
        linha(ID_Pedido=8, Nome_Fornecedor=None),                    # fornecedor vazio
    )

    sessao, validos, rejeitados, df_rejeitados = processar_planilha_upload(arquivo, "sessao-teste")

    assert sessao == "sessao-teste"
    assert validos == 3
    assert rejeitados == 5

    motivos = " | ".join(df_rejeitados["motivo"])
    assert "Preço unitário inválido" in motivos
    assert "Data inválida" in motivos
    assert "Unidade de medida não reconhecida" in motivos
    assert "ID_Pedido duplicado" in motivos
    assert "Fornecedor inválido ou vazio" in motivos


def test_pedidos_validos_chegam_ao_banco_convertidos(banco):
    arquivo = planilha(
        linha(ID_Pedido=1, Pedido_Sofreu_Atraso="SIM"),
        linha(ID_Pedido=3, Custo_Frete="12,5"),
    )

    processar_planilha_upload(arquivo, "sessao-teste")

    primeiro, segundo = banco.pedidos
    assert primeiro["data_compra"] == date(2025, 1, 15)
    assert primeiro["sofreu_atraso"] is True
    assert primeiro["sofreu_avaria"] is False
    assert segundo["custo_frete"] == 12.5
    assert banco.unidades["ACIDO X"] == "LITROS"


def test_pedido_sem_id_entra_no_banco_com_id_nulo(banco):
    """Coluna ID_Pedido é opcional: o banco precisa receber NULL, nunca NaN."""
    arquivo = planilha(linha(ID_Pedido=1), linha(ID_Pedido=None))

    processar_planilha_upload(arquivo, "sessao-teste")

    sem_id = banco.pedidos[1]["id_pedido_origem"]
    assert sem_id is None


def test_linha_sem_data_e_rejeitada(banco):
    arquivo = planilha(linha(ID_Pedido=1), linha(ID_Pedido=2, Data_Compra=None))

    _, validos, rejeitados, df_rejeitados = processar_planilha_upload(arquivo, "sessao-teste")

    assert (validos, rejeitados) == (1, 1)
    assert "Data inválida" in df_rejeitados["motivo"].iloc[0]


def test_todas_as_linhas_invalidas_nao_inserem_nada(banco):
    arquivo = planilha(linha(Preco_Unitario="caro"))

    _, validos, rejeitados, _ = processar_planilha_upload(arquivo, "sessao-teste")

    assert (validos, rejeitados) == (0, 1)
    assert banco.pedidos == []


def test_planilha_sem_coluna_obrigatoria_e_recusada(banco):
    colunas_sem_preco = [c for c in COLUNAS if c != "Preco_Unitario"]
    arquivo = planilha(linha(), colunas=colunas_sem_preco)

    with pytest.raises(ValueError, match="Preco_Unitario"):
        processar_planilha_upload(arquivo, "sessao-teste")


def test_uploads_de_sessoes_diferentes_ficam_separados(banco):
    processar_planilha_upload(planilha(linha(ID_Pedido=1), linha(ID_Pedido=2)), "sessao-a")
    processar_planilha_upload(planilha(linha(ID_Pedido=1)), "sessao-b")

    por_sessao = {}
    for pedido in banco.pedidos:
        por_sessao[pedido["sessao_id"]] = por_sessao.get(pedido["sessao_id"], 0) + 1

    assert por_sessao == {"sessao-a": 2, "sessao-b": 1}


def test_sem_sessao_informada_gera_um_uuid(banco):
    sessao, _, _, _ = processar_planilha_upload(planilha(linha()))

    assert str(uuid.UUID(sessao)) == sessao


def test_cada_upload_dispara_a_limpeza_de_sessoes_antigas(banco):
    processar_planilha_upload(planilha(linha()), "sessao-teste")

    assert banco.limpezas == 1