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
V0_2_1_VERSION = "v0.2.1"

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


def build_v0_2_1_rule_version() -> RuleVersion:
    """RuleVersion da segunda auditoria pos-entrega (pacote de correcao
    v0.2.1). Mesma familia de algoritmo de v0.2.0 (`ALGORITHM_V2`) - o que
    muda aqui e que a POLITICA de revisao de memoria (nota FSRS e
    intervalo minimo entre recuperacoes) passa a ser EXPLICITA e
    versionada nesta RuleVersion, em vez de viver apenas nos defaults da
    dataclass `AggregationConfig` (Principio 18: "mudar um numero e criar
    uma nova RuleVersion, nunca editar uma constante de modulo"):

    - `recall_rating_easy_min_confidence` / `recall_rating_good_min_confidence`:
      a nota FSRS (1-4) de uma recuperacao deixou de vir do
      `ProductionResult` bruto da interacao - agora vem EXCLUSIVAMENTE da
      classificacao/confianca da propria `EvidenceAssessment` de RETENTION
      daquela tentativa (ver `memory.fsrs_adapter.derive_recall_rating`).
      Uma avaliacao NEGATIVE sempre vira "Again"; POSITIVE vira
      Easy/Good/Hard conforme esses dois limiares de confianca.
    - `recall_min_confidence`: confianca minima da avaliacao de RETENTION
      para a tentativa contar como observacao de memoria valida.
    - `recall_min_interval_seconds`: intervalo minimo desde a ultima
      revisao para a nova tentativa contar como uma observacao DISTINTA -
      evita que tentativas segundos apartadas sejam tratadas como duas
      revisoes espacadas.

    Os VALORES continuam sendo os mesmos defaults provisorios ja em uso
    (nenhuma calibracao real com aprendizes existe ainda) - o que muda e
    que agora estao aqui, explicitos e auditaveis, nao apenas implicitos
    no default de uma dataclass Python."""

    config = AggregationConfig()
    return RuleVersion(
        id=new_id(),
        version=V0_2_1_VERSION,
        description=(
            "Segunda auditoria pos-entrega: (1) o primeiro card FSRS nasce pelo "
            "fluxo normal da aplicacao - is_planned_recall e SEMPRE derivado da "
            "acao SCHEDULE_RECALL escolhida pelo Decisor, nunca informado por um "
            "chamador; (2) a nota FSRS de uma recuperacao vem exclusivamente da "
            "EvidenceAssessment de RETENTION da propria tentativa (classificacao + "
            "confianca via recall_rating_easy_min_confidence/recall_rating_good_min_confidence), "
            "nunca do ProductionResult bruto, e exige relacao target, confianca >= "
            "recall_min_confidence e intervalo desde a ultima revisao >= "
            "recall_min_interval_seconds; (3) reprocessamento de uma interacao "
            "pendente usa exclusivamente a RawInteraction persistida, e uma "
            "idempotency_key reutilizada com atividade/sessao/conteudo diferente e "
            "rejeitada (IdempotencyConflictError), inclusive apos reinicio do "
            "processo; (4) a migration 0002 roda como uma transacao atomica, "
            "preserva TODAS as avaliacoes v0.1 (normalizando em vez de descartar "
            "as incompativeis com o novo CHECK) e tira backup automatico antes de "
            "alterar um banco existente; (5) restore impede novas escritas (recusa "
            "prosseguir se outra conexao ainda estiver aberta), achata o WAL e "
            "remove seus arquivos auxiliares antes de trocar o banco, e mostra "
            "falha ao usuario quando nao pode ser concluido; (6) o cluster de "
            "evidencia e definido por (sessao, tipo de atividade, competencias "
            "alvo) - nunca pelo texto do prompt, o que permitia que exercicios com "
            "textos superficialmente distintos mas pedagogicamente previsiveis "
            "forjassem independencia."
        ),
        created_at=utc_now_iso(),
        config_json=config.to_json(),
        algorithm_version=ALGORITHM_V2,
    )
