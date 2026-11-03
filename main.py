"""
API do formulário de contratação — George Reis Advocacia
Recebe JSON do site, valida, grava no banco e permite consulta autenticada.

Rodar local:  uvicorn main:app --reload
"""
import os
import re
import time
import secrets
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Optional, Literal

from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, field_validator, model_validator, ConfigDict
from sqlalchemy import create_engine, String, Boolean, DateTime, Integer, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session

# ---------------- CONFIG (variáveis de ambiente) ----------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./clientes.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

ALLOWED_ORIGINS = [o.strip() for o in os.getenv(
    "ALLOWED_ORIGINS",
    "https://www.georgereis.adv.br,https://georgereis.adv.br").split(",") if o.strip()]
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")  # obrigatório em produção
# -----------------------------------------------------------------

UFS = {"AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS","MG","PA",
       "PB","PR","PE","PI","RJ","RN","RS","RO","RR","SC","SP","SE","TO"}
DDDS_VALIDOS = {11,12,13,14,15,16,17,18,19,21,22,24,27,28,31,32,33,34,35,37,38,41,42,43,44,45,46,
                47,48,49,51,53,54,55,61,62,63,64,65,66,67,68,69,71,73,74,75,77,79,81,82,83,84,85,
                86,87,88,89,91,92,93,94,95,96,97,98,99}

RE_NOME = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’.\- ]+$")
RE_TEXTO = re.compile(r"^[0-9A-Za-zÀ-ÖØ-öø-ÿ'’.,º°ªª\-/ ]+$")
RE_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def so_digitos(v: str) -> str:
    return re.sub(r"\D", "", v)


def limpar(v: str) -> str:
    return re.sub(r"\s+", " ", v).strip()


def cpf_valido(cpf: str) -> bool:
    n = [int(c) for c in cpf]
    if len(n) != 11 or len(set(n)) == 1:
        return False
    for i in (9, 10):
        soma = sum(n[j] * (i + 1 - j) for j in range(i))
        if (soma * 10 % 11) % 10 != n[i]:
            return False
    return True


# ---------------- Modelo de entrada (validação) ----------------
class ClienteIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    nome: str
    nacionalidade: str
    sexo: Literal["Masculino", "Feminino"]
    estado_civil: Literal["Solteiro(a)", "Casado(a)", "União estável", "Divorciado(a)", "Viúvo(a)", "Separado(a)"]
    profissao: str
    empregado: Literal["Sim", "Não"]
    rg: str
    orgao_emissor: str
    cpf: str
    cep: str
    rua: str
    numero: str
    bairro: str
    cidade: str
    uf: str
    telefone: str
    email: Optional[str] = None
    consentimento: bool
    website: str = ""   # honeypot: humanos deixam vazio

    @field_validator("nome")
    @classmethod
    def v_nome(cls, v):
        v = limpar(v)
        if not (5 <= len(v) <= 120) or not RE_NOME.match(v) or len(v.split()) < 2:
            raise ValueError("Informe o nome completo, usando apenas letras.")
        return v

    @field_validator("nacionalidade", "profissao", "bairro", "cidade")
    @classmethod
    def v_texto_curto(cls, v):
        v = limpar(v)
        if not (2 <= len(v) <= 80) or not RE_TEXTO.match(v):
            raise ValueError("Campo inválido: use apenas letras, números e pontuação simples.")
        return v

    @field_validator("rua")
    @classmethod
    def v_rua(cls, v):
        v = limpar(v)
        if not (3 <= len(v) <= 120) or not RE_TEXTO.match(v):
            raise ValueError("Endereço inválido.")
        return v

    @field_validator("numero")
    @classmethod
    def v_numero(cls, v):
        v = limpar(v)
        if not re.match(r"^(S/?N|\d{1,6}[A-Za-z]?)$", v, re.I):
            raise ValueError("Número inválido (use dígitos ou S/N).")
        return v.upper()

    @field_validator("rg")
    @classmethod
    def v_rg(cls, v):
        v = limpar(v)
        if not (5 <= len(v) <= 20) or not re.match(r"^[0-9A-Za-z.\-/ ]+$", v):
            raise ValueError("RG inválido.")
        return v.upper()

    @field_validator("orgao_emissor")
    @classmethod
    def v_orgao(cls, v):
        v = limpar(v).upper()
        if not re.match(r"^[A-Z]{2,10}(/[A-Z]{2})?$", v):
            raise ValueError("Órgão emissor inválido (ex.: SSP/SP).")
        return v

    @field_validator("cpf")
    @classmethod
    def v_cpf(cls, v):
        d = so_digitos(v)
        if not cpf_valido(d):
            raise ValueError("CPF inválido.")
        return d

    @field_validator("cep")
    @classmethod
    def v_cep(cls, v):
        d = so_digitos(v)
        if len(d) != 8:
            raise ValueError("CEP deve ter 8 dígitos.")
        return d

    @field_validator("uf")
    @classmethod
    def v_uf(cls, v):
        v = v.upper()
        if v not in UFS:
            raise ValueError("UF inválida.")
        return v

    @field_validator("telefone")
    @classmethod
    def v_tel(cls, v):
        d = so_digitos(v)
        if d.startswith("55") and len(d) in (12, 13):
            d = d[2:]
        if len(d) not in (10, 11) or int(d[:2]) not in DDDS_VALIDOS:
            raise ValueError("Telefone inválido. Informe DDD + número.")
        if len(d) == 11 and d[2] != "9":
            raise ValueError("Celular deve começar com 9 após o DDD.")
        return d

    @field_validator("email")
    @classmethod
    def v_email(cls, v):
        if not v:
            return None
        v = v.strip().lower()
        if len(v) > 120 or not RE_EMAIL.match(v):
            raise ValueError("E-mail inválido.")
        return v

    @field_validator("consentimento")
    @classmethod
    def v_consent(cls, v):
        if v is not True:
            raise ValueError("É necessário aceitar o tratamento dos dados.")
        return v

    @model_validator(mode="after")
    def v_honeypot(self):
        if self.website:
            raise ValueError("Requisição inválida.")
        return self


