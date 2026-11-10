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
  webview/...                           relatórios do Report Catalog (prévia do Web Client):
                                        Protected VMs Job Schedule e Job Configuration Dump
                                        -> agendamento dos jobs

Sessões de job (/sessions) exigem papel acima de Read-Only, por isso o histórico vem do
estado de cada job e dos restore points.
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

import config
import evidencias

log = logging.getLogger(__name__)

_PAGINA = 500
# Relatórios do Report Catalog (uid fixo do template) usados para o agendamento dos jobs:
#   Protected VMs Job Schedule: colunas Schedule / Time / Runs On, só jobs de backup de VM
#   Job Configuration Dump: configuração de todos os jobs (inclui agentes); usado no que faltar
_REL_AGENDA_VMS = ("6235f7c9-d2eb-4f75-83f5-4571aeb215b5", [
    {"name": "RootIDsXml", "value": "<root><id>1000</id></root>"},  # toda a infraestrutura virtual
    {"name": "ScopeVCD", "value": ""}, {"name": "BusinessView", "value": ""},
    {"name": "JobType", "value": "-1"}, {"name": "ExcludeMask", "value": ""}, {"name": "SID", "value": ""},
    {"name": "ShowTemplates", "value": False}, {"name": "trees", "value": "scopeVbr"},
])
_REL_CONFIG_JOBS = ("476089d3-91aa-45ad-a62c-136261f6f96a", [
    {"name": "Scope", "value": "<root><id>1002</id></root>"},  # todos os servidores de backup
    {"name": "JobType", "value": "-1"}, {"name": "JobIds", "value": "<root><id>-1</id></root>"},
    {"name": "Options2Show", "value": "<root><id>-1</id></root>"},
])
_cache_agenda: dict | None = None
_chamadas_agenda: list[dict] = []
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
             form: dict | None = None, auth: bool = True, corpo_json: Any = None) -> Any:
        url = self.url + caminho + ("?" + urllib.parse.urlencode(params) if params else "")
        cab = {"Accept": "application/json"}
        corpo = None
        if form is not None:
            corpo = urllib.parse.urlencode(form).encode()
            cab["Content-Type"] = "application/x-www-form-urlencoded"
        elif corpo_json is not None:
            corpo = json.dumps(corpo_json).encode()
            cab["Content-Type"] = "application/json"
        if auth:
            cab["Authorization"] = f"Bearer {self._obter_token()}"
        req = urllib.request.Request(url, data=corpo, headers=cab, method=metodo)
        inicio = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=config.VEEAM_TIMEOUT_SEGUNDOS,
                                        context=self.ssl) as r:
                dados = r.read()
        except urllib.error.HTTPError as exc:
            evidencias.registrar("Veeam ONE", metodo, caminho, inicio, None, exc.code)
            if exc.code == 401 and auth and self._token:
                self._token = None
                return self._req(metodo, caminho, params, form, auth, corpo_json)
            raise ErroVeeam(f"{metodo} {caminho} -> HTTP {exc.code}: "
                            f"{exc.read().decode('utf-8', 'replace')[:200]}") from exc
        except urllib.error.URLError as exc:
            raise ErroVeeam(f"{metodo} {url} -> {exc.reason}") from exc
        resposta = json.loads(dados) if dados else None
        evidencias.registrar("Veeam ONE", metodo, caminho, inicio, resposta)
        return resposta

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

    def relatorio(self, uid: str, parametros: list[dict], secao: str = "table_details") -> list[dict]:
        """Linhas de uma tabela de relatório do Report Catalog, pela API da prévia do Web Client:
        abre sessão, dispara o dataset, espera ficar pronto e lê a seção."""
        base = f"/api/{self.versao}/webview"
        sessao = self._req("POST", f"{base}/sessions/start")
        try:
            tarefa = self._req("POST", f"{base}/report/tasks/{uid}/dataset/dsData/start",
                               corpo_json={"parameters": parametros, "session": sessao, "forceUpdate": False})
            st = tarefa
            for _ in range(150):
                st = self._req("GET", f"{base}/report/tasks/{tarefa['id']}/status")
                if st.get("state") != "Running":
                    break
                time.sleep(2)
            res = st.get("result") or {}
            if st.get("state") != "Completed" or not res.get("success"):
                raise ErroVeeam(f"relatório {uid} não concluiu ({st.get('state')}): {res.get('error')}")
            dados = self._req("POST", f"{base}/report/tasks/{uid}/sections/{secao}/data",
                              {"Offset": 0, "Limit": 100000},
                              corpo_json={"session": sessao, "resourceId": res["data"]["resourceId"]})
        finally:
            try:
                self._req("POST", f"{base}/sessions/finish", corpo_json=sessao)
            except ErroVeeam:
                pass
        return (dados or {}).get("items") or []

    def agendamentos(self) -> dict[tuple[str, str], tuple[str, str]]:
        """(servidor VBR, job) -> (agendamento, fonte com os valores crus). Vale para a execução toda."""
        global _cache_agenda, _chamadas_agenda
        if _cache_agenda is not None:
            evidencias.CHAMADAS.extend(_chamadas_agenda)  # mesmas chamadas valem para este cliente
            return _cache_agenda
        n_antes = len(evidencias.CHAMADAS)
        agenda: dict[tuple[str, str], tuple[str, str]] = {}
        for x in self.relatorio(*_REL_AGENDA_VMS):
            agenda[(_norm(x.get("bs_name")), _norm(x.get("job_name")))] = (
                formatar_agenda(x.get("schedule_type"), x.get("time"), x.get("runs_on")),
                f"Protected VMs Job Schedule: {x.get('schedule_type')} | {x.get('time')} | {x.get('runs_on')}")
        try:
            config_jobs: dict[tuple[str, str], dict] = {}
            for x in self.relatorio(*_REL_CONFIG_JOBS):
                if x.get("wizard_page") == "Schedule":
                    chave = (_norm(x.get("backup_server")), _norm(x.get("job_name")))
                    config_jobs.setdefault(chave, {})[x.get("property")] = x.get("setting") or ""
            for chave, props in config_jobs.items():
                crus = "; ".join(f"{k}={v}" for k, v in props.items() if k.startswith(("Run", "Start", "Day",
                                                                                     "Schedule", "Parent")))
                agenda.setdefault(chave, (formatar_config(props), f"Job Configuration Dump: {crus}"))
        except ErroVeeam as exc:
            log.warning("Job Configuration Dump indisponível (agendamento de agentes): %s", exc)
        _cache_agenda = agenda
        _chamadas_agenda = evidencias.CHAMADAS[n_antes:]
        return agenda


