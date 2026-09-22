"""Definicoes de RuleVersion conhecidas do sistema.

Cada mudanca de regra pedagogica cria uma nova RuleVersion (Principio 18).
Este modulo e o unico lugar que "registra" o CONTEUDO de uma versao - o
resto do sistema so le RuleVersion.config_json, nunca hardcoda thresholds
(Secao 8/9 do pacote de correcao v0.2).
"""

from __future__ import annotations

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import RuleVersion
from central_universal.domain.ids import new_id
from central_universal.evidence.aggregation import AggregationConfig

V0_1_0_VERSION = "v0.1.0"
V0_2_0_VERSION = "v0.2.0"

# Algoritmo original (pre Red Team): thresholds hardcoded em
# aggregation.py, corroboracao de regressao por 2-de-3 recentes, cluster
# de evidencia igual a um id arbitrario passado pelo chamador.
ALGORITHM_V1 = "v1-hardcoded-thresholds"

# Algoritmo corrigido (pos Red Team, pacote de correcao v0.2): thresholds
# vem de config_json, cluster e computado no servidor, regressao nunca e
# rebaixada automaticamente, projecao usa geracoes em vez de reconstrucao
# destrutiva. Ver DECISIONS.md e PEDAGOGICAL_CONSTITUTION.md.
ALGORITHM_V2 = "v2-config-driven-clusters-no-autodowngrade"


def build_v0_2_0_rule_version() -> RuleVersion:
    """A RuleVersion corrigida apos o Red Team pos-entrega. Os valores de
    `AggregationConfig` aqui sao EXPLICITAMENTE os mesmos numeros
    provisorios ja documentados em DECISIONS.md (2/3 clusters, 2/3 dias de
    retencao) - a diferenca de v0.1.0 nao e o VALOR dos thresholds, e que
    agora eles sao dados de configuracao imutavel e auditavel em vez de
    constantes soltas no codigo, e que o algoritmo ao redor deles mudou
    (sem auto-downgrade, clusters server-side, etc.)."""

    config = AggregationConfig()
    return RuleVersion(
        id=new_id(),
        version=V0_2_0_VERSION,
        description=(
            "Correcoes pos Red Team: EvidenceEvent/EvidenceAssessment separados; "
            "clusters computados no servidor com no maximo uma contribuicao por "
            "cluster/competencia/dimensao/direcao; regressao nunca rebaixa "
            "automaticamente (so sinaliza, pendente de validacao deliberada); "
            "evidencia incidental negativa gera hipotese, nunca regressao; "
            "achados inconclusivos/baixa confianca/causa alternativa pendente "
            "excluidos da agregacao e da memoria; revisao FSRS so ocorre via "
            "MemoryObservation explicito (recuperacao planejada, intervalo "
            "relevante, avaliacao valida e conclusiva); CompetencyState "
            "reconstruido por geracoes (nunca destrutivamente); prerequisitos "
            "bloqueados na escrita; RuleVersion/DecisionEvent/ProviderEvent/"
            "MemoryReviewLog/BackupEvent imutaveis no banco."
        ),
        created_at=utc_now_iso(),
        config_json=config.to_json(),
        algorithm_version=ALGORITHM_V2,
    )
