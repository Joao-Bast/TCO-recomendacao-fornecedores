"""Banco de dados falso (em memória) usado nos testes do ETL.

Imita só o que processar_planilha_upload usa: INSERT em fornecedor/produto/pedido,
SELECT dos ids e o DELETE de limpeza. Assim nenhum teste toca no PostgreSQL real."""


class _Resultado:
    def __init__(self, linhas=None, rowcount=0):
        self._linhas = linhas or []
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._linhas)


class _Conexao:
    def __init__(self, banco):
        self._banco = banco

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, parametros=None):
        return self._banco.executar(str(sql), parametros or {})


class BancoFalso:
    def __init__(self):
        self.fornecedores = {}  # nome -> id
        self.produtos = {}      # nome -> id
        self.unidades = {}      # nome do produto -> unidade
        self.pedidos = []       # um dict por linha inserida em "pedido"
        self.limpezas = 0       # quantas vezes a limpeza de sessões antigas rodou

    def begin(self):
        return _Conexao(self)

    def connect(self):
        return _Conexao(self)

    def executar(self, sql, parametros):
        if "DELETE FROM pedido" in sql:
            self.limpezas += 1
            return _Resultado(rowcount=0)
        if "INSERT INTO fornecedor" in sql:
            self.fornecedores.setdefault(parametros["nome"], len(self.fornecedores) + 1)
            return _Resultado()
        if "INSERT INTO produto" in sql:
            nome = parametros["nome"]
            self.produtos.setdefault(nome, len(self.produtos) + 1)
            self.unidades.setdefault(nome, parametros["unidade"])
            return _Resultado()
        if "INSERT INTO pedido" in sql:
            self.pedidos.append(dict(parametros))
            return _Resultado()
        if "FROM fornecedor" in sql:
            return _Resultado([(i, n) for n, i in self.fornecedores.items()])
        if "FROM produto" in sql:
            return _Resultado([(i, n) for n, i in self.produtos.items()])
        raise AssertionError(f"consulta inesperada no banco falso: {sql}")