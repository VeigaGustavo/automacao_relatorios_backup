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


# ------------------------------------------------------------------ volumetria contratada

# Onde o volume do contrato é medido: (rótulo no relatório, explicação para o cliente)
#   frontend = On-premise: dados nos servidores do cliente (origem)
#   backend  = Cloud: cópias de backup no armazenamento de destino
MEDICAO = {
    "frontend": ("On-premise", "espaço usado nos discos dos servidores protegidos (origem)"),
    "backend": ("Cloud", "espaço ocupado pelas cópias de backup no armazenamento de destino, "
                         "com todas as cópias da retenção"),
}


def medir_volume(vol: dict, maquinas: list[dict], usado_backend: float, agora: datetime) -> dict:
    """Mede um volume do contrato conforme vol["medicao"]:

    backend   espaço usado no destino: cota em uso da nuvem Sangfor (SCP) + repositórios Veeam do
              cliente (compartilhado = só os backups dele) + cota usada do Cloud Connect.
              Mesmo valor do cartão "Repositório (em uso)".
    frontend  dados na origem: usedSourceSizeBytes de cada máquina protegida (ver
              frontend_por_servidor). vol["maquinas"] restringe às
              máquinas cobertas pelo contrato. Cloud Connect não entra: a origem fica no VBR do cliente.
    """
    medicao = (vol.get("medicao") or "").strip().lower()
    if medicao not in MEDICAO:
        raise ValueError(f"contrato: medicao deve ser 'frontend' ou 'backend' (veio {vol.get('medicao')!r})")
    if medicao == "backend":
        return {"medicao": medicao, "bytes": usado_backend, "maquinas": {}}
    filtro = {m.upper() for m in vol.get("maquinas") or []}
    origem = frontend_por_servidor([(m["nome"], m["origem_bytes"], m["ultimo_backup"]) for m in maquinas
                                    if not filtro or m["nome"].upper() in filtro], agora)
    return {"medicao": medicao, "bytes": sum(origem.values()), "maquinas": origem,
            "faltando": sorted(filtro - set(origem))}


def frontend_por_servidor(linhas: list[tuple], agora: datetime) -> dict[str, float]:
    """(nome, bytes na origem, último backup) -> {NOME: bytes}, para somar o Front-End:
    - só servidores com backup nos últimos DIAS_PERIODO dias (os parados não estão sendo protegidos);
    - servidor em mais de uma linha (dois jobs, ou VM recriada com o mesmo nome) conta uma vez,
      com o valor da linha de backup mais recente."""
    limite = agora - timedelta(days=config.DIAS_PERIODO)
    escolhido: dict[str, tuple] = {}
    for nome, n, ultimo in linhas:
        if not n or not ultimo or ultimo < limite:
            continue
        atual = escolhido.get(nome.upper())
        if atual is None or ultimo > atual[1]:
            escolhido[nome.upper()] = (n, ultimo)
    return {nome: n for nome, (n, _) in escolhido.items()}


# ------------------------------------------------------------------ montagem

