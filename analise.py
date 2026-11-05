"""Regras do relatório: transforma os dados coletados (Veeam ONE + SCP) no conteúdo de cada seção.

Independente do formato de saída. Cada valor com cor vem com um nível:
  ok (verde), alerta (amarelo), critico (vermelho), neutro (cinza).
"""

from __future__ import annotations

import re
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


# ------------------------------------------------------------------ erros

# Alarmes do Veeam ONE em linguagem do cliente: nome -> o que aconteceu
_ALARMES = {
    "backup job state": "Falha na execução do backup",
    "vm with no backup": "Servidor sem backup recente",
    "computer with no backup": "Servidor sem backup recente",
    "suspicious incremental backup size": "Tamanho do backup fora do padrão",
    "unusual job duration": "Duração do backup fora do padrão",
    "backup repository free space": "Pouco espaço no armazenamento de backup",
    "backup repository connection failure": "Armazenamento de backup inacessível",
    "job disabled": "Backup desativado",
    "cloud tenant quota usage": "Cota de cópia externa quase cheia",
}
_NORMALIZADO = re.compile(r"back to normal|resolved", re.I)


def _dias(d: datetime, agora: datetime) -> str:
    n = (agora - d).days
    return "hoje" if n <= 0 else "há 1 dia" if n == 1 else f"há {n} dias"


def _agenda(texto: str | None) -> str:
    """'Diário 22:00' -> ' para todos os dias às 22:00' (para encaixar na frase)."""
    if not texto or texto == "-":
        return ""
    m = re.fullmatch(r"(.*?)\s*(\d{2}:\d{2})", texto.strip())
    if not m:
        return f" ({texto})"
    dias, hora = m.groups()
    if dias.lower().startswith("di") and "rio" in dias.lower():
        return f" para todos os dias às {hora}"
    if dias.startswith("Dia "):
        return f" para todo dia {dias[4:]} do mês às {hora}"
    return f" para {dias} às {hora}"


# Mensagens frequentes do Veeam (inglês) -> português; o que não estiver aqui vai como veio
_FRASES = [
    (r"(?:Repository )?free space is below (\d+)\s*%", r"o espaço livre do armazenamento está abaixo de \1%"),
    (r"Not enough free space(?: on (?:the )?(?:backup )?repository)?", "não há espaço livre suficiente"),
    (r"Unable to connect to (?:the )?(?:backup )?repository", "não foi possível conectar ao armazenamento de backup"),
    (r"Failed to create (?:VM )?snapshot", "não foi possível criar o snapshot do servidor"),
    (r"Failed to connect to (?:the )?host", "não foi possível conectar ao servidor"),
    (r"(?:Operation )?timed out|timeout", "tempo limite excedido"),
    (r"Access is denied", "acesso negado"),
    (r"The network path was not found", "caminho de rede não encontrado"),
    (r"Error:\s*", ""),
]


def _traduzir(t: str) -> str:
    for de, para in _FRASES:
        t = re.sub(de, para, t, flags=re.I)
    frases = [f.strip(" .:-") for f in re.split(r"(?<=\.)\s+", t)]
    return " ".join(f[:1].upper() + f[1:] + "." for f in frases if f)


def _motivo_alarme(descricao: str) -> str:
    """Traduz os textos mais comuns do Veeam ONE; o restante vai como veio."""
    t = re.sub(r"\s+", " ", descricao or "").strip()
    m = re.search(r'Job "([^"]+)" finished with (\w+)', t, re.I)
    if m:
        estado = {"failed": "falha", "warning": "aviso"}.get(m.group(2).lower(), m.group(2))
        resto = _traduzir(t[m.end():])
        return f'A rotina de backup "{m.group(1)}" terminou com {estado}' + (f". {resto}" if resto else ".")
    return _traduzir(t) or "—"


