"""Configuração: lê o .env da raiz do projeto (variáveis de ambiente reais têm prioridade)."""

from __future__ import annotations

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent


def _carregar_env(caminho: Path) -> None:
    if not caminho.is_file():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        chave = chave.strip().removeprefix("export ").strip()
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        os.environ.setdefault(chave, valor)


_carregar_env(Path(os.environ.get("ENV_FILE") or RAIZ / ".env"))


def _bool(nome: str, padrao: bool) -> bool:
    valor = os.environ.get(nome)
    if valor is None or valor == "":
        return padrao
    return valor.strip().lower() in ("1", "true", "sim", "yes", "on")


# --- Veeam ONE (somente leitura) ---
VEEAM_ONE_URL = os.environ.get("VEEAM_ONE_URL", "").rstrip("/")
VEEAM_USUARIO = os.environ.get("VEEAM_USUARIO", "")
VEEAM_SENHA = os.environ.get("VEEAM_SENHA", "")
VEEAM_ONE_API_VERSAO = os.environ.get("VEEAM_ONE_API_VERSAO") or "v2.3"
VEEAM_VERIFICAR_SSL = _bool("VEEAM_VERIFICAR_SSL", True)
VEEAM_TIMEOUT_SEGUNDOS = int(os.environ.get("VEEAM_TIMEOUT_SEGUNDOS") or 60)

# --- SCP / Sangfor Cloud Platform (assinatura EC2 AK/SK) ---
SCP_BASE_URL = os.environ.get("SCP_BASE_URL", "").rstrip("/")
SCP_ACCESS_KEY = os.environ.get("SCP_ACCESS_KEY", "")
SCP_SECRET_KEY = os.environ.get("SCP_SECRET_KEY", "")
SCP_REGION = os.environ.get("SCP_REGION") or "cn-south-1"
SCP_SERVICE = os.environ.get("SCP_SERVICE") or "open-api"
SCP_TIMEOUT_SEGUNDOS = int(os.environ.get("SCP_TIMEOUT_SEGUNDOS") or 90)

# --- Relatório ---
# Janela usada para "backup recente" e job ativo/inativo
DIAS_PERIODO = int(os.environ.get("RELATORIO_DIAS_PERIODO") or 30)
# Último backup com mais de N horas fica em alerta (amarelo); mais de N dias, vermelho
HORAS_ALERTA_BACKUP = int(os.environ.get("RELATORIO_HORAS_ALERTA") or 36)
DIAS_CRITICO_BACKUP = int(os.environ.get("RELATORIO_DIAS_CRITICO") or 7)
PASTA_SAIDA = Path(os.environ.get("RELATORIO_PASTA_SAIDA") or RAIZ / "relatorios")
