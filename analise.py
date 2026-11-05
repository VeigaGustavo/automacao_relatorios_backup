"""Regras do relatório: transforma os dados coletados (Veeam ONE + SCP) no conteúdo de cada seção.

Independente do formato de saída. Cada valor com cor vem com um nível:
  ok (verde), alerta (amarelo), critico (vermelho), neutro (cinza).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import config

BRT = timezone(timedelta(hours=-3))


# ------------------------------------------------------------------ formatação

def fmt_bytes(n) -> str:
    if n is None:
        return "—"
    n = float(n)
    if n < 1024:
        return f"{n:.0f} B"
    for unidade in ("KB", "MB", "GB", "TB", "PB"):
        n /= 1024
        if n < 1024 or unidade == "PB":
            return f"{n:,.2f} {unidade}".replace(",", "X").replace(".", ",").replace("X", ".")
    return "—"


def fmt_data(d: datetime | None, hora: bool = True) -> str:
    if not d:
        return "—"
    return d.astimezone(BRT).strftime("%d/%m/%Y %H:%M" if hora else "%d/%m/%Y")


def fmt_pct(x: float) -> str:
    return f"{x:.0f}%"


# ------------------------------------------------------------------ situação do último backup

def _nivel_veeam(m: dict, job: dict | None, agora: datetime) -> str:
    """Em dia = recebeu cópia na última execução do job (vale para jobs diários e semanais)."""
    d = m["ultimo_backup"]
    if not d or agora - d > timedelta(days=config.DIAS_CRITICO_BACKUP):
        return "critico"
    if job and job["ultima_execucao"] and job["status"] != "Running":
        if d < job["ultima_execucao"] - timedelta(hours=2):
            return "alerta"
    return "ok"


def _nivel_scp(d: datetime | None, agora: datetime) -> str:
    if not d:
        return "critico"
    horas = (agora - d).total_seconds() / 3600
    if horas <= config.HORAS_ALERTA_BACKUP:
        return "ok"
    return "alerta" if horas <= config.DIAS_CRITICO_BACKUP * 24 else "critico"


# ------------------------------------------------------------------ montagem

def montar(dados: dict) -> dict:
    cli, v, s, agora = dados["cliente"], dados["veeam"], dados["scp"], dados["coletado_em"]
    inicio = agora - timedelta(days=config.DIAS_PERIODO)

    jobs = {j["nome"]: j for j in v["jobs"] if j["ativo"]}
    maquinas = [m for m in v["maquinas"] if m["job"] in jobs]
    vms = [vm for t in s["tenants"] for vm in t["vms"]]

    # Servidores: Veeam e nuvem (SCP) numa tabela só; o job é uma coluna
    servidores = []
    for m in maquinas:
        servidores.append({
            "nome": m["nome"], "job": m["job"], "ultimo": fmt_data(m["ultimo_backup"]),
            "nivel": _nivel_veeam(m, jobs.get(m["job"]), agora),
            "copias": m["restore_points"], "espaco": m["backup_bytes"],
        })
    for vm in vms:
        sem = not vm["politicas"] and not vm["pontos"]
        servidores.append({
            "nome": vm["nome"], "job": ", ".join(vm["politicas"]) or "Sem backup configurado",
            "ultimo": "Sem backup" if sem else fmt_data(vm["ultimo_backup"]),
            "nivel": "critico" if sem else _nivel_scp(vm["ultimo_backup"], agora),
            "copias": vm["pontos"], "espaco": vm["backup_bytes"],
        })
    servidores.sort(key=lambda x: (x["nome"].lower(), x["job"].lower()))

    # Armazenamento Veeam: em repositório compartilhado, Used Space = só os backups do cliente
    uso_cliente: dict[str, float] = {}
    for m in maquinas:
        uso_cliente[m["repositorio"].upper()] = uso_cliente.get(m["repositorio"].upper(), 0) + m["backup_bytes"]
    armazenamento = []
    for r in v["repositorios"]:
        usado = r["usado_bytes"] if r["exclusivo"] else uso_cliente.get(r["nome"].upper(), 0)
        if not usado and not r["exclusivo"]:
            continue  # compartilhado sem backups deste cliente
        armazenamento.append({"nome": r["nome"], "usado": usado, "livre": r["livre_bytes"]})
    for c in v["cloud_connect"]:
        if not c["cota_bytes"] and not c["usado_bytes"]:
            continue  # tenant sem cota e sem uso: não contratado
        armazenamento.append({"nome": f"{c['nome']} (cópia externa)", "usado": c["usado_bytes"],
                              "livre": max(c["cota_bytes"] - c["usado_bytes"], 0) if c["cota_bytes"] else None})
    usado_veeam = sum(x["usado"] for x in armazenamento)

    # Painel
    nomes_veeam = {m["nome"].upper() for m in maquinas}
    vol_veeam = sum(m["backup_bytes"] for m in maquinas)
    vol_scp = sum(vm["backup_bytes"] for vm in vms)
    quota = sum(t["quota_bytes"] for t in s["tenants"])
    # Cartões: (rótulo, valor, cor, linhas de detalhe)
    painel = [
        ("Repositório (em uso)", fmt_bytes(quota + usado_veeam), "azul",
         [f"Nuvem {fmt_bytes(quota)}", f"Veeam {fmt_bytes(usado_veeam)}"] if usado_veeam and quota else []),
        ("VMs", str(len(vms) + len(nomes_veeam)), "escuro",
         [f"Nuvem {len(vms)}", f"Veeam {len(nomes_veeam)}"] if vms and nomes_veeam else []),
        ("Disco alocado", fmt_bytes(sum(vm["disco_bytes"] for vm in vms)), "escuro", ["servidores na nuvem"]),
        ("Total de backups", fmt_bytes(vol_scp + vol_veeam), "azul",
         [f"Nuvem {fmt_bytes(vol_scp)}", f"Veeam {fmt_bytes(vol_veeam)}"] if vol_scp and vol_veeam else []),
    ]

    # Contratado x consumo real (mesma medição do automacao_veeam)
    contrato = cli.get("contrato") or {}
    consumo = []
    for item, contratado, real, fmt in [
        ("Servidores em backup", contrato.get("servidores"), len(nomes_veeam), lambda n: f"{n:.0f}"),
        ("Volume de backup", (contrato.get("volume_gb") or 0) * 1024**3 or None, usado_veeam, fmt_bytes),
    ]:
        if not contratado:
            continue
        pct = 100 * real / contratado
        consumo.append({"item": item, "contratado": fmt(contratado), "real": fmt(real), "pct": fmt_pct(pct),
                        "nivel": "critico" if pct > 100 else "alerta" if pct >= 85 else "ok"})

    return {
        "cliente": cli["nome"],
        "razao_social": cli.get("razao_social") or cli["nome"],
        "periodo": f"{fmt_data(inicio, False)} a {fmt_data(agora, False)}",
        "painel": painel,
        "consumo": consumo,
        "servidores": servidores,
        "armazenamento": armazenamento,
    }
