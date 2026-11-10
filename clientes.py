"""Cadastro dos clientes do relatório: o que pertence a cada um no Veeam ONE e no SCP.

Cada cliente define como achar os dados dele:

  scp_tenants            nomes exatos dos tenants (projects) no SCP
  veeam.servidores       IPs/nomes de VBRs dedicados ao cliente (tudo que estiver neles entra)
  veeam.jobs_regex       regex aplicada ao NOME do job (para VBRs compartilhados)
  veeam.repositorios     repositórios do cliente (backups gravados neles entram; o uso do
                         disco aparece na seção de armazenamento)
  veeam.cloud_connect    tenants do Veeam Cloud Connect (cota usada / contratada)

  contrato               quantidade contratada, mesmos valores do CONTRATOS_BACKUP do automacao_veeam:
                           servidores  quantidade de servidores (None = não contratado)
                           volumes     lista de volumetrias contratadas, cada uma com
                                         gb       volume em GB binário (1 TB = 1024 GB)
                                         medicao  onde o volume é medido (obrigatório):
                                                  "frontend" = na ORIGEM: tamanho dos dados dos
                                                               servidores protegidos (usedSourceSize)
                                                  "backend"  = no DESTINO: espaço ocupado pelas cópias
                                                               no repositório / Cloud Connect
                                         maquinas (opcional, só frontend) restringe às máquinas
                                                  cobertas por esse item do contrato

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
        "contrato": {"servidores": None, "volumes": []},
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
        # automacao_veeam CONTRATOS_BACKUP: ID 174 (30 servidores) e ID 237 (5 TB = 5120 GB, Back-End)
        "contrato": {"servidores": 30, "volumes": [{"gb": 5120, "medicao": "backend"}]},
        "veeam": {
            # VBR local da Uniggel: todos os jobs dele são do cliente
            "servidores": ["10.10.100.15"],
            "jobs_regex": None,
            "repositorios": ["REPO-LOCAL-UNIGGEL"],
            "cloud_connect": ["uniggel.sementes"],
        },
    },
    # Clientes Veeam (a Mix também tem tenant na nuvem Sangfor). Contrato = IDs do CONTRATOS_BACKUP do automacao_veeam;
    # linhas "Backup Front-End" (Medicao_Volumetria = frontend) entram com medicao "frontend"
    "mix": {
        "nome": "Mix Alimentos",
        "razao_social": "MIX ALIMENTOS LTDA",
        "scp_tenants": ["TN - MIX ALIMENTOS"],
        # ID 102 (5 servidores) e ID 226 (4 TB Front-End)
        "contrato": {"servidores": 5, "volumes": [{"gb": 4096, "medicao": "frontend"}]},
        "veeam": {"servidores": [], "jobs_regex": None,
                  "repositorios": ["IDX-REPO-MIX-ALIMENTOS", "IDX-REPO-MIX-ALIMENTOS-IMUTAVEL"],
                  "cloud_connect": []},
    },
    "akc": {
        "nome": "AKC Diagnósticos",
        "razao_social": "ARAI, KAMINISHI, COSTA & CIA LTDA",
        "scp_tenants": [],
        # ID 12 (2 servidores), ID 217 (75 TB Back-End, repositórios inteiros) e ID 215
        # (1,2 TB Front-End, só os 2 servidores FISIOLOGIA do ID 12)
        "contrato": {"servidores": 2, "volumes": [
            {"gb": 76800, "medicao": "backend"},
            {"gb": 1228.8, "medicao": "frontend",
             "maquinas": ["FISIOLOGIA-01.akc.diagnosticos", "FISIOLOGIA-02.akc.diagnosticos"]},
        ]},
        "veeam": {"servidores": [], "jobs_regex": None,
                  "repositorios": ["REPO-AKC01", "REPO-AKC02"], "cloud_connect": []},
    },
    "fomento": {
        "nome": "Agência de Fomento",
        "razao_social": "AGÊNCIA DE FOMENTO DO ESTADO DO TOCANTINS S/A",
        "scp_tenants": [],
        # ID 66 (15 servidores) e ID 346 (8 TB Back-End)
        "contrato": {"servidores": 15, "volumes": [{"gb": 8192, "medicao": "backend"}]},
        "veeam": {"servidores": [], "jobs_regex": None,
                  "repositorios": ["IDX-REPO-FOMENTO"], "cloud_connect": []},
    },
    "sesi": {
        "nome": "SESI-SENAI-FIETO",
        "razao_social": "SESI-SENAI-FIETO - DR/TO",
        "scp_tenants": [],
        # ID 286 (29 servidores) e ID 287 (7 TB Back-End)
        "contrato": {"servidores": 29, "volumes": [{"gb": 7168, "medicao": "backend"}]},
        "veeam": {"servidores": [], "jobs_regex": None,
                  "repositorios": ["IDX-REPO-SESI01"], "cloud_connect": []},
    },
    "sebrae": {
        "nome": "SEBRAE/TO",
        "razao_social": "SERVIÇO DE APOIO ÀS MICRO E PEQUENAS EMPRESAS DO ESTADO DO TOCANTINS – SEBRAE/TO",
        "scp_tenants": [],
        # ID 146 (16 TB Back-End)
        "contrato": {"servidores": None, "volumes": [{"gb": 16384, "medicao": "backend"}]},
        "veeam": {"servidores": [], "jobs_regex": None,
                  "repositorios": ["REPO-VSP-SEBRAE-01"], "cloud_connect": []},
    },
    "rededia": {
        "nome": "Rede Dia Supermercados",
        "razao_social": "REDE DIA SUPERMERCADOS LTDA",
        "scp_tenants": [],
        # ID 133 (1 servidor) e ID 343 (800 GB Back-End)
        "contrato": {"servidores": 1, "volumes": [{"gb": 800, "medicao": "backend"}]},
        "veeam": {"servidores": [], "jobs_regex": None,
                  "repositorios": ["IDX-REPO-SUP DIA A DIA", "IDX-REPO-SUP-DIA-A-DIA-IMUTAVEL"],
                  "cloud_connect": []},
    },
    "camara": {
        "nome": "Câmara Municipal de Palmas",
        "razao_social": "CÂMARA MUNICIPAL DE PALMAS",
        "scp_tenants": [],
        # ID 2 (10 servidores) e ID 213 (3 TB Back-End); cliente roda o próprio VBR (Cloud Connect):
        # só a cota usada no destino é visível, Front-End não seria mensurável
        "contrato": {"servidores": 10, "volumes": [{"gb": 3072, "medicao": "backend"}]},
        "veeam": {"servidores": [], "jobs_regex": None, "repositorios": [], "cloud_connect": ["CMP"]},
    },
}
