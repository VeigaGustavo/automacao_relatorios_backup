"""Evidências do relatório: chamadas às APIs, valores brutos usados e cálculos.

Gera .txt e (com --pdf) .docx/.pdf no timbrado IDX:
    python main.py uniggel --pdf --evidencias
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

# Chamadas registradas por veeam_one._req e scp._get durante a coleta do cliente atual
CHAMADAS: list[dict] = []


def registrar(api: str, metodo: str, caminho: str, inicio: float, resposta, status: int = 200) -> None:
    CHAMADAS.append({"api": api, "metodo": metodo, "caminho": caminho.split("?")[0], "status": status,
                     "ms": (time.perf_counter() - inicio) * 1000, "itens": _contar(resposta)})


def _contar(r) -> int | None:
    if isinstance(r, list):
        return len(r)
    if isinstance(r, dict):
        for chave in ("items", "data"):
            v = r.get(chave)
            if isinstance(v, list):
                return len(v)
            if isinstance(v, dict):
                for k in ("data", "list", "items", "projects"):
                    if isinstance(v.get(k), list):
                        return len(v[k])
    return None


def _generico(caminho: str) -> str:
    """/servers/0b1c.../backups -> /servers/{id}/backups (agrupa chamadas iguais)."""
    caminho = re.sub(r"/[0-9a-f]{8}-[0-9a-f-]{27,}", "/{id}", caminho, flags=re.I)
    return re.sub(r"(?<!/janus)/\d+(?=/|$)", "/{id}", caminho)  # mantém a versão /janus/20180725


def _iso(d: datetime | None) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else "null"


# ------------------------------------------------------------------ conteúdo
# Blocos: ("titulo", texto) | ("texto", texto) | ("tabela", cabecalho, linhas, colunas à direita, larguras)

def _conteudo(dados: dict, r: dict, chamadas: list[dict]) -> tuple[str, str, list[tuple]]:
    import analise, config  # noqa: E401 (evita import circular na carga)

    fb, cli, agora = analise.fmt_bytes, dados["cliente"], dados["coletado_em"]
    calc = r["calculo"]
    B: list[tuple] = []
    titulo = f"Evidências | {cli['nome']}"
    sub = f"Coleta: {analise.fmt_data(agora)} (horário de Brasília)  ·  Período: {r['periodo']}"

    fontes = f"Veeam ONE {config.VEEAM_ONE_URL} (API REST {config.VEEAM_ONE_API_VERSAO})"
    if cli.get("scp_tenants"):
        fontes += f"  ·  Sangfor SCP {config.SCP_BASE_URL}"
    v = cli["veeam"]
    filtro = "; ".join(f"{k}: {', '.join(v[k]) if isinstance(v[k], list) else v[k]}"
                       for k in ("servidores", "repositorios", "cloud_connect", "jobs_regex") if v.get(k))
    if cli.get("scp_tenants"):
        filtro += f"; tenants SCP: {', '.join(cli['scp_tenants'])}"
    B += [("texto", f"Fontes: {fontes}"), ("texto", f"Filtro do cliente (clientes.py): {filtro}")]

    # 1. chamadas
    B.append(("titulo", "1. Chamadas à API"))
    grupos: "OrderedDict[tuple, dict]" = OrderedDict()
    for c in chamadas:
        g = grupos.setdefault((c["api"], c["metodo"], _generico(c["caminho"])), {"n": 0, "itens": 0, "ms": 0.0})
        g["n"] += 1
        g["itens"] += c["itens"] or 0
        g["ms"] += c["ms"]
    B.append(("tabela", ["API", "Método", "Endpoint", "Chamadas", "Itens", "Tempo"],
              [[a, m, p, g["n"], g["itens"], f"{g['ms'] / 1000:.1f}s".replace(".", ",")]
               for (a, m, p), g in grupos.items()], {3, 4, 5}, [1150, 750, 4670, 900, 800, 800]))
    falhas = [c for c in chamadas if c.get("status", 200) != 200]
    B.append(("texto", ("Todas as chamadas responderam HTTP 200. " if not falhas else
                        f"{len(chamadas) - len(falhas)} de {len(chamadas)} chamadas responderam HTTP 200. ")
              + "webview/* = relatórios do Report Catalog (Protected VMs Job Schedule e Job Configuration "
                "Dump), usados para o agendamento."))
    for c in falhas:
        B.append(("texto", f"• Falha: {c['api']} {c['metodo']} {c['caminho']} -> HTTP {c['status']}"))

    # 2. valores brutos por servidor
    jobs = {j["nome"]: j for j in dados["veeam"]["jobs"]}
    serv = r["servidores"]
    veeam = [x for x in serv if x["origem"] == "Veeam"]
    sang = [x for x in serv if x["origem"] == "Sangfor"]
    if veeam:
        B.append(("titulo", "2a. Veeam: valores retornados pela API"))
        B.append(("texto", "protectedData/*/backups: lastProtectedDate → Último backup; restorePoints → Retenção; "
                           "totalRestorePointSizeBytes → Cloud. vbrJobs/*: lastRun e status do job."))
        linhas = []
        for x in veeam:
            m, j = x["bruto"], jobs.get(x["job"], {})
            linhas.append([x["nome"], x["job"], _iso(m["ultimo_backup"]), m["restore_points"],
                           f"{m['backup_bytes']:.0f}", _iso(j.get("ultima_execucao")), j.get("status", "-")])
        B.append(("tabela", ["Servidor", "Job", "lastProtectedDate", "restore Points", "totalRestorePoint SizeBytes",
                             "job.lastRun", "job.status"], linhas, {3, 4},
                  [1400, 1350, 1700, 700, 1300, 1700, 920]))
        B.append(("texto", "Agendamento (Report Catalog): valores crus → texto no relatório"))
        ag = OrderedDict()
        for x in veeam:
            j = jobs.get(x["job"], {})
            ag[x["job"]] = [x["job"], j.get("agendamento_fonte") or "não encontrado nos relatórios", x["agendamento"]]
        B.append(("tabela", ["Job", "Fonte / valores crus", "Relatório"], list(ag.values()), set(),
                  [2000, 5170, 1900]))
    if sang:
        B.append(("titulo", "2b. Sangfor: valores retornados pela API"))
        B.append(("texto", "janus servers, servers/{id}/backups e backup-policies: trigger_cron → Agendamento; "
                           "time do ponto mais recente → Último backup; tamanho dos pontos → Cloud."))
        pol = {p["nome"]: p for t in dados["scp"]["tenants"] for p in t["politicas"]}
        linhas = []
        for x in sang:
            vm = x["bruto"]
            crons = ", ".join(dict.fromkeys(str(pol[p].get("cron")) for p in vm["politicas"] if p in pol)) or "-"
            linhas.append([x["nome"], ", ".join(vm["politicas"]) or "-", crons, vm["pontos"],
                           _iso(vm["ultimo_backup"]), f"{vm['backup_bytes']:.0f}"])
        B.append(("tabela", ["Servidor", "Política", "trigger_cron", "Pontos", "Último ponto (time)",
                             "Bytes de backup"], linhas, {3, 5}, [2300, 1300, 1250, 800, 1820, 1600]))

    # 3. cálculos
    B.append(("titulo", "3. Cálculos"))
    B.append(("texto", f"Status: Em dia se o último backup tem até {config.DIAS_CRITICO_BACKUP} dias e "
                       "(Veeam) o servidor não ficou de fora da última execução do job (tolerância de 2h) / "
                       f"(Sangfor) o último ponto tem até {config.HORAS_ALERTA_BACKUP}h. Senão: Sem backup."))
    for x in serv:
        if x["nivel"] == "ok":
            continue
        d = x["bruto"]["ultimo_backup"]
        if d:
            h = (agora - d).total_seconds() / 3600
            idade = f"último backup há {h:.0f}h ({h / 24:.1f} dias)".replace(".", ",")
        else:
            idade = "nenhum ponto de backup"
        B.append(("texto", f"• Sem backup: {x['nome']} ({x['origem']}, {x['job']}): {idade}"))
    rot = analise.MEDICAO[r["medicao"]][0]
    if r["medicao"] == "frontend":
        B.append(("texto", f"Coluna {rot} (contrato medido na origem): Veeam = usedSourceSizeBytes da VM/agente; "
                           "Sangfor = storage_status.used_mb da VM. Total = cada servidor uma vez (linha com backup "
                           f"mais recente), só os com backup nos últimos {config.DIAS_PERIODO} dias."))
    else:
        B.append(("texto", f"Coluna {rot} (contrato medido no destino): Veeam = totalRestorePointSizeBytes; "
                           "Sangfor = tamanho dos pontos de backup. Total = soma das linhas."))
    for g in r["grupos"]:
        B.append(("texto", f"• {rot} {g['origem']}: {len(g['servidores'])} linhas = {g['volume']:.0f} bytes = "
                           f"{fb(g['volume'])}"))
    if len(r["grupos"]) > 1:
        tot = sum(g["volume"] for g in r["grupos"])
        B.append(("texto", f"• Total do resumo: " + " + ".join(fb(g["volume"]) for g in r["grupos"]) + f" = {fb(tot)}"))
    B.append(("texto", "Cartões do topo:"))
    B.append(("texto", f"• Repositório (em uso) = cota em uso SCP {fb(calc['quota'])} + armazenamento Veeam "
                       f"{fb(calc['usado_veeam'])} = {fb(calc['quota'] + calc['usado_veeam'])}"))
    B.append(("texto", f"• VMs = {calc['qtd_scp']} na nuvem + {calc['qtd_veeam']} Veeam (máquinas distintas + "
                       f"Cloud Connect {calc['qtd_cc']}) = {calc['qtd_scp'] + calc['qtd_veeam']}"))
    if calc["qtd_scp"]:
        B.append(("texto", f"• Disco alocado = soma de disks[].size_mb das VMs da nuvem = {fb(calc['disco'])}"))
    B.append(("texto", f"• Total de backups = SCP {fb(calc['vol_scp'])} + Veeam {fb(calc['vol_veeam'])} "
                       f"= {fb(calc['vol_scp'] + calc['vol_veeam'])}"))
    if calc["vol_veeam"] > calc["usado_veeam"] > 0:
        B.append(("texto", f"Obs.: no Veeam, Total de backups ({fb(calc['vol_veeam'])}) é maior que o espaço usado "
                           f"no armazenamento ({fb(calc['usado_veeam'])}) porque totalRestorePointSizeBytes é o "
                           "tamanho lógico das cópias; com fast clone (ReFS/XFS) blocos repetidos não ocupam "
                           "disco de novo."))
    if calc["armazenamento"]:
        B.append(("texto", "Armazenamento Veeam (vbr/repositories; repositório compartilhado = só backups do "
                           "cliente):"))
        for a in calc["armazenamento"]:
            B.append(("texto", f"• {a['nome']}: utilizado {fb(a['usado'])}"))
    for med in calc["medicoes"]:
        if med["medicao"] == "backend":
            B.append(("texto", f"Volume Cloud (destino) = cota em uso SCP {fb(calc['quota'])} + armazenamento "
                               f"Veeam {fb(calc['usado_veeam'])} = Repositório (em uso) = {fb(med['bytes'])}"))
            continue
        B.append(("texto", "Volume On-premise (origem) = usedSourceSizeBytes de cada máquina, uma vez por máquina "
                           "(valor da linha com backup mais recente), só as com backup nos últimos "
                           f"{config.DIAS_PERIODO} dias = {fb(med['bytes'])}"))
        for nome, n in sorted(med["maquinas"].items()):
            B.append(("texto", f"• {nome}: {n:.0f} bytes = {fb(n)}"))
        if med["faltando"]:
            B.append(("texto", "• Máquinas do contrato sem backup ativo encontrado: " + ", ".join(med["faltando"])))
    if r["consumo"]:
        B.append(("texto", f"Servidores em backup (contrato) = {calc['qtd_veeam']} Veeam + "
                           f"{calc['qtd_scp_protegidas']} VMs da nuvem com política ou pontos de backup = "
                           f"{calc['qtd_veeam'] + calc['qtd_scp_protegidas']}"))
        B.append(("texto", "Contratado x Consumo: " + "  |  ".join(
            f"{c['item']}" + (f" — {analise.MEDICAO[c['medicao']][0]}" if c["medicao"] else "") + f": {c['real']} ÷ {c['contratado']} = {c['pct']}, livre {c['livre'][0]}" for c in r["consumo"])))
    avisos = dados["veeam"]["avisos"] + dados["scp"]["avisos"]
    if avisos:
        B.append(("texto", "Avisos da coleta: " + " | ".join(avisos)))
    return titulo, sub, B


# ------------------------------------------------------------------ saídas

def _txt(titulo: str, sub: str, blocos: list[tuple]) -> str:
    L = [titulo.upper(), sub, ""]
    for b in blocos:
        if b[0] == "titulo":
            L += ["", b[1].upper()]
        elif b[0] == "texto":
            L.append(b[1])
        else:
            _, cab, linhas, direita, _larg = b
            linhas = [[str(c) for c in l] for l in linhas]
            larg = [max([len(h)] + [len(l[i]) for l in linhas]) for i, h in enumerate(cab)]

            def fmt(l):
                return "  ".join(c.rjust(larg[i]) if i in direita else c.ljust(larg[i])
                                 for i, c in enumerate(l)).rstrip()
            L += [fmt(cab), "  ".join("-" * n for n in larg)] + [fmt(l) for l in linhas]
    return "\n".join(L) + "\n"


def _docx(titulo: str, sub: str, blocos: list[tuple], caminho: Path) -> Path:
    import relatorio_docx as rd
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Pt

    doc = Document(str(rd.TEMPLATE))
    for p in list(doc.element.body.iterchildren(qn("w:p"))):
        doc.element.body.remove(p)
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(9)
    par = rd._paragrafo(doc, depois=2)
    rd._texto(par, "Evidências do Relatório de Backup", 18, rd.ESCURO, True)
    rd._texto(par, "  |  ", 18, rd.CINZA_CLARO)
    rd._texto(par, titulo.split("|", 1)[1].strip(), 18, rd.ESCURO)
    rd._paragrafo(doc, sub, 9.5, rd.CINZA, depois=8)
    for b in blocos:
        if b[0] == "titulo":
            rd._titulo_secao(doc, b[1])
        elif b[0] == "texto":
            rd._paragrafo(doc, b[1], 8.5, rd.CINZA if not b[1].startswith("•") else rd.ESCURO, depois=3)
        else:
            _, cab, linhas, direita, larg = b
            rd._tabela(doc, cab, [[str(c) for c in l] for l in linhas], larg, direita, tam=7.5)
            rd._paragrafo(doc, "", depois=4)
    doc.core_properties.title = titulo
    doc.core_properties.author = "IDX Data Centers"
    doc.save(str(caminho))
    return caminho


def gerar(dados: dict, r: dict, chamadas: list[dict], base: Path, pdf: bool = False) -> list[Path]:
    """base sem extensão; grava base.txt e, com pdf=True, base.pdf (o .docx intermediário é apagado)."""
    titulo, sub, blocos = _conteudo(dados, r, chamadas)
    base.parent.mkdir(parents=True, exist_ok=True)
    txt = base.with_suffix(".txt")
    txt.write_text(_txt(titulo, sub, blocos), encoding="utf-8-sig")
    saidas = [txt]
    if pdf:
        import relatorio_docx
        docx = _docx(titulo, sub, blocos, base.with_suffix(".docx"))
        saidas.append(relatorio_docx.exportar_pdf(docx))
        docx.unlink(missing_ok=True)
    return saidas