# ---------------- Banco de dados ----------------
class Base(DeclarativeBase):
    pass


class Cliente(Base):
    __tablename__ = "clientes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    nome: Mapped[str] = mapped_column(String(120))
    nacionalidade: Mapped[str] = mapped_column(String(80))
    sexo: Mapped[str] = mapped_column(String(10))
    estado_civil: Mapped[str] = mapped_column(String(20))
    profissao: Mapped[str] = mapped_column(String(80))
    empregado: Mapped[str] = mapped_column(String(3))
    rg: Mapped[str] = mapped_column(String(20))
    orgao_emissor: Mapped[str] = mapped_column(String(15))
    cpf: Mapped[str] = mapped_column(String(11), index=True)
    cep: Mapped[str] = mapped_column(String(8))
    rua: Mapped[str] = mapped_column(String(120))
    numero: Mapped[str] = mapped_column(String(10))
    bairro: Mapped[str] = mapped_column(String(80))
    cidade: Mapped[str] = mapped_column(String(80))
    uf: Mapped[str] = mapped_column(String(2))
    telefone: Mapped[str] = mapped_column(String(11))
    email: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    consentimento_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    contrato_gerado: Mapped[bool] = mapped_column(Boolean, default=False)


engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Base.metadata.create_all(engine)

# ---------------- App ----------------
app = FastAPI(title="Formulário George Reis Advocacia", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# Limite simples por IP: 5 envios / 10 min (em memória; com vários servidores use Redis)
_HIST: dict[str, deque] = defaultdict(deque)


def limitar(request: Request):
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "?"))
    agora = time.time()
    q = _HIST[ip]
    while q and agora - q[0] > 600:
        q.popleft()
    if len(q) >= 5:
        raise HTTPException(429, "Muitas tentativas. Aguarde alguns minutos.")
    q.append(agora)


@app.exception_handler(RequestValidationError)
async def erro_validacao(request: Request, exc: RequestValidationError):
    campos = {}
    for e in exc.errors():
        campo = str(e["loc"][-1]) if e.get("loc") else "?"
        msg = str(e.get("msg", "")).replace("Value error, ", "")
        if e.get("type") == "missing":
            msg = "Campo obrigatório."
        elif e.get("type") == "extra_forbidden":
            msg = "Campo não permitido."
        campos[campo] = msg
    # não devolve o conteúdo enviado (pode conter dados pessoais)
    return JSONResponse(status_code=422, content={"ok": False, "erros": campos})


@app.post("/api/clientes", status_code=201, dependencies=[Depends(limitar)])
def criar_cliente(dados: ClienteIn):
    agora = datetime.now(timezone.utc)
    d = dados.model_dump(exclude={"website", "consentimento"})
    with Session(engine) as s:
        c = Cliente(**d, criado_em=agora, consentimento_em=agora)
        s.add(c)
        s.commit()
    return {"ok": True, "mensagem": "Dados recebidos. Entraremos em contato."}


def exigir_admin(x_api_key: str = Header(default="")):
    if not ADMIN_API_KEY or not secrets.compare_digest(x_api_key, ADMIN_API_KEY):
        raise HTTPException(401, "Não autorizado.")


@app.get("/api/admin/clientes", dependencies=[Depends(exigir_admin)])
def listar(pendentes: bool = False):
    with Session(engine) as s:
        q = select(Cliente).order_by(Cliente.id.desc())
        if pendentes:
            q = q.where(Cliente.contrato_gerado.is_(False))
        return [{c.name: getattr(r, c.name) for c in Cliente.__table__.columns}
                for r in s.scalars(q)]


@app.get("/saude")
def saude():
    return {"ok": True}
