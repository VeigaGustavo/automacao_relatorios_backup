# automacao_relatorios_backup — Relatório de Backup por cliente

Gera um relatório Word (.docx, e opcionalmente PDF) por cliente, sobre o papel timbrado da IDX
(`template.docx`), com o estado dos backups em **duas fontes**:

| Fonte | O que entra no relatório |
|---|---|
| **Veeam ONE** (REST, somente leitura) | Jobs (status, última execução, duração, dados transferidos), máquinas protegidas (restore points, último backup, tamanho), repositórios (capacidade/uso/imutabilidade) e cota do Cloud Connect |
| **SCP** (Open-API, assinatura EC2) | Políticas de backup e retenção, VMs do tenant com pontos de backup, último backup e volume |

Clientes cadastrados no protótipo: **CRP** e **Uniggel Sementes** (`clientes.py`).

> Somente leitura nas duas APIs. Nada é alterado no Veeam nem no SCP.

## Como rodar

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env      # e preencha (mesmas credenciais do automacao_veeam e do scp-console)

.venv\Scripts\python.exe main.py                 # todos os clientes
.venv\Scripts\python.exe main.py crp             # só um
.venv\Scripts\python.exe main.py uniggel --json  # também grava os dados coletados
.venv\Scripts\python.exe main.py --pdf           # também gera o PDF (exige Microsoft Word)
.venv\Scripts\python.exe main.py --listar
```

Saída em `relatorios/relatorio-backup-<cliente>-<data>.docx` (e `.pdf` com `--pdf`). Termina com código 1 se a coleta de
algum cliente falhar.

## Estrutura do relatório

Linguagem simples, voltada ao cliente. Cabeçalho, rodapé e margens vêm do `template.docx` (timbrado IDX).

1. **Título e painel**: Repositório (em uso), VMs, Disco alocado e Total de backups (nuvem + Veeam).
2. **Contratado x Consumo** (só se `contrato` estiver preenchido em `clientes.py`).
3. **Armazenamento**: Used Space / Free Space.
4. **Servidores**: Servidor, Job e Último backup (verde em dia, amarelo atenção, vermelho sem backup).

## Regras

- **Job ativo**: executou nos últimos `RELATORIO_DIAS_PERIODO` dias (padrão 30). Os demais aparecem
  como "inativo" (ex.: jobs antigos substituídos pelos `_Novo` da CRP).
- **Máquina Veeam em dia**: recebeu ponto na última execução do job (funciona para jobs diários e
  semanais); vermelho se o último backup passou de `RELATORIO_DIAS_CRITICO` dias (padrão 7).
- **VM SCP em dia**: backup nas últimas `RELATORIO_HORAS_ALERTA` horas (padrão 36; as políticas
  atuais são diárias).
- **Volume Veeam**: soma lógica dos restore points das máquinas (com fast clone em ReFS/XFS pode
  passar do espaço físico). Repositórios compartilhados (CRP) mostram o uso total do disco.
- **Volume SCP**: soma do `size` dos pontos de backup (em KB), mesma regra do scp-console.
- **Volumetria contratada**: cada item de `contrato.volumes` em `clientes.py` diz onde é medido
  (`medicao`, obrigatório), conforme o contrato:
  - `"backend"` (destino): cota em uso da nuvem Sangfor (SCP) + espaço usado pelas cópias no
    repositório Veeam do cliente (compartilhado = só os backups dele) + cota usada do Cloud Connect.
    É o mesmo valor do cartão "Repositório (em uso)". Livre = contratado − esse total.
  - `"frontend"` (origem): `usedSourceSizeBytes` das máquinas protegidas, uma vez por máquina (a mesma
    máquina em dois jobs não soma duas vezes). `maquinas` restringe às máquinas cobertas pelo item
    (ex.: AKC ID 215, só FISIOLOGIA-01/02). Cloud Connect não tem Front-End visível.
  O relatório mostra a medição no nome da linha ("Volume de backup (Front-End)") e explica o termo.
- Sessões de job (`/sessions`) não estão disponíveis para o papel Read-Only do Veeam ONE.
- **Agendamento**: a API pública não expõe o agendamento dos jobs, então ele vem do Report Catalog
  (mesma API da prévia do Web Client, `webview/...`): "Protected VMs Job Schedule" (colunas
  Schedule / Time / Runs On) e, para o que ele não cobre (agentes), "Job Configuration Dump".
  Jobs de Proxmox não aparecem em nenhum dos dois e ficam com "—". Na nuvem (SCP), vem da política.

## Cadastro de clientes (`clientes.py`)

```python
"uniggel": {
    "nome": "Uniggel Sementes",
    "razao_social": "UNIGGEL SEMENTES, INDÚSTRIA E COMÉRCIO LTDA",
    "scp_tenants": ["TN - UNIGGEL SEMENTES"],      # nome exato do tenant no SCP
    "contrato": {"servidores": 30,                 # None = não contratado
                 "volumes": [{"gb": 5120, "medicao": "backend"}]},  # "frontend" = origem, "backend" = destino
    "veeam": {
        "servidores": ["10.10.100.15"],              # VBR dedicado: todos os jobs entram
        "jobs_regex": None,                          # para VBR compartilhado (CRP: r"\bCRP\b")
        "repositorios": ["REPO-LOCAL-UNIGGEL"],
        "cloud_connect": ["uniggel.sementes"],
    },
},
```

## Módulos

| Arquivo | Responsabilidade |
|---|---|
| `main.py` | CLI: coleta e gera o relatório de cada cliente |
| `clientes.py` | Cadastro de clientes e de como achar os dados deles |
| `veeam_one.py` | Cliente da API do Veeam ONE e coleta por cliente |
| `scp.py` | Cliente da Open-API do SCP (assinatura EC2), políticas, VMs e pontos de backup |
| `analise.py` | Regras do relatório (em dia, contratado x consumo, ocorrências traduzidas) |
| `relatorio_docx.py` | Monta o .docx sobre o `template.docx` e exporta PDF via Word |
| `config.py` | Leitura do `.env` e parâmetros do relatório |
