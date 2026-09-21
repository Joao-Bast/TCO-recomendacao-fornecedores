# modelo/previsao_preco.py
import pandas as pd
from sqlalchemy import text
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.model_selection import KFold, cross_validate
from config.database import get_engine

R2_MINIMO_PARA_CONFIAR = 0.0  # abaixo disso, nem o melhor modelo bate a média histórica
N_SPLITS = 5  # número de divisões da validação cruzada


def listar_produtos_cadastrados():
    engine = get_engine()
    with engine.connect() as conn:
        resultado = conn.execute(text("SELECT nome FROM produto ORDER BY nome")).fetchall()
    return [row[0] for row in resultado]


def carregar_dados_produto(nome_produto):
    engine = get_engine()
    with engine.connect() as conn:
        resultado = conn.execute(
            text("""
                SELECT p.data_compra, p.quantidade, p.preco_unitario
                FROM pedido p
                JOIN produto pr ON pr.id = p.produto_id
                WHERE pr.nome = :nome_produto
                ORDER BY p.data_compra
            """),
            {"nome_produto": nome_produto},
        ).fetchall()

    df = pd.DataFrame(resultado, columns=["data_compra", "quantidade", "preco_unitario"])
    df["data_compra"] = pd.to_datetime(df["data_compra"])
    df["quantidade"] = df["quantidade"].astype(float)
    df["preco_unitario"] = df["preco_unitario"].astype(float)
    return df


def preparar_features(df):
    data_inicial = df["data_compra"].min()
    df = df.copy()
    df["dias_desde_inicio"] = (df["data_compra"] - data_inicial).dt.days
    X = df[["dias_desde_inicio", "quantidade"]]
    y = df["preco_unitario"]
    return X, y, data_inicial


def avaliar_modelo_cv(modelo, X, y, n_splits=N_SPLITS):
    """Avalia um modelo com validação cruzada k-fold, em vez de um único split.
    Retorna a média e o desvio padrão das métricas ao longo das k divisões."""
    kf = KFold(n_splits=n_splits, shuffle=True, random_state=42)
    resultado = cross_validate(
        modelo, X, y, cv=kf,
        scoring={
            "mae": "neg_mean_absolute_error",
            "rmse": "neg_root_mean_squared_error",
            "r2": "r2",
        },
    )
    return {
        "mae_medio": -resultado["test_mae"].mean(),
        "mae_desvio": resultado["test_mae"].std(),
        "rmse_medio": -resultado["test_rmse"].mean(),
        "rmse_desvio": resultado["test_rmse"].std(),
        "r2_medio": resultado["test_r2"].mean(),
        "r2_desvio": resultado["test_r2"].std(),
    }


def treinar_e_avaliar(nome_produto, n_splits=N_SPLITS):
    """Compara Random Forest e Gradient Boosting via validação cruzada k-fold,
    seleciona o melhor pela média do R², e retreina esse modelo com todos os
    dados disponíveis para gerar a previsão final (a CV serve só para avaliação
    honesta; a previsão em produção usa o máximo de dado possível)."""
    df = carregar_dados_produto(nome_produto)
    X, y, data_inicial = preparar_features(df)

    candidatos = {
        "Random Forest": RandomForestRegressor(
            n_estimators=100, max_depth=4, min_samples_leaf=3, random_state=42
        ),
        "Gradient Boosting": GradientBoostingRegressor(
            n_estimators=100, max_depth=3, min_samples_leaf=3,
            learning_rate=0.05, random_state=42
        ),
    }

    print(f"\nValidação cruzada ({n_splits}-fold) para '{nome_produto}':")
    resultados = {}
    for nome, modelo in candidatos.items():
        resultados[nome] = avaliar_modelo_cv(modelo, X, y, n_splits)
        r = resultados[nome]
        print(
            f"  {nome:18s} | MAE: R$ {r['mae_medio']:.2f} (±{r['mae_desvio']:.2f}) "
            f"| RMSE: R$ {r['rmse_medio']:.2f} (±{r['rmse_desvio']:.2f}) "
            f"| R²: {r['r2_medio']:.3f} (±{r['r2_desvio']:.3f})"
        )

    melhor_nome = max(resultados, key=lambda n: resultados[n]["r2_medio"])
    melhor = resultados[melhor_nome]
    print(f"  → Selecionado: {melhor_nome} (maior R² médio)")

    # Retreina o modelo escolhido com todos os dados, para a previsão final
    modelo_final = candidatos[melhor_nome]
    modelo_final.fit(X, y)

    return modelo_final, data_inicial, df, melhor["r2_medio"], melhor_nome


def prever_preco_atual(nome_produto):
    modelo, data_inicial, df, r2, nome_modelo = treinar_e_avaliar(nome_produto)

    quantidade_referencia = df["quantidade"].median()
    dias_ate_hoje = (df["data_compra"].max() - data_inicial).days

    entrada = pd.DataFrame(
        [[dias_ate_hoje, quantidade_referencia]],
        columns=["dias_desde_inicio", "quantidade"],
    )

    confiavel = r2 > R2_MINIMO_PARA_CONFIAR

    if confiavel:
        previsao = modelo.predict(entrada)[0]
        print(f"Preço previsto ({nome_modelo}, R² médio={r2:.3f}) para '{nome_produto}': R$ {previsao:.2f}")
    else:
        previsao = df["preco_unitario"].mean()
        print(f"Nenhum modelo confiável (melhor R² médio={r2:.3f}) — usando média histórica para '{nome_produto}': R$ {previsao:.2f}")

    return previsao, r2, confiavel


if __name__ == "__main__":
    for produto in listar_produtos_cadastrados():
        prever_preco_atual(produto)
        print("-" * 50)