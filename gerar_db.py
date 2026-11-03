"""
Gera os contratos (.docx) dos clientes que ainda não tiveram contrato,
lendo direto do banco de dados.   Uso:  python gerar_db.py
"""
import re
import unicodedata
from pathlib import Path

from docxtpl import DocxTemplate
from sqlalchemy import select
from sqlalchemy.orm import Session

from main import engine, Cliente   # usa o mesmo banco da API (DATABASE_URL)

MODELO = "modelos/contrato_honorarios.docx"
PASTA_SAIDA = Path("saida")


def fmt_cpf(d):  return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
def fmt_cep(d):  return f"{d[:5]}-{d[5:]}"
def fmt_tel(d):
    return f"({d[:2]}) {d[2:7]}-{d[7:]}" if len(d) == 11 else f"({d[:2]}) {d[2:6]}-{d[6:]}"


ESTADO_CIVIL = {
    "Solteiro(a)": ("solteiro", "solteira"), "Casado(a)": ("casado", "casada"),
    "Divorciado(a)": ("divorciado", "divorciada"), "Viúvo(a)": ("viúvo", "viúva"),
    "Separado(a)": ("separado", "separada"), "União estável": ("em união estável",) * 2,
}


def contexto(c: Cliente) -> dict:
    fem = c.sexo == "Feminino"
    suf = "a" if fem else "o"
    return {
        "nome": c.nome,
        "nacionalidade": c.nacionalidade,
        "estado_civil": ESTADO_CIVIL[c.estado_civil][1 if fem else 0],
        "profissao": c.profissao.lower(),
        "situacao_trabalho": ("empregad" if c.empregado == "Sim" else "desempregad") + suf,
        "portador": "portadora" if fem else "portador",
        "inscrito": f"inscrit{suf}",
        "domiciliado": f"domiciliad{suf}",
        "denominado": f"denominad{suf}",
        "rg": f"{c.rg} {c.orgao_emissor}",
        "cpf": fmt_cpf(c.cpf),
        "endereco": f"{c.rua}, nº {c.numero}, {c.bairro}, CEP: {fmt_cep(c.cep)}, {c.cidade}/{c.uf}",
        "telefone": fmt_tel(c.telefone),
    }


def nome_arquivo(c: Cliente) -> str:
    base = unicodedata.normalize("NFD", c.nome)
    base = "".join(ch for ch in base if unicodedata.category(ch) != "Mn")
    base = re.sub(r"[^\w ]", "", base).strip().lower().replace(" ", "_")
    return f"contrato_{c.id}_{base}.docx"


def main():
    PASTA_SAIDA.mkdir(exist_ok=True)
    gerados = 0
    with Session(engine) as s:
        for c in s.scalars(select(Cliente).where(Cliente.contrato_gerado.is_(False))):
            doc = DocxTemplate(MODELO)
            doc.render(contexto(c))
            caminho = PASTA_SAIDA / nome_arquivo(c)
            doc.save(caminho)
            c.contrato_gerado = True
            gerados += 1
            print(f"Gerado: {caminho}")
        s.commit()
    print(f"\nConcluído: {gerados} contrato(s).")


if __name__ == "__main__":
    main()