def montar(dados: dict) -> dict:
    cli, v, s, agora = dados["cliente"], dados["veeam"], dados["scp"], dados["coletado_em"]
    inicio = agora - timedelta(days=config.DIAS_PERIODO)

    jobs = {j["nome"]: j for j in v["jobs"] if j["ativo"]}
    maquinas = [m for m in v["maquinas"] if m["job"] in jobs]
    vms = [vm for t in s["tenants"] for vm in t["vms"]]

    politicas = {p["nome"]: p for t in s["tenants"] for p in t["politicas"]}

    # Servidores: Veeam e nuvem Sangfor (SCP); "origem" separa as tabelas no relatório
    # Retenção: SCP = configuração da política; Veeam = cópias guardadas (a API não expõe a configuração)
    servidores = []
    for m in maquinas:
        servidores.append({
            "origem": "Veeam", "nome": m["nome"], "job": m["job"], "ultimo": fmt_data(m["ultimo_backup"]),
            "agendamento": jobs[m["job"]].get("agendamento") or "—",
            "nivel": _nivel_veeam(m, jobs.get(m["job"]), agora),
            "copias": m["restore_points"], "espaco": m["backup_bytes"], "frontend": m["origem_bytes"] or None,
            "retencao": (f"{m['restore_points']} cópia" + ("s" if m["restore_points"] != 1 else ""))
                        if m["restore_points"] else "—",
            "bruto": m,
        })
    for vm in vms:
        sem = not vm["politicas"] and not vm["pontos"]
        servidores.append({
            "origem": "Sangfor", "nome": vm["nome"], "job": ", ".join(vm["politicas"]) or "Sem backup configurado",
            "ultimo": "Sem backup" if sem else fmt_data(vm["ultimo_backup"]),
            "agendamento": ", ".join(dict.fromkeys(politicas[p]["agendamento"] for p in vm["politicas"]
                                                   if p in politicas)) or "—",
            "nivel": "critico" if sem else _nivel_scp(vm["ultimo_backup"], agora),
            "copias": vm["pontos"], "espaco": vm["backup_bytes"], "frontend": vm.get("usado_bytes"),
            "retencao": next((politicas[p]["retencao"] for p in vm["politicas"] if p in politicas), "—"),
            "bruto": vm,
        })
    # Coluna de volume única, na medição do contrato (1º volume; sem volume contratado = Cloud)
    contrato = cli.get("contrato") or {}
    med_volume = next((str(vol.get("medicao") or "").strip().lower() for vol in contrato.get("volumes") or []),
                   "backend")
    for x in servidores:
        # Status binário: em dia (verde) ou sem backup (vermelho)
        x["sem_backup"] = x["ultimo"] in ("—", "Sem backup")
        x["status"] = "Em dia" if x["nivel"] == "ok" else "Sem backup"
        x["nivel"] = "ok" if x["nivel"] == "ok" else "critico"
        x["volume"] = x["frontend"] if med_volume == "frontend" else x["espaco"]
    servidores.sort(key=lambda x: (x["nome"].lower(), x["job"].lower()))

    # Uma tabela por origem (Veeam / Sangfor) + resumo no topo
    grupos = []
    for origem in ("Veeam", "Sangfor"):
        lista = [x for x in servidores if x["origem"] == origem]
        if not lista:
            continue
        if med_volume == "frontend":  # cada servidor uma vez, só os com backup no período
            volume = sum(frontend_por_servidor([(x["nome"], x["frontend"], x["bruto"]["ultimo_backup"])
                                                for x in lista], agora).values())
        else:
            volume = sum(x["espaco"] or 0 for x in lista)
        em_dia = sum(1 for x in lista if x["nivel"] == "ok")
        grupos.append({"origem": origem, "servidores": lista, "em_dia": em_dia,
                       "sem_backup": len(lista) - em_dia, "volume": volume})

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
    # Cloud Connect: o cliente roda o próprio VBR; só a contagem de máquinas da cota é visível
    qtd_cc = sum(c["servidores"] + c["vms"] + c["estacoes"] for c in v["cloud_connect"])
    qtd_veeam = len(nomes_veeam) + qtd_cc
    # Cartões: (rótulo, valor, cor, linhas de detalhe)
    painel = [
        ("Repositório (em uso)", fmt_bytes(quota + usado_veeam), "azul",
         [f"Nuvem {fmt_bytes(quota)}", f"Veeam {fmt_bytes(usado_veeam)}"] if usado_veeam and quota else []),
        ("VMs", str(len(vms) + qtd_veeam), "escuro",
         [f"Nuvem {len(vms)}", f"Veeam {qtd_veeam}"] if vms and qtd_veeam else []),
    ]
    if vms:  # disco alocado só existe para as VMs da nuvem Sangfor
        painel.append(("Disco alocado", fmt_bytes(sum(vm["disco_bytes"] for vm in vms)), "escuro",
                       ["servidores na nuvem"]))
    painel.append(("Total de backups", fmt_bytes(vol_scp + vol_veeam), "azul",
                   [f"Nuvem {fmt_bytes(vol_scp)}", f"Veeam {fmt_bytes(vol_veeam)}"] if vol_scp and vol_veeam else []))

    # Contratado x consumo real. Livre = contratado − backup utilizado; no Back-End o utilizado é o
    # mesmo total do cartão "Repositório (em uso)": cota em uso da nuvem Sangfor (SCP) + Veeam
    # Servidores: máquinas Veeam + VMs da nuvem com backup (política ou pontos; a sem backup não está protegida)
    qtd_scp_protegidas = sum(1 for vm in vms if vm["politicas"] or vm["pontos"])
    linhas_contrato = [("Servidores em backup", contrato.get("servidores"), qtd_veeam + qtd_scp_protegidas,
                        lambda n: f"{n:.0f}", None)]
    medicoes = []
    for vol in contrato.get("volumes") or []:
        med = medir_volume(vol, maquinas, quota + usado_veeam, agora)
        medicoes.append(med)
        linhas_contrato.append(("Volume de backup", (vol.get("gb") or 0) * 1024**3,
                                med["bytes"], fmt_bytes, med["medicao"]))
    consumo = []
    for item, contratado, real, fmt, medicao in linhas_contrato:
        if not contratado:
            continue
        pct = 100 * real / contratado
        nivel = "critico" if pct > 100 else "alerta" if pct >= 85 else "ok"
        # Livre = contratado − utilizado; acima do contratado mostra o excedente
        livre = (fmt(contratado - real), None) if real <= contratado else (f"Excedido {fmt(real - contratado)}",
                                                                            "critico")
        consumo.append({"item": item, "contratado": fmt(contratado), "real": fmt(real), "livre": livre,
                        "pct": fmt_pct(pct), "medicao": medicao, "nivel": nivel})

    return {
        "cliente": cli["nome"],
        "razao_social": cli.get("razao_social") or cli["nome"],
        "coletado_em": agora,
        "periodo": f"{fmt_data(inicio, False)} a {fmt_data(agora, False)}",
        "painel": painel,
        "consumo": consumo,
        "servidores": servidores,
        "medicao": med_volume,
        "grupos": grupos,
        "armazenamento": armazenamento,
        # valores intermediários, para o arquivo de evidências
        "calculo": {"quota": quota, "usado_veeam": usado_veeam, "vol_veeam": vol_veeam, "vol_scp": vol_scp,
                    "qtd_scp": len(vms), "qtd_veeam": qtd_veeam, "qtd_cc": qtd_cc,
                    "qtd_scp_protegidas": qtd_scp_protegidas,
                    "disco": sum(vm["disco_bytes"] for vm in vms), "armazenamento": armazenamento,
                    "medicoes": medicoes},
    }
