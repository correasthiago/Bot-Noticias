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
V0_2_2_VERSION = "v0.2.2"

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


def build_v0_2_2_rule_version() -> RuleVersion:
    """RuleVersion da terceira auditoria pos-entrega (pacote de correcao
    v0.2.2). Corrige um erro de categoria que sobreviveu a v0.2.1: usar
    `EvidenceAssessment.confidence` para decidir Easy/Good/Hard era, na
    pratica, o MESMO tipo de problema que a v0.2.1 ja tinha corrigido uma
    vez (usar um sinal que nao mede o que precisa medir) - `confidence`
    mede o quanto o AVALIADOR confia no proprio julgamento de
    classificacao, nunca o quao FACIL foi para o APRENDIZ recuperar a
    informacao. Uma avaliacao positiva de alta confianca virava "Easy"
    automaticamente mesmo quando a resposta so saiu certa com uma pista
    explicita.

    Mudancas de politica desta versao (Secao 2 da terceira auditoria
    pos-entrega):

    - A nota FSRS (Easy/Good/Hard) passa a vir EXCLUSIVAMENTE de dois
      sinais OBSERVAVEIS do processo de recuperacao da propria tentativa -
      `help_level` (quanto suporte foi dado ANTES da resposta) e
      `production_result` (como a resposta correta foi alcancada) - nunca
      de `confidence` (ver `memory.fsrs_adapter.derive_recall_rating` para
      a tabela completa de decisao). `confidence` continua sendo usada,
      mas SO como filtro de elegibilidade (`recall_min_confidence`) -
      nunca mais como sinal de facilidade. Os dois limiares de confianca
      que a v0.2.1 introduzira para isso
      (`recall_rating_easy_min_confidence`/`..._good_min_confidence`)
      foram REMOVIDOS de `AggregationConfig` - nao fazem mais sentido.
    - `first_review_min_interval_since_learning_seconds` (NOVO): a
      PRIMEIRA revisao de uma competencia (quando ainda nao existe
      `memory_state.last_review_at` para comparar) tambem precisa de um
      intervalo minimo verificado - antes, essa checagem simplesmente nao
      acontecia na primeira revisao. O intervalo e medido desde a
      PRIMEIRA evidencia ja registrada para a competencia (proxy
      observavel de "quando o aprendiz comecou a aprender isto").

    Os VALORES continuam sendo defaults provisorios (nenhuma calibracao
    real com aprendizes existe ainda) - o que muda e a formula que os usa,
    nao um ajuste fino de numeros."""

    config = AggregationConfig()
    return RuleVersion(
        id=new_id(),
        version=V0_2_2_VERSION,
        description=(
            "Terceira auditoria pos-entrega: (1) o 'estado atual' de uma projecao "
            "(CompetencyState) e escolhido por sequence_number - um inteiro "
            "monotonico explicito atribuido pelo repositorio na propria escrita - "
            "nunca mais por computed_at+id (que podia empatar sob escritas rapidas "
            "e ser desempatado por um uuid aleatorio, produzindo uma escolha "
            "nao-deterministica); (2) a nota FSRS de uma recuperacao vem "
            "exclusivamente de sinais OBSERVAVEIS da propria tentativa "
            "(help_level + production_result) - nunca da confianca do avaliador, "
            "que mede outra coisa (o quanto o avaliador confia no julgamento de "
            "classificacao, nao o quao facil foi a recuperacao); a PRIMEIRA "
            "revisao de uma competencia tambem verifica o intervalo desde a "
            "primeira evidencia/aprendizagem, nao so entre revisoes subsequentes; "
            "(3) a restauracao de backup mantem exclusividade no banco ativo do "
            "inicio da checagem ate o fim da troca OU reversao - uma conexao "
            "aberta durante a janela de copia/troca tambem aborta a restauracao, "
            "nao so uma aberta antes de comecar; (4) o registro de aplicacao da "
            "migration 0002 em schema_migrations entra na MESMA transacao atomica "
            "do resto da migration - uma falha ao gravar esse registro reverte o "
            "esquema inteiro para a versao anterior, nunca deixa a migration "
            "'quase aplicada'."
        ),
        created_at=utc_now_iso(),
        config_json=config.to_json(),
        algorithm_version=ALGORITHM_V2,
    )
