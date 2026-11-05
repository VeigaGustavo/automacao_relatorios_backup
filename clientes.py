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
    # Clientes só Veeam (sem nuvem Sangfor). Contrato = IDs do CONTRATOS_BACKUP do automacao_veeam;
    # linhas "Front-End" ficam de fora (não são medidas pelo volume do repositório)
    "mix": {
        "nome": "Mix Alimentos",
        "razao_social": "MIX ALIMENTOS LTDA",
        "scp_tenants": [],
        # ID 102 (5 servidores); ID 226 é front-end
        "contrato": {"servidores": 5, "volume_gb": None},
        "veeam": {"servidores": [], "jobs_regex": None,
                  "repositorios": ["IDX-REPO-MIX-ALIMENTOS", "IDX-REPO-MIX-ALIMENTOS-IMUTAVEL"],
                  "cloud_connect": []},
    },
    "akc": {
        "nome": "AKC Diagnósticos",
        "razao_social": "ARAI, KAMINISHI, COSTA & CIA LTDA",
        "scp_tenants": [],
        # ID 12 (2 servidores) e ID 217 (75 TB); ID 215 é front-end
        "contrato": {"servidores": 2, "volume_gb": 76800},
        "veeam": {"servidores": [], "jobs_regex": None,
                  "repositorios": ["REPO-AKC01", "REPO-AKC02"], "cloud_connect": []},
    },
    "fomento": {
        "nome": "Agência de Fomento",
        "razao_social": "AGÊNCIA DE FOMENTO DO ESTADO DO TOCANTINS S/A",
        "scp_tenants": [],
        # ID 66 (15 servidores) e ID 346 (8 TB)
        "contrato": {"servidores": 15, "volume_gb": 8192},
        "veeam": {"servidores": [], "jobs_regex": None,
                  "repositorios": ["IDX-REPO-FOMENTO"], "cloud_connect": []},
    },
    "sesi": {
        "nome": "SESI-SENAI-FIETO",
        "razao_social": "SESI-SENAI-FIETO - DR/TO",
        "scp_tenants": [],
        # ID 286 (29 servidores) e ID 287 (7 TB)
        "contrato": {"servidores": 29, "volume_gb": 7168},
        "veeam": {"servidores": [], "jobs_regex": None,
                  "repositorios": ["IDX-REPO-SESI01"], "cloud_connect": []},
    },
    "sebrae": {
        "nome": "SEBRAE/TO",
        "razao_social": "SERVIÇO DE APOIO ÀS MICRO E PEQUENAS EMPRESAS DO ESTADO DO TOCANTINS – SEBRAE/TO",
        "scp_tenants": [],
        # ID 146 (16 TB)
        "contrato": {"servidores": None, "volume_gb": 16384},
        "veeam": {"servidores": [], "jobs_regex": None,
                  "repositorios": ["REPO-VSP-SEBRAE-01"], "cloud_connect": []},
    },
    "rededia": {
        "nome": "Rede Dia Supermercados",
        "razao_social": "REDE DIA SUPERMERCADOS LTDA",
        "scp_tenants": [],
        # ID 133 (1 servidor) e ID 343 (800 GB)
        "contrato": {"servidores": 1, "volume_gb": 800},
        "veeam": {"servidores": [], "jobs_regex": None,
                  "repositorios": ["IDX-REPO-SUP DIA A DIA", "IDX-REPO-SUP-DIA-A-DIA-IMUTAVEL"],
                  "cloud_connect": []},
    },
    "camara": {
        "nome": "Câmara Municipal de Palmas",
        "razao_social": "CÂMARA MUNICIPAL DE PALMAS",
        "scp_tenants": [],
        # ID 2 (10 servidores) e ID 213 (3 TB); cliente roda o próprio VBR (Cloud Connect)
        "contrato": {"servidores": 10, "volume_gb": 3072},
        "veeam": {"servidores": [], "jobs_regex": None, "repositorios": [], "cloud_connect": ["CMP"]},
    },
}