def _erros(jobs: dict, maquinas: list, vms: list, politicas: dict, alarmes: list, agora: datetime) -> list[dict]:
    """Um item por problema: o que deu errado, onde e por quê. Lista vazia = nada a reportar."""
    erros = []
    alarmes_ativos = [a for a in alarmes
                      if (a["status"] or "").lower() != "resolved" and not _NORMALIZADO.search(a["descricao"])]

    def alarme_do_job(nome: str) -> dict | None:
        return next((a for a in alarmes_ativos if f'"{nome.upper()}"' in a["descricao"].upper()), None)

    # 1. Jobs cuja última execução falhou ou terminou com aviso
    jobs_com_erro = set()
    for j in jobs.values():
        st = (j["status"] or "").lower()
        if st not in ("failed", "warning"):
            continue
        jobs_com_erro.add(j["nome"])
        alm = alarme_do_job(j["nome"])
        erros.append({
            "onde": j["nome"], "data": j["ultima_execucao"],
            "problema": "Backup falhou" if st == "failed" else "Backup concluído com aviso",
            "motivo": _motivo_alarme(alm["descricao"]) if alm else
                      ("A última execução da rotina terminou com erro; parte ou todos os servidores não foram copiados."
                       if st == "failed" else "A última execução terminou, mas com avisos em algum servidor."),
            "nivel": "critico" if st == "failed" else "alerta",
        })

    # 2. Servidores do Veeam sem cópia recente
    for m in maquinas:
        nivel = _nivel_veeam(m, jobs.get(m["job"]), agora)
        if nivel == "ok":
            continue
        d, job = m["ultimo_backup"], jobs.get(m["job"])
        if not d:
            problema, motivo = "Nenhum backup concluído", f'O servidor está na rotina "{m["job"]}", mas não há cópia registrada.'
        elif nivel == "critico":
            problema = "Backup desatualizado"
            motivo = f'A última cópia concluída pela rotina "{m["job"]}" foi {_dias(d, agora)}.'
        else:
            problema = "Ficou de fora da última execução"
            motivo = (f'A rotina "{m["job"]}" rodou em {fmt_data(job["ultima_execucao"])}, '
                      "mas este servidor não foi copiado.")
        erros.append({"onde": m["nome"], "data": d, "problema": problema, "motivo": motivo, "nivel": nivel})

    # 3. Servidores na nuvem (SCP) com política, mas sem backup recente
    for vm in vms:
        if not vm["politicas"]:
            continue  # sem backup contratado: aparece só na tabela de servidores
        d = vm["ultimo_backup"]
        nivel = _nivel_scp(d, agora)
        if nivel == "ok":
            continue
        pol = vm["politicas"][0]
        quando = _agenda((politicas.get(pol) or {}).get("agendamento"))
        if not d:
            problema, motivo = "Nenhum backup concluído", f"O backup está programado{quando}, mas nenhuma cópia foi registrada."
        else:
            problema = "Backup desatualizado"
            motivo = (f"O backup está programado{quando}, mas a última cópia concluída foi "
                      f"{_dias(d, agora)}.")
        erros.append({"onde": vm["nome"], "data": d, "problema": problema, "motivo": motivo, "nivel": nivel})

    # 4. Alarmes ainda ativos que não se referem a um job já listado
    for a in alarmes_ativos:
        if any(f'"{n.upper()}"' in a["descricao"].upper() for n in jobs_com_erro):
            continue
        cita = re.search(r'(?:Job|VM|Computer) "([^"]+)"', a["descricao"])
        erros.append({
            "onde": cita.group(1) if cita else "Infraestrutura de backup", "data": a["data"],
            "problema": _ALARMES.get(a["nome"].lower(), a["nome"]),
            "motivo": _motivo_alarme(a["descricao"]),
            "nivel": "critico" if (a["status"] or "").lower() == "error" else "alerta",
        })

    antigo = datetime.min.replace(tzinfo=timezone.utc)
    erros.sort(key=lambda e: e["data"] or antigo, reverse=True)
    erros.sort(key=lambda e: e["nivel"] != "critico")
    for e in erros:
        e["data"] = fmt_data(e["data"]) if e["data"] else "—"
    return erros


# ------------------------------------------------------------------ montagem

def montar(dados: dict) -> dict:
    cli, v, s, agora = dados["cliente"], dados["veeam"], dados["scp"], dados["coletado_em"]
    inicio = agora - timedelta(days=config.DIAS_PERIODO)

    jobs = {j["nome"]: j for j in v["jobs"] if j["ativo"]}
    maquinas = [m for m in v["maquinas"] if m["job"] in jobs]
    vms = [vm for t in s["tenants"] for vm in t["vms"]]

    politicas = {p["nome"]: p for t in s["tenants"] for p in t["politicas"]}

    # Servidores: Veeam e nuvem (SCP) numa tabela só; o job é uma coluna
    # Retenção: SCP = configuração da política; Veeam = cópias guardadas (a API não expõe a configuração)
    servidores = []
    for m in maquinas:
        servidores.append({
            "nome": m["nome"], "job": m["job"], "ultimo": fmt_data(m["ultimo_backup"]),
            "nivel": _nivel_veeam(m, jobs.get(m["job"]), agora),
            "copias": m["restore_points"], "espaco": m["backup_bytes"],
            "retencao": (f"{m['restore_points']} cópia" + ("s" if m["restore_points"] != 1 else ""))
                        if m["restore_points"] else "—",
        })
    for vm in vms:
        sem = not vm["politicas"] and not vm["pontos"]
        servidores.append({
            "nome": vm["nome"], "job": ", ".join(vm["politicas"]) or "Sem backup configurado",
            "ultimo": "Sem backup" if sem else fmt_data(vm["ultimo_backup"]),
            "nivel": "critico" if sem else _nivel_scp(vm["ultimo_backup"], agora),
            "copias": vm["pontos"], "espaco": vm["backup_bytes"],
            "retencao": next((politicas[p]["retencao"] for p in vm["politicas"] if p in politicas), "—"),
        })
    for x in servidores:
        x["status"] = {"ok": "Em dia", "alerta": "Atrasado"}.get(
            x["nivel"], "Sem backup" if x["ultimo"] in ("—", "Sem backup") else "Desatualizado")
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

    # Contratado x consumo real: só Veeam (mesma medição do automacao_veeam). A nuvem Sangfor (SCP)
    # só é usada pela Uniggel e pela CRP (grupo IDX) e não entra no contrato de backup
    contrato = cli.get("contrato") or {}
    consumo = []
    for item, contratado, real, fmt in [
        ("Servidores em backup", contrato.get("servidores"), qtd_veeam, lambda n: f"{n:.0f}"),
        ("Volume de backup", (contrato.get("volume_gb") or 0) * 1024**3 or None, usado_veeam, fmt_bytes),
    ]:
        if not contratado:
            continue
        pct = 100 * real / contratado
        consumo.append({"item": item, "contratado": fmt(contratado), "real": fmt(real), "pct": fmt_pct(pct),
                        "nivel": "critico" if pct > 100 else "alerta" if pct >= 85 else "ok"})

    erros = _erros(jobs, maquinas, vms, politicas, v["alarmes"], agora)

    return {
        "cliente": cli["nome"],
        "razao_social": cli.get("razao_social") or cli["nome"],
        "periodo": f"{fmt_data(inicio, False)} a {fmt_data(agora, False)}",
        "painel": painel,
        "consumo": consumo,
        "erros": erros,
        "servidores": servidores,
        "armazenamento": armazenamento,
    }
