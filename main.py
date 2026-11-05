"""Gera o relatório de backup (Veeam ONE + SCP) de um ou mais clientes.

    python main.py                 # todos os clientes de clientes.py
    python main.py crp uniggel     # só os informados
    python main.py crp --json      # também grava os dados coletados em JSON
    python main.py --listar        # lista os clientes cadastrados
    python main.py crp --simular-erros   # exemplo com erros fictícios (arquivo "-SIMULACAO")
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

import analise
import config
import relatorio_docx
import scp
import veeam_one
from clientes import CLIENTES

log = logging.getLogger("relatorio")


def _slug(texto: str) -> str:
    t = unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-") or "cliente"


def coletar(chave: str) -> dict:
    cli = CLIENTES[chave]
    agora = datetime.now(timezone.utc)
    log.info("[%s] coletando Veeam ONE...", cli["nome"])
    v = veeam_one.coletar(cli)
    log.info("[%s] %d jobs, %d maquinas, %d alarmes", cli["nome"], len(v["jobs"]), len(v["maquinas"]), len(v["alarmes"]))
    log.info("[%s] coletando SCP...", cli["nome"])
    s = scp.coletar(cli)
    log.info("[%s] %d tenant(s), %d VMs", cli["nome"], len(s["tenants"]), sum(len(t["vms"]) for t in s["tenants"]))
    for aviso in v["avisos"] + s["avisos"]:
        log.warning("[%s] %s", cli["nome"], aviso)
    return {"chave": chave, "cliente": cli, "coletado_em": agora, "veeam": v, "scp": s}


def simular_erros(dados: dict) -> None:
    """Injeta erros fictícios nos dados coletados, só para ver como a tabela de erros fica."""
    v, agora = dados["veeam"], dados["coletado_em"]
    ativos = [j for j in v["jobs"] if j["ativo"]]
    if ativos:  # rotina que falhou, com o motivo vindo de um alarme
        j = ativos[0]
        j["status"] = "Failed"
        v["alarmes"].append({"nome": "Backup job state", "status": "Error", "data": j["ultima_execucao"],
                             "origem": j["servidor"], "tipo_origem": "VbrBackupServer", "repeticoes": 1,
                             "descricao": f'Job "{j["nome"]}" finished with Failed. Error: Unable to connect '
                                          "to the backup repository. Not enough free space."})
    if len(ativos) > 1:  # rotina concluída com aviso
        ativos[1]["status"] = "Warning"
    maquinas = [m for m in v["maquinas"] if m["job"] in {j["nome"] for j in ativos}]
    if maquinas:  # servidor sem cópia há 12 dias
        maquinas[-1]["ultimo_backup"] = agora - timedelta(days=12)
    if len(maquinas) > 1:  # servidor que ficou de fora da última execução
        job = next(j for j in ativos if j["nome"] == maquinas[len(maquinas) // 2]["job"])
        maquinas[len(maquinas) // 2]["ultimo_backup"] = job["ultima_execucao"] - timedelta(days=1)
    v["alarmes"].append({"nome": "Backup repository free space", "status": "Warning",
                         "data": agora - timedelta(hours=5), "origem": "repositorio", "repeticoes": 1,
                         "tipo_origem": "VbrRepository",
                         "descricao": "Repository free space is below 15%."})
    for t in dados["scp"]["tenants"]:  # servidor na nuvem com backup atrasado
        vm = next((x for x in t["vms"] if x["politicas"]), None)
        if vm:
            vm["ultimo_backup"] = agora - timedelta(days=3)
            break


def principal(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Relatório de backup por cliente (Veeam ONE + SCP).")
    ap.add_argument("clientes", nargs="*", help=f"chaves de clientes.py (padrão: todos: {', '.join(CLIENTES)})")
    ap.add_argument("--saida", default=str(config.PASTA_SAIDA), help="pasta dos relatórios")
    ap.add_argument("--json", action="store_true", help="grava também os dados coletados (.json)")
    ap.add_argument("--pdf", action="store_true", help="gera também o PDF (exige Microsoft Word)")
    ap.add_argument("--simular-erros", action="store_true",
                    help="gera um exemplo com erros fictícios (arquivo com sufixo -SIMULACAO)")
    ap.add_argument("--listar", action="store_true", help="lista os clientes cadastrados e sai")
    args = ap.parse_args(argv)

    if args.listar:
        for k, c in CLIENTES.items():
            print(f"{k:12s} {c['nome']}  (SCP: {', '.join(c['scp_tenants']) or '-'})")
        return 0

    chaves = args.clientes or list(CLIENTES)
    desconhecidas = [c for c in chaves if c not in CLIENTES]
    if desconhecidas:
        ap.error(f"cliente(s) desconhecido(s): {', '.join(desconhecidas)}. Use --listar.")

    falhas = 0
    for chave in chaves:
        try:
            dados = coletar(chave)
        except (veeam_one.ErroVeeam, scp.ErroSCP) as exc:
            log.error("[%s] falha na coleta: %s", chave, exc)
            falhas += 1
            continue
        base = f"relatorio-backup-{_slug(dados['cliente']['nome'])}-{dados['coletado_em'].astimezone(analise.BRT):%Y-%m-%d}"
        if args.simular_erros:
            simular_erros(dados)
            base += "-SIMULACAO"
        if args.json:
            caminho = config.Path(args.saida) / f"{base}.json"
            caminho.parent.mkdir(parents=True, exist_ok=True)
            caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            log.info("[%s] dados: %s", dados["cliente"]["nome"], caminho)
        for arquivo in relatorio_docx.gerar(dados, config.Path(args.saida) / f"{base}.docx", pdf=args.pdf):
            log.info("[%s] relatorio: %s", dados["cliente"]["nome"], arquivo)
    return 1 if falhas else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    sys.exit(principal())