# ----------------------------------------------------------------- utilidades

_DIAS = {"monday": "segunda", "tuesday": "terça", "wednesday": "quarta", "thursday": "quinta",
         "friday": "sexta", "saturday": "sábado", "sunday": "domingo"}
_ORDINAL = {"first": "1º", "second": "2º", "third": "3º", "fourth": "4º", "last": "último"}


def _hora(texto: str) -> str:
    """'at 18:30:00' / 'at 6:30:00 PM' -> '18:30'."""
    m = re.search(r"(\d{1,2}):(\d{2})(?::\d{2})?\s*([AP]M)?", texto or "", re.I)
    if not m:
        return (texto or "").strip()
    h = int(m.group(1))
    if m.group(3):
        h = h % 12 + (12 if m.group(3).upper() == "PM" else 0)
    return f"{h:02d}:{m.group(2)}"


def formatar_agenda(tipo: str | None, hora: str | None, dias: str | None) -> str:
    """Colunas Schedule / Time / Runs On do relatório -> 'Diário às 22:00', 'A cada 4 horas'..."""
    tipo, hora, dias = (tipo or "").strip(), (hora or "").strip(), (dias or "").strip()
    t = tipo.lower()
    if t == "periodically":
        m = re.search(r"(\d+)\s*(hour|minute)", hora, re.I)
        if m:
            n, un = int(m.group(1)), "hora" if m.group(2).lower() == "hour" else "minuto"
            return f"A cada {n} {un}" + ("s" if n != 1 else "")
        return hora or "Periódico"
    h = _hora(hora)
    if t == "daily":
        if dias.lower() in ("everyday", "every day", "", "n/a"):
            return f"Diário às {h}"
        nomes = [_DIAS.get(d.strip().lower(), d.strip()) for d in re.split(r"[,;]", dias) if d.strip()]
        return f"{', '.join(n.capitalize() for n in nomes)} às {h}"
    if t == "monthly":
        m = re.match(r"(\w+)\s+(\w+)", dias)
        if m and m.group(1).lower() in _ORDINAL:
            return f"{_ORDINAL[m.group(1).lower()]} {_DIAS.get(m.group(2).lower(), m.group(2))} do mês às {h}"
        m = re.search(r"(\d+)", dias)
        return f"Dia {m.group(1)} de cada mês às {h}" if m else f"Mensal às {h}"
    if not t or t in ("n/a", "not scheduled", "none"):
        return "Manual"
    return " ".join(x for x in (tipo, hora, dias) if x and x != "N/A")


