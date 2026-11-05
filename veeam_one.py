"""Coleta dos dados de backup de um cliente na API REST do Veeam ONE (somente leitura).

Endpoints usados (/api/{versao}/...):
  vbr/backupServers                     servidores VBR (nome = IP)
  vbrJobs/vmBackupJobs, agentBackupJobs, backupCopyJobs
                                        status, última execução, duração, dados transferidos
  protectedData/virtualMachines         nome e tamanho de origem das VMs
  protectedData/virtualMachines/backups backups por VM: job, repositório, restore points
  protectedData/computers/backups       idem para agentes (servidores físicos)
  vbr/repositories                      capacidade / livre / imutabilidade
  cloudConnect/tenants[/tenantQuotas]   cota do Cloud Connect
  alarms/triggeredAlarms                alarmes do Veeam ONE

Sessões de job (/sessions) exigem papel acima de Read-Only, por isso o histórico vem do
estado de cada job e dos restore points.
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

import config

log = logging.getLogger(__name__)

_PAGINA = 500
# Tipos de job consultados: (recurso, rótulo exibido no relatório)
_TIPOS_JOB = [
    ("vbrJobs/vmBackupJobs", "Backup de VM"),
    ("vbrJobs/agentBackupJobs", "Agente"),
    ("vbrJobs/backupCopyJobs", "Backup Copy"),
]


class ErroVeeam(RuntimeError):
    pass


class ClienteVeeamOne:
    def __init__(self) -> None:
        if not (config.VEEAM_ONE_URL and config.VEEAM_USUARIO and config.VEEAM_SENHA):
            raise ErroVeeam("Preencha VEEAM_ONE_URL, VEEAM_USUARIO e VEEAM_SENHA no .env.")
        self.url = config.VEEAM_ONE_URL
        self.versao = config.VEEAM_ONE_API_VERSAO
        self.ssl = (ssl.create_default_context() if config.VEEAM_VERIFICAR_SSL
                    else ssl._create_unverified_context())
        self._token: str | None = None

    def _req(self, metodo: str, caminho: str, params: dict | None = None,
             form: dict | None = None, auth: bool = True) -> Any:
        url = self.url + caminho + ("?" + urllib.parse.urlencode(params) if params else "")
        cab = {"Accept": "application/json"}
        corpo = None
        if form is not None:
            corpo = urllib.parse.urlencode(form).encode()
            cab["Content-Type"] = "application/x-www-form-urlencoded"
        if auth:
            cab["Authorization"] = f"Bearer {self._obter_token()}"
        req = urllib.request.Request(url, data=corpo, headers=cab, method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=config.VEEAM_TIMEOUT_SEGUNDOS,
                                        context=self.ssl) as r:
                dados = r.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and auth and self._token:
                self._token = None
                return self._req(metodo, caminho, params, form, auth)
            raise ErroVeeam(f"{metodo} {caminho} -> HTTP {exc.code}: "
                            f"{exc.read().decode('utf-8', 'replace')[:200]}") from exc
        except urllib.error.URLError as exc:
            raise ErroVeeam(f"{metodo} {url} -> {exc.reason}") from exc
        return json.loads(dados) if dados else None

    def _obter_token(self) -> str:
        if self._token is None:
            r = self._req("POST", "/api/token", auth=False, form={
                "grant_type": "password",
                "username": config.VEEAM_USUARIO,
                "password": config.VEEAM_SENHA,
            })
            self._token = r["access_token"]
        return self._token

    def listar(self, recurso: str, **params: Any) -> list[dict]:
        caminho = f"/api/{self.versao}/{recurso}"
        itens: list[dict] = []
        while True:
            r = self._req("GET", caminho, {**params, "Offset": len(itens), "Limit": _PAGINA}) or {}
            pagina = r.get("items") or []
            itens.extend(pagina)
            total = r.get("totalCount")
            if not pagina or len(pagina) < _PAGINA or (total is not None and len(itens) >= total):
                return itens


# ----------------------------------------------------------------- utilidades

def data_iso(valor: Any) -> datetime | None:
    """'2026-11-05T09:02:28.183Z' -> datetime UTC."""
    if not valor:
        return None
    try:
        return datetime.strptime(str(valor)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().upper()


def _uid_job(job: dict) -> str | None:
    for chave, valor in job.items():
        if chave.endswith("JobUid") and valor:
            return str(valor)
    return None


# ----------------------------------------------------------------- coleta

def coletar(cliente_cfg: dict) -> dict:
    """Coleta tudo o que pertence ao cliente. Ver clientes.py para as regras."""
    cfg = cliente_cfg["veeam"]
    api = ClienteVeeamOne()
    agora = datetime.now(timezone.utc)
    inicio_periodo = agora - timedelta(days=config.DIAS_PERIODO)
    avisos: list[str] = []

    servidores = {s["backupServerId"]: s for s in api.listar("vbr/backupServers")}
    nome_srv = {sid: s.get("name") or str(sid) for sid, s in servidores.items()}
    ids_dedicados = {sid for sid, nome in nome_srv.items()
                     if _norm(nome) in {_norm(x) for x in cfg.get("servidores") or []}}
    repos_cliente = {_norm(r) for r in cfg.get("repositorios") or []}
    regex = re.compile(cfg["jobs_regex"], re.I) if cfg.get("jobs_regex") else None

    # --- backups por máquina (precisamos deles para achar jobs pelo repositório)
    vms = {str(v.get("vmUidInVbr")): v for v in api.listar("protectedData/virtualMachines")}
    backups: list[dict] = []
    for b in api.listar("protectedData/virtualMachines/backups"):
        if b.get("type") == "Replication":
            continue
        vm = vms.get(str(b.get("vmUidInVbr"))) or {}
        backups.append({
            "nome": vm.get("name") or b.get("vmUidInVbr"),
            "tipo": "VM",
            "plataforma": vm.get("platform") or "",
            "origem_bytes": vm.get("usedSourceSizeBytes") or 0,
            "provisionado_bytes": vm.get("provisionedSourceSizeBytes") or 0,
            "raw": b,
        })
    for b in api.listar("protectedData/computers/backups"):
        backups.append({
            "nome": b.get("name") or "-",
            "tipo": "Agente",
            "plataforma": b.get("platform") or "",
            "origem_bytes": b.get("usedSourceSizeBytes") or 0,
            "provisionado_bytes": b.get("provisionedSourceSizeBytes") or 0,
            "raw": b,
        })

    # --- jobs
    jobs_todos: list[dict] = []
    for recurso, rotulo in _TIPOS_JOB:
        try:
            for j in api.listar(recurso):
                jobs_todos.append({**j, "_tipo": rotulo})
        except ErroVeeam as exc:
            log.warning("Nao foi possivel listar %s: %s", recurso, exc)

    uids_por_repo = {str(b["raw"].get("jobUid")) for b in backups
                     if _norm((b["raw"].get("repository") or {}).get("name")) in repos_cliente}

    def job_do_cliente(j: dict) -> bool:
        return (j.get("backupServerId") in ids_dedicados
                or (regex is not None and regex.search(j.get("name") or "") is not None)
                or _uid_job(j) in uids_por_repo)

    jobs = []
    for j in jobs_todos:
        if not job_do_cliente(j):
            continue
        ultima = data_iso(j.get("lastRun"))
        jobs.append({
            "uid": _uid_job(j),
            "nome": j.get("name") or "-",
            "tipo": j["_tipo"],
            "descricao": j.get("description") or "",
            "servidor": nome_srv.get(j.get("backupServerId"), "-"),
            "status": j.get("status") or "-",
            "ultima_execucao": ultima,
            "duracao_s": j.get("lastRunDurationSec"),
            "media_s": j.get("avgDurationSec"),
            "transferido_bytes": j.get("lastTransferredDataBytes") or 0,
            # Job sem execução no período é considerado legado/desativado
            "ativo": bool(ultima and ultima >= inicio_periodo),
        })
    jobs.sort(key=lambda j: (not j["ativo"], j["nome"].lower()))
    uids_jobs = {j["uid"] for j in jobs}

    # --- máquinas protegidas (uma linha por máquina + job)
    maquinas: dict[tuple, dict] = {}
    for b in backups:
        raw = b["raw"]
        repo = (raw.get("repository") or {}).get("name") or "-"
        if str(raw.get("jobUid")) not in uids_jobs and _norm(repo) not in repos_cliente:
            continue
        chave = (_norm(b["nome"]), str(raw.get("jobUid")))
        ultimo = data_iso(raw.get("lastProtectedDate"))
        m = maquinas.get(chave)
        if m is None:
            maquinas[chave] = {
                "nome": b["nome"],
                "tipo": b["tipo"],
                "job": raw.get("jobName") or "-",
                "repositorio": repo,
                "servidor": raw.get("backupServerName") or nome_srv.get(raw.get("backupServerId"), "-"),
                "restore_points": int(raw.get("restorePoints") or 0),
                "ultimo_backup": ultimo,
                "origem_bytes": b["origem_bytes"],
                "backup_bytes": raw.get("totalRestorePointSizeBytes") or 0,
                "ultimo_rp_bytes": raw.get("latestRestorePointSizeBytes") or 0,
            }
        else:  # mesma máquina/job em mais de uma cadeia de backup
            m["restore_points"] += int(raw.get("restorePoints") or 0)
            m["backup_bytes"] += raw.get("totalRestorePointSizeBytes") or 0
            if ultimo and (m["ultimo_backup"] is None or ultimo > m["ultimo_backup"]):
                m["ultimo_backup"] = ultimo
    lista_maquinas = sorted(maquinas.values(), key=lambda m: (m["nome"].lower(), m["job"].lower()))

    # --- repositórios: os cadastrados + os que recebem backups dos jobs do cliente
    repos_usados = repos_cliente | {_norm(m["repositorio"]) for m in lista_maquinas}
    repositorios = []
    for r in api.listar("vbr/repositories"):
        if _norm(r.get("name")) not in repos_usados:
            continue
        cap, livre = r.get("capacityBytes") or 0, r.get("freeSpaceBytes") or 0
        repositorios.append({
            "nome": r.get("name"),
            "servidor": nome_srv.get(r.get("backupServerId"), "-"),
            "tipo": r.get("type") or "-",
            "capacidade_bytes": cap,
            "livre_bytes": livre,
            "usado_bytes": max(cap - livre, 0),
            "estado": r.get("state") or "-",
            "imutavel": bool(r.get("isImmutable")),
            "dias_imutabilidade": r.get("immutabilityIntervalDays"),
            "dias_ate_encher": r.get("outOfSpaceInDays"),
            # Repositório compartilhado: o uso do disco não é só do cliente
            "exclusivo": _norm(r.get("name")) in repos_cliente,
        })
    repositorios.sort(key=lambda r: (not r["exclusivo"], r["nome"]))

    # --- Cloud Connect
    cloud = []
    nomes_cc = {_norm(n) for n in cfg.get("cloud_connect") or []}
    if nomes_cc:
        tenants = {t.get("tenantId"): t for t in api.listar("cloudConnect/tenants")
                   if _norm(t.get("name")) in nomes_cc}
        por_tenant: dict[Any, dict] = {}
        for q in api.listar("cloudConnect/tenants/tenantQuotas"):
            t = tenants.get(q.get("tenantId"))
            if not t:
                continue
            acc = por_tenant.setdefault(q.get("tenantId"), {
                "nome": t.get("name"), "cota_bytes": 0, "usado_bytes": 0,
                "servidores": 0, "vms": 0, "estacoes": 0, "ultima_atividade": None,
            })
            acc["cota_bytes"] += (q.get("quotaMb") or 0) * 1024**2
            acc["usado_bytes"] += (q.get("usedQuotaMb") or 0) * 1024**2
            acc["servidores"] += int(q.get("serversCount") or 0)
            acc["vms"] += int(q.get("protectedVmsCount") or 0)
            acc["estacoes"] += int(q.get("workstationsCount") or 0)
            atv = data_iso(q.get("lastBackupActivityTime"))
            if atv and (acc["ultima_atividade"] is None or atv > acc["ultima_atividade"]):
                acc["ultima_atividade"] = atv
        cloud = list(por_tenant.values())
        for t in tenants.values():
            if t.get("tenantId") not in por_tenant:
                cloud.append({"nome": t.get("name"), "cota_bytes": 0, "usado_bytes": 0, "servidores": 0,
                              "vms": 0, "estacoes": 0, "ultima_atividade": None})
        faltando = nomes_cc - {_norm(t.get("name")) for t in tenants.values()}
        for n in sorted(faltando):
            avisos.append(f"Tenant Cloud Connect '{n}' não encontrado no Veeam ONE.")

    # --- alarmes de backup do período ligados ao cliente:
    #     origem na infraestrutura de backup (Vbr*) e que citem entre aspas um job/máquina
    #     do cliente, ou que venham do VBR/repositório/tenant dedicado a ele
    citados = {f'"{_norm(n)}"' for n in [j["nome"] for j in jobs] + [m["nome"] for m in lista_maquinas]}
    origens_dedicadas = repos_cliente | nomes_cc | {_norm(nome_srv[s]) for s in ids_dedicados}
    ruido = re.compile(r"is available|security vulnerability|license|out-of-date|"
                       r"security & compliance|analytics service", re.I)
    alarmes = []
    try:
        for a in api.listar("alarms/triggeredAlarms"):
            quando = data_iso(a.get("triggeredTime"))
            fonte = a.get("alarmSource") or {}
            if (not quando or quando < inicio_periodo
                    or not str(fonte.get("objectType") or "").startswith("Vbr")
                    or ruido.search(a.get("name") or "")):
                continue
            origem = fonte.get("objectName") or ""
            texto = _norm(a.get("description"))
            if _norm(origem) not in origens_dedicadas and not any(c in texto for c in citados):
                continue
            alarmes.append({
                "nome": a.get("name") or "-",
                "status": a.get("status") or "-",
                "data": quando,
                "origem": origem,
                "tipo_origem": (a.get("alarmSource") or {}).get("objectType") or "",
                "descricao": a.get("description") or "",
                "repeticoes": a.get("repeatCount") or 0,
            })
    except ErroVeeam as exc:
        avisos.append(f"Alarmes indisponíveis: {exc}")
    alarmes.sort(key=lambda a: a["data"], reverse=True)

    for sid in {s for s in servidores if s in ids_dedicados or any(
            nome_srv[s] == j["servidor"] for j in jobs)}:
        estado = servidores[sid].get("connectionState")
        if estado != "Connected":
            avisos.append(f"VBR {nome_srv[sid]} está '{estado}' no Veeam ONE; dados podem estar desatualizados.")

    if not jobs:
        avisos.append("Nenhum job do Veeam encontrado para o cliente (confira clientes.py).")

    return {
        "jobs": jobs,
        "maquinas": lista_maquinas,
        "repositorios": repositorios,
        "cloud_connect": cloud,
        "alarmes": alarmes,
        "avisos": avisos,
    }
