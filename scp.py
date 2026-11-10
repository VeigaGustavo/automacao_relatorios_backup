"""Coleta dos backups de um tenant na Open-API do SCP (Sangfor Cloud Platform).

Mesma assinatura EC2 (AWS4-HMAC-SHA256) e mesmos endpoints do scp-console v1.3.1:
  GET /janus/20180725/projects                     tenants
  GET /janus/20200725/servers?project_id=          VMs (fallback 20180725)
  GET /janus/20180725/backup-policies[/{id}]       políticas e retenção
  GET /janus/20180725/servers/{id}/backups         pontos de backup de cada VM (size em KB)
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import config
import evidencias

log = logging.getLogger(__name__)

_CONCORRENCIA = 8
_SSL = ssl._create_unverified_context()  # SCP com certificado autoassinado
_TZ_SCP = dt.timezone(dt.timedelta(hours=-3))  # horários do SCP vêm no fuso local (Brasília)


class ErroSCP(RuntimeError):
    pass


def _assinar(chave: bytes, msg: str) -> bytes:
    return hmac.new(chave, msg.encode(), hashlib.sha256).digest()


def _cabecalhos_ec2(metodo: str, url: str) -> dict[str, str]:
    """Igual ao create_ec2_auth do proxy do scp-console (query string vazia na assinatura)."""
    agora = dt.datetime.now(dt.timezone.utc)
    amz_date = agora.strftime("%Y%m%dT%H%M%SZ")
    dia = agora.strftime("%Y%m%d")
    canonical = "\n".join([
        metodo.upper(),
        urllib.parse.urlparse(url).path or "/",
        "",
        f"x-amz-date:{amz_date}\n",
        "path;x-amz-date",
        hashlib.sha256(b"").hexdigest(),
    ])
    escopo = f"{dia}/{config.SCP_REGION}/{config.SCP_SERVICE}/aws4_request"
    a_assinar = "\n".join(["AWS4-HMAC-SHA256", amz_date, escopo,
                           hashlib.sha256(canonical.encode()).hexdigest()])
    k = _assinar(("AWS4" + config.SCP_SECRET_KEY).encode(), dia)
    for parte in (config.SCP_REGION, config.SCP_SERVICE, "aws4_request"):
        k = _assinar(k, parte)
    assinatura = hmac.new(k, a_assinar.encode(), hashlib.sha256).hexdigest()
    return {
        "Authorization": (f"AWS4-HMAC-SHA256 Credential={config.SCP_ACCESS_KEY}/{escopo}, "
                          f"SignedHeaders=path;x-amz-date, Signature={assinatura}"),
        "X-Amz-Date": amz_date,
    }


def _get(caminho: str, **params: Any) -> Any:
    if not (config.SCP_BASE_URL and config.SCP_ACCESS_KEY and config.SCP_SECRET_KEY):
        raise ErroSCP("Preencha SCP_BASE_URL, SCP_ACCESS_KEY e SCP_SECRET_KEY no .env.")
    params = {k: v for k, v in params.items() if v is not None and v != ""}
    url = config.SCP_BASE_URL + caminho + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Accept": "application/json", **_cabecalhos_ec2("GET", url)})
    inicio = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=config.SCP_TIMEOUT_SEGUNDOS, context=_SSL) as r:
            resposta = json.loads(r.read() or b"null")
        evidencias.registrar("Sangfor SCP", "GET", caminho, inicio, resposta)
        return resposta
    except urllib.error.HTTPError as exc:
        evidencias.registrar("Sangfor SCP", "GET", caminho, inicio, None, exc.code)
        raise ErroSCP(f"GET {caminho} -> HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}") from exc
    except urllib.error.URLError as exc:
        raise ErroSCP(f"GET {url} -> {exc.reason}") from exc


def _lista(resp: Any) -> list[dict]:
    """Formato Sangfor: {code, message, data: {data: [...]}} (com variações)."""
    if isinstance(resp, list):
        return resp
    d = (resp or {}).get("data")
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for chave in ("data", "list", "items", "projects"):
            if isinstance(d.get(chave), list):
                return d[chave]
    return []


def _todas_paginas(caminho: str, **params: Any) -> list[dict]:
    itens, pagina = [], 0
    for _ in range(1000):
        resp = _get(caminho, **params, page_num=pagina, page_size=100)
        itens.extend(_lista(resp))
        prox = ((resp or {}).get("data") or {}).get("next_page_num") if isinstance(resp, dict) else None
        if prox in (None, "") or not str(prox).isdigit() or int(prox) <= pagina:
            break
        pagina = int(prox)
    return itens


def _data_scp(valor: Any) -> dt.datetime | None:
    if not valor:
        return None
    if isinstance(valor, (int, float)):
        return dt.datetime.fromtimestamp(valor, dt.timezone.utc) if valor > 0 else None
    try:
        return dt.datetime.strptime(str(valor)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_TZ_SCP)
    except ValueError:
        return None


# ------------------------------------------------------------- formatações

_HORAS = {"h": 1, "d": 24, "w": 168, "m": 720, "y": 8760}


def formatar_retencao(ret: dict | None) -> str:
    """Mesma regra do scp-console: maior retenção entre as camadas, em dias.

    Valores "N" = quantidade de backups; "N,unidade" = período ("3,d" = 3 dias).
    """
    ret = ret or {}
    camadas = {"all": None, "day": "d", "week": "w", "month": "m", "year": "y"}
    dias, ultimos = 0, None
    for chave, unidade_padrao in camadas.items():
        v = ret.get(chave)
        if v is None or v == "" or str(v) == "-1":
            continue
        partes = [p.strip() for p in str(v).split(",")]
        n, unidade = partes[0], (partes[1] if len(partes) > 1 else unidade_padrao)
        horas = _HORAS.get((unidade or "").lower())
        try:
            if horas:
                dias = max(dias, -(-int(float(n) * horas) // 24))
            else:
                ultimos = n
        except ValueError:
            continue
    if dias:
        return f"{dias} {'dia' if dias == 1 else 'dias'}"
    if ultimos:
        return "Último backup" if str(ultimos) == "1" else f"Últimos {ultimos} backups"
    return "Não configurada"


_DIAS_SEMANA = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"]


def formatar_cron(cron: str | None) -> str:
    """Quartz (seg min hora dia mês dia-semana): '0 0 20 * * ?' -> 'Diário às 20:00'."""
    f = str(cron or "").split()
    if len(f) < 6 or not f[1].isdigit() or not f[2].isdigit():
        return cron or "-"
    hora = f"{int(f[2]):02d}:{int(f[1]):02d}"
    dom, dow = f[3], f[5]
    if dow not in ("?", "*"):
        dias = [(_DIAS_SEMANA[int(x) - 1] if x.isdigit() and 1 <= int(x) <= 7 else x) for x in dow.split(",")]
        return f"{', '.join(dias)} às {hora}"
    if dom not in ("?", "*"):
        return f"Dia {dom} de cada mês às {hora}"
    return f"Diário às {hora}"


# ------------------------------------------------------------- coleta

def _politicas(projeto_id: str, ids_vms: set[str]) -> list[dict]:
    """Políticas do tenant + políticas de outros projetos que protegem VMs dele."""
    todas = _todas_paginas("/janus/20180725/backup-policies")
    proprias = _todas_paginas("/janus/20180725/backup-policies", project_id=projeto_id)
    por_id: dict[str, dict] = {}
    for p in proprias + todas:
        por_id[p.get("id") or p.get("name")] = {**por_id.get(p.get("id") or p.get("name"), {}), **p}

    def detalhar(p: dict) -> dict:
        if isinstance(p.get("resources"), list) or not p.get("id"):
            return p
        try:
            return {**p, **(_get(f"/janus/20180725/backup-policies/{p['id']}").get("data") or {})}
        except ErroSCP as exc:
            log.warning("Falha ao detalhar politica %s: %s", p.get("id"), exc)
            return p

    with ThreadPoolExecutor(_CONCORRENCIA) as ex:
        detalhadas = list(ex.map(detalhar, por_id.values()))
    escolhidas = [p for p in detalhadas
                  if str(p.get("project_id")) == str(projeto_id)
                  or any(str(r.get("id")) in ids_vms for r in p.get("resources") or [])]
    return sorted(escolhidas, key=lambda p: str(p.get("name")).lower())


def coletar(cliente_cfg: dict) -> dict:
    if not cliente_cfg.get("scp_tenants"):
        return {"tenants": [], "avisos": []}  # cliente sem nuvem Sangfor
    projetos = _lista(_get("/janus/20180725/projects"))
    avisos: list[str] = []
    tenants = []
    for nome in cliente_cfg.get("scp_tenants") or []:
        proj = next((p for p in projetos if (p.get("name") or "").strip().upper() == nome.strip().upper()), None)
        if not proj:
            avisos.append(f"Tenant '{nome}' não encontrado no SCP.")
            continue
        tenants.append(_coletar_tenant(proj, avisos))
    return {"tenants": tenants, "avisos": avisos}


def _coletar_tenant(proj: dict, avisos: list[str]) -> dict:
    pid = proj["id"]
    try:
        servidores = _todas_paginas("/janus/20200725/servers", project_id=pid)
    except ErroSCP as exc:
        log.warning("Listagem 20200725 indisponivel (%s); usando 20180725.", exc)
        servidores = _todas_paginas("/janus/20180725/servers", project_id=pid)

    ids = {str(s.get("id")) for s in servidores}
    try:
        politicas = _politicas(pid, ids)
    except ErroSCP as exc:
        politicas = []
        avisos.append(f"Políticas de backup indisponíveis ({proj.get('name')}): {exc}")

    def pontos(vm: dict) -> dict:
        try:
            lista = _lista(_get(f"/janus/20180725/servers/{vm['id']}/backups"))
        except ErroSCP as exc:
            log.warning("Backups da VM %s indisponiveis: %s", vm.get("name"), exc)
            return {"erro": str(exc), "pontos": []}
        return {"erro": None, "pontos": lista}

    with ThreadPoolExecutor(_CONCORRENCIA) as ex:
        backups = list(ex.map(pontos, servidores))

    # File Storage em uso: mesma regra do fetchFileStorageQuota do scp-console
    quota_bytes, quota_erro = 0.0, None
    try:
        itens = _lista(_get("/janus/20180725/quotas", project_id=pid, namespace="storage"))
        de_storage = [i for i in itens if any(
            k in str(i.get(c) or "").lower() for c in ("namespace", "resource", "name") for k in ("storage", "nas"))]
        for item in de_storage or itens:
            for sq in item.get("separated_quotas") or []:
                quota_bytes += float(sq.get("in_use") or 0)
    except ErroSCP as exc:
        quota_erro = str(exc)
        avisos.append(f"Quota de storage indisponível ({proj.get('name')}): {exc}")

    vms = []
    for vm, bk in zip(servidores, backups):
        pts = bk["pontos"]
        datas = [d for d in (_data_scp(p.get("time")) for p in pts) if d]
        # size dos pontos vem em KB (mesma regra do scp-console); 20200725 traz o total em bytes
        if vm.get("backup_total_byte") is not None:
            total_bytes = float(vm["backup_total_byte"] or 0)
        else:
            total_bytes = sum(float(p.get("size") or 0) for p in pts) * 1024
        nomes_pol = [p.get("name") or p.get("id") for p in politicas
                     if any(str(r.get("id")) == str(vm.get("id")) for r in p.get("resources") or [])]
        disco_mb = sum(float(d.get("size_mb") or 0) for d in vm.get("disks") or [])
        vms.append({
            "nome": vm.get("name") or vm.get("id"),
            "status": vm.get("status") or "-",
            "so": vm.get("os_name") or vm.get("os_type") or "-",
            "disco_bytes": disco_mb * 1024**2,
            # espaço usado nos discos da VM (storage_status.used_mb): o dado de origem (Front-End)
            "usado_bytes": float((vm.get("storage_status") or {}).get("used_mb") or 0) * 1024**2 or None,
            "politicas": nomes_pol,
            "pontos": len(pts),
            "primeiro_backup": min(datas) if datas else None,
            "ultimo_backup": max(datas) if datas else _data_scp(vm.get("latest_backup_time")),
            "backup_bytes": total_bytes,
            "erro": bk["erro"],
        })
    vms.sort(key=lambda v: str(v["nome"]).lower())

    lista_pol = []
    for p in politicas:
        sched = p.get("scheduled_operation") or {}
        lista_pol.append({
            "nome": p.get("name") or p.get("id"),
            "tipo": p.get("type") or "-",
            "ativa": str(p.get("enable")) == "1",
            "agendamento": formatar_cron(p.get("trigger_cron")),
            "cron": p.get("trigger_cron"),
            "retencao": formatar_retencao(sched.get("retention")),
            "janela_h": sched.get("duration_hours"),
            "vms": sum(1 for r in p.get("resources") or [] if str(r.get("id")) in ids),
            "do_tenant": str(p.get("project_id")) == str(pid),
        })
    return {"id": pid, "nome": proj.get("name"), "ativo": str(proj.get("enabled")) == "1",
            "vms": vms, "politicas": lista_pol, "quota_bytes": quota_bytes, "quota_erro": quota_erro}
