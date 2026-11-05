"""Cadastro dos clientes do relatório: o que pertence a cada um no Veeam ONE e no SCP.

Cada cliente define como achar os dados dele:

  scp_tenants            nomes exatos dos tenants (projects) no SCP
  veeam.servidores       IPs/nomes de VBRs dedicados ao cliente (tudo que estiver neles entra)
  veeam.jobs_regex       regex aplicada ao NOME do job (para VBRs compartilhados)
  veeam.repositorios     repositórios do cliente (backups gravados neles entram; o uso do
                         disco aparece na seção de armazenamento)
  veeam.cloud_connect    tenants do Veeam Cloud Connect (cota usada / contratada)

  contrato               quantidade contratada: servidores e volume em GB binário (1 TB = 1024 GB),
                         mesmos valores do CONTRATOS_BACKUP do automacao_veeam. None = não informado

Um job entra se estiver num servidor dedicado, se o nome casar com jobs_regex ou se algum
backup dele for gravado num repositório do cliente. Uma máquina entra se o backup dela
pertencer a um job do cliente ou estiver num repositório do cliente.
"""

CLIENTES = {
    "crp": {
        "nome": "CRP",
        "razao_social": "CRP",
        "scp_tenants": ["TN - CRP"],
        # CRP não é cliente (faz parte do grupo IDX): sem contrato, o relatório sai sem
        # a tabela Contratado x Consumo
        "contrato": {"servidores": None, "volume_gb": None},
        "veeam": {
            "servidores": [],
            # VBR 10.10.100.150 é compartilhado: só os jobs com CRP no nome
            "jobs_regex": r"\bCRP\b",
            "repositorios": [],
            "cloud_connect": [],
        },
    },
    "uniggel": {
        "nome": "Uniggel Sementes",
        "razao_social": "UNIGGEL SEMENTES, INDÚSTRIA E COMÉRCIO LTDA",
        "scp_tenants": ["TN - UNIGGEL SEMENTES"],
        # automacao_veeam CONTRATOS_BACKUP: ID 174 (30 servidores) e ID 237 (5 TB = 5120 GB)
        "contrato": {"servidores": 30, "volume_gb": 5120},
        "veeam": {
            # VBR local da Uniggel: todos os jobs dele são do cliente
            "servidores": ["10.10.100.15"],
            "jobs_regex": None,
            "repositorios": ["REPO-LOCAL-UNIGGEL"],
            "cloud_connect": ["uniggel.sementes"],
        },
    },
}
