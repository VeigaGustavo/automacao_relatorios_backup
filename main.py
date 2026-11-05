"""Gera o relatório de backup (Veeam ONE + SCP) de um ou mais clientes.

    python main.py                 # todos os clientes de clientes.py
    python main.py crp uniggel     # só os informados
    python main.py crp --json      # também grava os dados coletados em JSON
    python main.py --listar        # lista os clientes cadastrados
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import unicodedata
from datetime import datetime, timezone

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


def principal(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Relatório de backup por cliente (Veeam ONE + SCP).")
    ap.add_argument("clientes", nargs="*", help=f"chaves de clientes.py (padrão: todos: {', '.join(CLIENTES)})")
    ap.add_argument("--saida", default=str(config.PASTA_SAIDA), help="pasta dos relatórios")
    ap.add_argument("--json", action="store_true", help="grava também os dados coletados (.json)")
    ap.add_argument("--pdf", action="store_true", help="gera também o PDF (exige Microsoft Word)")
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
