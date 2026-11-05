"""Relatório de backup em Word, sobre o papel timbrado da IDX (template.docx).

O template traz cabeçalho (faixa e logo IDX), rodapé (site e 0800) e margens; o corpo é
preenchido aqui. Paleta do modelo: azul IDX #00A2FF, preto e cinzas; fonte Calibri.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

import analise
import config

TEMPLATE = config.RAIZ / "template.docx"

AZUL = "00A2FF"
ESCURO = "231F20"
CINZA = "5E5E5E"
CINZA_CLARO = "D5D5D5"
FUNDO = "F3F8FC"
NIVEL = {"ok": "1E8E3E", "alerta": "C77700", "critico": "C62828", "neutro": CINZA,
         "azul": AZUL, "escuro": ESCURO}
LARGURA = 9070  # dxa: A4 (11906) − margens de 1418 do template


# ------------------------------------------------------------------ utilitários de XML

def _sombrear(cell, cor: str) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), cor)
    tcPr.append(shd)


def _bordas(cell, **lados: tuple[int, str]) -> None:
    """lados: top/bottom/left/right = (espessura em oitavos de ponto, cor) ou None para nenhuma."""
    tcPr = cell._tc.get_or_add_tcPr()
    tcb = OxmlElement("w:tcBorders")
    for lado in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{lado}")
        valor = lados.get(lado)
        if valor:
            el.set(qn("w:val"), "single")
            el.set(qn("w:sz"), str(valor[0]))
            el.set(qn("w:color"), valor[1])
        else:
            el.set(qn("w:val"), "nil")
        tcb.append(el)
    tcPr.append(tcb)


def _largura(cell, dxa: int) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    tcW = OxmlElement("w:tcW")
    tcW.set(qn("w:w"), str(dxa))
    tcW.set(qn("w:type"), "dxa")
    tcPr.append(tcW)


def _margens_celula(tabela, topo=60, lado=100) -> None:
    tblPr = tabela._tbl.tblPr
    mar = OxmlElement("w:tblCellMar")
    for nome, v in (("top", topo), ("bottom", topo), ("left", lado), ("right", lado)):
        el = OxmlElement(f"w:{nome}")
        el.set(qn("w:w"), str(v))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tblPr.append(mar)


def _fixar_layout(tabela, larguras: list[int]) -> None:
    tblPr = tabela._tbl.tblPr
    lay = OxmlElement("w:tblLayout")
    lay.set(qn("w:type"), "fixed")
    tblPr.append(lay)
    w = OxmlElement("w:tblW")
    w.set(qn("w:w"), str(sum(larguras)))
    w.set(qn("w:type"), "dxa")
    tblPr.append(w)
    grid = tabela._tbl.tblGrid
    for gc, lw in zip(grid.findall(qn("w:gridCol")), larguras):
        gc.set(qn("w:w"), str(lw))


def _repetir_cabecalho(linha) -> None:
    trPr = linha._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    trPr.append(el)


def _nao_quebrar(linha) -> None:
    trPr = linha._tr.get_or_add_trPr()
    trPr.append(OxmlElement("w:cantSplit"))


# ------------------------------------------------------------------ texto

def _texto(par, texto: str, tam: float = 10, cor: str = ESCURO, negrito: bool = False):
    run = par.add_run(texto)
    run.font.name = "Calibri"
    run.font.size = Pt(tam)
    run.font.bold = negrito
    run.font.color.rgb = RGBColor.from_string(cor)
    return run


def _paragrafo(doc_ou_celula, texto: str = "", tam: float = 10, cor: str = ESCURO, negrito=False,
               antes: float = 0, depois: float = 0, alinhar=None):
    par = doc_ou_celula.add_paragraph()
    fmt = par.paragraph_format
    fmt.space_before, fmt.space_after, fmt.line_spacing = Pt(antes), Pt(depois), 1.0
    if alinhar:
        par.alignment = alinhar
    if texto:
        _texto(par, texto, tam, cor, negrito)
    return par


def _titulo_secao(doc, titulo: str, explicacao: str = "") -> None:
    par = _paragrafo(doc, antes=16, depois=2)
    par.paragraph_format.keep_with_next = True
    _texto(par, titulo, 13, ESCURO, True)
    if explicacao:
        p2 = _paragrafo(doc, explicacao, 9, CINZA, depois=6)
        p2.paragraph_format.keep_with_next = True


# ------------------------------------------------------------------ tabelas

def _tabela(doc, cabecalho: list[str], linhas: list[list], larguras: list[int],
            direita: set[int] = frozenset(), total: list | None = None) -> None:
    """linhas: valores str ou (texto, nivel) para colorir a célula."""
    tab = doc.add_table(rows=1, cols=len(cabecalho))
    tab.alignment = WD_TABLE_ALIGNMENT.CENTER
    _fixar_layout(tab, larguras)
    _margens_celula(tab)
    borda = (4, CINZA_CLARO)

    def preencher(cell, valor, i, negrito=False, cor=ESCURO, fundo=None, tam=9, bordas=None):
        _largura(cell, larguras[i])
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        if fundo:
            _sombrear(cell, fundo)
        _bordas(cell, **(bordas or {"bottom": borda}))
        texto, nivel = (valor if isinstance(valor, tuple) else (valor, None))
        par = cell.paragraphs[0]
        par.paragraph_format.space_before = par.paragraph_format.space_after = Pt(0)
        if i in direita:
            par.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        _texto(par, str(texto), tam, NIVEL[nivel] if nivel else cor, negrito or bool(nivel))

    cab = tab.rows[0]
    _repetir_cabecalho(cab)
    for i, h in enumerate(cabecalho):
        preencher(cab.cells[i], h, i, negrito=True, cor="FFFFFF", fundo=AZUL)
    for n, linha in enumerate(linhas):
        row = tab.add_row()
        _nao_quebrar(row)
        for i, valor in enumerate(linha):
            preencher(row.cells[i], valor, i, negrito=(i == 0), fundo=FUNDO if n % 2 else None)
    if total:
        row = tab.add_row()
        _nao_quebrar(row)
        for i, valor in enumerate(total):
            preencher(row.cells[i], valor, i, negrito=True, cor="FFFFFF", fundo=ESCURO, tam=10.5,
                      bordas={"top": (18, AZUL)})
            row.height = Pt(22)


def _painel(doc, cartoes: list[tuple[str, str, str, list[str]]]) -> None:
    """Cartões do topo (mesmos do scp-console): fundo claro e filete azul em cima."""
    n = len(cartoes)
    espaco = 120
    largura = (LARGURA - espaco * (n - 1)) // n
    larguras = []
    for i in range(n):
        larguras.append(largura)
        if i < n - 1:
            larguras.append(espaco)
    tab = doc.add_table(rows=1, cols=len(larguras))
    _fixar_layout(tab, larguras)
    _margens_celula(tab, topo=90, lado=140)
    for i, cell in enumerate(tab.rows[0].cells):
        _largura(cell, larguras[i])
        if i % 2:  # coluna de espaçamento
            _bordas(cell)
            continue
        rotulo, valor, cor, detalhe = cartoes[i // 2]
        _sombrear(cell, FUNDO)
        _bordas(cell, top=(18, AZUL))
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(2)
        _texto(p, rotulo.upper(), 7.5, CINZA, True)
        _paragrafo(cell, valor, 17, NIVEL[cor], True)


# ------------------------------------------------------------------ documento

def gerar(dados: dict, caminho: Path, pdf: bool = False) -> list[Path]:
    r = analise.montar(dados)
    doc = Document(str(TEMPLATE))
    corpo = doc.element.body
    # remove o parágrafo vazio do template (mantém o sectPr com cabeçalho/rodapé)
    for p in list(corpo.iterchildren(qn("w:p"))):
        corpo.remove(p)

    estilo = doc.styles["Normal"]
    estilo.font.name = "Calibri"
    estilo.font.size = Pt(10)

    # Título
    titulo = _paragrafo(doc, depois=2)
    _texto(titulo, "Relatório de Backup", 20, ESCURO, True)
    _texto(titulo, "  |  ", 20, CINZA_CLARO)
    _texto(titulo, r["cliente"], 20, ESCURO)
    _paragrafo(doc, f"Período: {r['periodo']}", 9.5, CINZA, depois=12)

    _painel(doc, r["painel"])

    if r["consumo"]:
        _titulo_secao(doc, "Contratado x Consumo")
        _tabela(doc, ["Item", "Contratado", "Consumo", "Uso"],
                [[c["item"], c["contratado"], c["real"], (c["pct"], c["nivel"])] for c in r["consumo"]],
                [3370, 1900, 1900, 1900], {1, 2, 3})

    if r["erros"]:
        _titulo_secao(doc, "Erros encontrados",
                      "Problemas identificados nos backups do período e o motivo de cada um.")
        _tabela(doc, ["Servidor / Rotina", "O que deu errado", "Por quê", "Quando"],
                [[e["onde"], (e["problema"], e["nivel"]), e["motivo"], e["data"]] for e in r["erros"]],
                [2000, 1950, 3420, 1700], {3})

    if r["armazenamento"]:
        _titulo_secao(doc, "Armazenamento")
        _tabela(doc, ["Armazenamento", "Espaço usado", "Espaço livre"],
                [[a["nome"], analise.fmt_bytes(a["usado"]),
                  analise.fmt_bytes(a["livre"]) if a["livre"] is not None else "—"] for a in r["armazenamento"]],
                [4870, 2100, 2100], {1, 2})

    if r["servidores"]:
        total = sum(x["espaco"] or 0 for x in r["servidores"])

        def pct(n) -> str:
            return f"{100 * n / total:.1f}%".replace(".", ",") if total and n else "—"

        _titulo_secao(doc, "Servidores",
                      "Status: verde = em dia; amarelo = atrasado; vermelho = desatualizado ou sem backup. "
                      "Retenção = por quanto tempo as cópias são guardadas (nuvem) ou quantas cópias estão "
                      "guardadas hoje (Veeam). Consumo = espaço ocupado pelas cópias de cada servidor; "
                      "% = participação no total de backups.")
        _tabela(doc, ["Servidor", "Rotina de backup", "Último backup", "Status", "Retenção", "Consumo", "%"],
                [[x["nome"], "—" if x["status"] == "Sem backup" and not x["copias"] else x["job"],
                  x["ultimo"] if x["ultimo"] != "Sem backup" else "—",
                  (x["status"], x["nivel"]), x["retencao"],
                  analise.fmt_bytes(x["espaco"]) if x["espaco"] else "—", pct(x["espaco"])]
                 for x in r["servidores"]],
                [1600, 1450, 1650, 1400, 1100, 1150, 720], {2, 4, 5, 6},
                total=["Total", "", "", "", "", analise.fmt_bytes(total), "100%" if total else "—"])

    doc.core_properties.title = f"Relatório de Backup - {r['razao_social']}"
    doc.core_properties.author = "IDX Data Centers"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(caminho))
    saidas = [caminho]
    if pdf:
        saidas.append(exportar_pdf(caminho))
    return saidas


def exportar_pdf(docx: Path) -> Path:
    """Converte com o Microsoft Word (Windows). Sem Word, use o .docx."""
    destino = docx.with_suffix(".pdf")
    script = (
        "$w = New-Object -ComObject Word.Application; $w.Visible = $false; "
        f"$d = $w.Documents.Open('{docx.resolve()}', $false, $true); "
        f"$d.SaveAs([ref]'{destino.resolve()}', [ref]17); $d.Close($false); $w.Quit()"
    )
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], check=True, capture_output=True)
    return destino