_SEMANA = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_ABREV = {"monday": "seg", "tuesday": "ter", "wednesday": "qua", "thursday": "qui",
          "friday": "sex", "saturday": "sáb", "sunday": "dom"}


def _faixa_dias(dias: list[str]) -> str:
    """['monday', ..., 'saturday'] -> 'seg a sáb'; todos -> ''; soltos -> 'seg, qua'."""
    idx = sorted({_SEMANA.index(d) for d in dias if d in _SEMANA})
    if len(idx) == 7 or not idx:
        return ""
    if len(idx) > 2 and idx == list(range(idx[0], idx[-1] + 1)):
        return f"{_ABREV[_SEMANA[idx[0]]]} a {_ABREV[_SEMANA[idx[-1]]]}"
    return ", ".join(_ABREV[_SEMANA[i]] for i in idx)


def formatar_config(p: dict) -> str:
    """Página 'Schedule' do Job Configuration Dump -> mesmo formato de formatar_agenda."""
    if p.get("Run automatically after defined job") == "True":
        return f"Após {p.get('Parent schedule job') or 'outro job'}"
    if p.get("Run job automatically") == "False":
        return "Manual"
    if p.get("Run the job monthly option") == "True":
        ordem, dia = p.get("Day number in month to run the job", ""), p.get("Day of week to run the job", "")
        return formatar_agenda("Monthly", p.get("Run the job monthly at this time"), f"{ordem} {dia}")
    if p.get("Run the job daily option") == "True":
        hora = p.get("Start jobs daily at this time")
        dias = [d.strip().lower() for d in (p.get("Days to run the job") or "").split(";") if d.strip()]
        if p.get("Schedule daily option kind") == "Everyday" or len(set(dias)) == 7 or not dias:
            return f"Diário às {_hora(hora)}"
        faixa = _DIAS.get(dias[0], dias[0]) if len(set(dias)) == 1 else _faixa_dias(dias)
        return f"{faixa[:1].upper() + faixa[1:]} às {_hora(hora)}"
    if p.get("Run job periodically every") == "True":
        texto = formatar_agenda("Periodically", "Every " + (p.get("Run job periodically period") or ""), "")
        # 'Monday: 6-23; Tuesday: 6-23; ...' -> dias e janela (quando a janela é igual em todos)
        janela = {}
        for parte in (p.get("Run job periodically schedule") or "").split(";"):
            if ":" in parte:
                dia, horas = parte.split(":", 1)
                janela[dia.strip().lower()] = horas.strip()
        extras = []
        faixa = _faixa_dias(list(janela))
        if faixa:
            extras.append(faixa)
        if len(set(janela.values())) == 1:
            h = next(iter(janela.values()))
            if h.lower() != "all day":
                extras.append(", ".join(f"{a}h–{b}h" for a, b in
                                        (x.strip().split("-") for x in h.split(",") if "-" in x)))
        return texto + (f" ({', '.join(extras)})" if extras else "")
    return "—"


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
    try:
        agenda = api.agendamentos()
    except ErroVeeam as exc:
        agenda = {}
        avisos.append(f"Agendamento dos jobs indisponível: {exc}")
    for j in jobs:
        j["agendamento"], j["agendamento_fonte"] = agenda.get((_norm(j["servidor"]), _norm(j["nome"])),
                                                              (None, None))
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
        "avisos": avisos,
    }
