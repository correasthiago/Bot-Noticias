"""Orquestra o registro de interacoes/evidencias e o recomputo de estado.

Este e o unico ponto de entrada para escrever em raw_interaction,
evidence_event, evidence_assessment e competency_state. Ele:

- resolve o cluster de evidencia no SERVIDOR (nunca aceita um id vindo do
  chamador - Secao 6 do pacote de correcao v0.2, ver `evidence.clustering`);
- garante idempotencia REAL de avaliacao via `try_claim_evaluation`, que
  tambem permite REPROCESSAR uma interacao que ainda esta `pending`
  (Secao 5 do pacote de correcao v0.2, T8);
- nunca deixa `mere_presence` alterar estado (Secao 8, T5);
- nunca deixa um achado `inconclusive`, de confianca baixa ou com causa
  alternativa pendente promover/rebaixar estado (Secao 11 do pacote de
  correcao v0.2);
- so cria EvidenceAssessment quando o payload do avaliador e valido
  (validacao acontece ANTES de chegar aqui - ver `evaluator.contract`);
- recomputa CompetencyState sempre a partir do historico COMPLETO de
  evidencia daquela (competencia, dimensao), escrevendo na GERACAO ativa
  (Secao 10 do pacote de correcao v0.2) - nunca apaga geracoes antigas;
- rejeita (IdempotencyConflictError) uma idempotency_key reutilizada com
  atividade, sessao ou conteudo DIFERENTE do que foi persistido da
  primeira vez (Secao 3 do pacote de correcao v0.2.1) - nunca aceita
  silenciosamente qual dos dois "ganha".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from central_universal.domain.clock import parse_iso, utc_now_iso
from central_universal.domain.entities import (
    Activity,
    CompetencyState,
    EvidenceAssessment,
    EvidenceEvent,
    ProjectionGeneration,
    RawInteraction,
    RuleVersion,
)
from central_universal.domain.enums import (
    Dimension,
    EvaluationStatus,
    EvidenceRelation,
    HelpLevel,
    ProductionResult,
    ProjectionGenerationStatus,
)
from central_universal.domain.ids import new_id
from central_universal.evaluator.contract import EvaluatorOutput
from central_universal.evidence.aggregation import AggregationConfig, UsableEvidence, classify_dimension
from central_universal.evidence.clustering import resolve_cluster
from central_universal.persistence.db import transaction
from central_universal.persistence.repositories import Repositories


class IdempotencyConflictError(ValueError):
    """Levantada quando uma `idempotency_key` ja usada e reenviada com
    atividade, sessao ou conteudo diferente do que foi persistido da
    primeira vez (Secao 3 do pacote de correcao v0.2.1). Isto NUNCA e
    resolvido silenciosamente escolhendo uma das duas versoes - e um erro
    do chamador que precisa ser corrigido (uma idempotency_key deve
    identificar UMA submissao, nao ser reaproveitada para conteudo novo)."""


@dataclass
class EvaluationRecordResult:
    """Resultado de `record_evaluation`. `already_evaluated=True` significa
    que outra chamada ja tinha completado a avaliacao desta interacao
    primeiro - nada foi escrito de novo (idempotencia real, Secao 5)."""

    already_evaluated: bool
    competency_states: list[CompetencyState] = field(default_factory=list)
    assessments: list[EvidenceAssessment] = field(default_factory=list)


def ensure_active_generation(repos: Repositories, rule_version: RuleVersion) -> str:
    """Garante que existe uma geracao ativa de CompetencyState, criando a
    primeira sob demanda se o banco ainda nao tiver nenhuma (Secao 10 do
    pacote de correcao v0.2)."""

    existing = repos.active_projection_generation.get_id()
    if existing is not None:
        return existing

    generation = ProjectionGeneration(
        id=new_id(),
        rule_version_id=rule_version.id,
        created_at=utc_now_iso(),
        status=ProjectionGenerationStatus.ACTIVE,
        activated_at=utc_now_iso(),
        note="Geracao inicial criada sob demanda (banco sem historico previo).",
    )
    repos.projection_generations.insert(generation)
    repos.active_projection_generation.set(generation.id)
    return generation.id


class EvidenceService:
    def __init__(self, repos: Repositories) -> None:
        self.repos = repos

    def record_interaction(
        self,
        *,
        activity: Activity,
        session_id: str,
        idempotency_key: str,
        learner_input: str,
        tutor_output: str,
        help_level: HelpLevel,
        production_result: ProductionResult,
        occurred_at: str | None = None,
    ) -> tuple[RawInteraction, bool]:
        """Retorna (interacao, criada_agora). Submissao repetida da mesma
        idempotency_key devolve a interacao ja existente sem duplicar nada
        (Secao 24, T8). O cluster de evidencia e resolvido no SERVIDOR a
        partir de (sessao, atividade) - nunca informado pelo chamador."""

        existing = self.repos.raw_interactions.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            if (
                existing.activity_id != activity.id
                or existing.session_id != session_id
                or existing.learner_input != learner_input
                or existing.help_level != help_level
                or existing.production_result != production_result
            ):
                raise IdempotencyConflictError(
                    f"idempotency_key '{idempotency_key}' ja foi usada para uma "
                    f"RawInteraction com atividade/sessao/conteudo diferente "
                    f"(persistido: activity={existing.activity_id!r} session={existing.session_id!r} "
                    f"help_level={existing.help_level.value!r} production_result={existing.production_result.value!r}; "
                    f"recebido agora: activity={activity.id!r} session={session_id!r} "
                    f"help_level={help_level.value!r} production_result={production_result.value!r})"
                )
            return existing, False

        cluster = resolve_cluster(self.repos, session_id=session_id, activity=activity)

        interaction = RawInteraction(
            id=new_id(),
            activity_id=activity.id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            learner_input=learner_input,
            tutor_output=tutor_output,
            help_level=help_level,
            production_result=production_result,
            occurred_at=occurred_at or utc_now_iso(),
            evidence_cluster_id=cluster.id,
            evaluation_status=EvaluationStatus.PENDING,
        )
        self.repos.raw_interactions.insert(interaction)
        return interaction, True

    def record_evaluation(
        self,
        *,
        raw_interaction: RawInteraction,
        evaluator_output: EvaluatorOutput,
        rule_version: RuleVersion,
        evaluator_provider_event_id: str | None = None,
    ) -> EvaluationRecordResult:
        # Idempotencia real (Secao 5, T8): a transicao pending->completed e
        # atomica e so pode acontecer uma vez. Se outra chamada ja
        # completou esta avaliacao, nao escrevemos NADA aqui - e seguro
        # reprocessar uma interacao pending quantas vezes for preciso, mas
        # uma ja completed nunca gera evidencia duplicada.
        claimed = self.repos.raw_interactions.try_claim_evaluation(raw_interaction.id)
        if not claimed:
            return EvaluationRecordResult(already_evaluated=True)

        touched: set[tuple[str, Dimension]] = set()
        assessments_created: list[EvidenceAssessment] = []

        for finding in evaluator_output.findings:
            event = EvidenceEvent(
                id=new_id(),
                raw_interaction_id=raw_interaction.id,
                competency_id=finding.competency_id,
                dimension=finding.dimension,
                relation=finding.relation,
                help_level=raw_interaction.help_level,
                production_result=raw_interaction.production_result,
                evidence_cluster_id=raw_interaction.evidence_cluster_id,
                created_at=utc_now_iso(),
            )
            self.repos.evidence_events.insert(event)

            assessment = EvidenceAssessment(
                id=new_id(),
                evidence_event_id=event.id,
                rule_version_id=rule_version.id,
                classification=finding.classification,
                result=finding.result,
                confidence=finding.confidence,
                justification=finding.justification,
                alternative_cause=finding.alternative_cause,
                inconclusive=finding.inconclusive,
                evaluator_provider_event_id=evaluator_provider_event_id,
                created_at=utc_now_iso(),
            )
            self.repos.evidence_assessments.insert(assessment)
            assessments_created.append(assessment)

            # Principio: mere_presence NAO atualiza estado de competencia.
            if finding.relation != EvidenceRelation.MERE_PRESENCE:
                touched.add((finding.competency_id, finding.dimension))

        config = AggregationConfig.from_json(rule_version.config_json)
        generation_id = ensure_active_generation(self.repos, rule_version)

        competency_states = [
            self.recompute_state(competency_id, dimension, rule_version, generation_id, config)
            for competency_id, dimension in sorted(touched)
        ]

        return EvaluationRecordResult(
            already_evaluated=False,
            competency_states=competency_states,
            assessments=assessments_created,
        )

    def recompute_state(
        self,
        competency_id: str,
        dimension: Dimension,
        rule_version: RuleVersion,
        generation_id: str | None = None,
        config: AggregationConfig | None = None,
    ) -> CompetencyState:
        generation_id = generation_id or ensure_active_generation(self.repos, rule_version)
        config = config or AggregationConfig.from_json(rule_version.config_json)

        events = self.repos.evidence_events.list_for_competency(competency_id, dimension)

        usable: list[UsableEvidence] = []
        last_assessment_id: str | None = None
        last_created_at: str | None = None

        for event in events:
            if event.relation == EvidenceRelation.MERE_PRESENCE:
                continue
            assessments = self.repos.evidence_assessments.get_for_evidence_event(event.id)
            if not assessments:
                continue
            latest = max(assessments, key=lambda a: a.created_at)

            # Secao 11 do pacote de correcao v0.2: achados inconclusivos ou
            # com causa alternativa pendente nunca promovem/rebaixam nada.
            if latest.inconclusive or latest.alternative_cause:
                continue

            usable.append(
                UsableEvidence(
                    evidence_type=latest.classification,
                    relation=event.relation,
                    help_level=event.help_level,
                    evidence_cluster_id=event.evidence_cluster_id,
                    confidence=latest.confidence,
                    created_at=parse_iso(event.created_at),
                )
            )
            if last_created_at is None or event.created_at > last_created_at:
                last_created_at = event.created_at
                last_assessment_id = latest.id

        result = classify_dimension(dimension, usable, config)

        state = CompetencyState(
            id=new_id(),
            generation_id=generation_id,
            competency_id=competency_id,
            dimension=dimension,
            state=result.state,
            possible_regression=result.possible_regression,
            has_unresolved_contradiction=result.has_unresolved_contradiction,
            last_evidence_assessment_id=last_assessment_id,
            rule_version_id=rule_version.id,
            computed_at=utc_now_iso(),
            explanation=result.explanation,
        )
        self.repos.competency_states.insert(state)
        return state


def recompute_all_from_log(repos: Repositories, rule_version: RuleVersion) -> list[CompetencyState]:
    """Reconstroi a projecao competency_state a partir do zero, usando
    apenas raw_interaction + evidence_event + evidence_assessment +
    rule_version - mas SEM apagar nada (Secao 10 do pacote de correcao
    v0.2, T10). Constroi uma GERACAO NOVA numa unica transacao, valida
    (todo par (competencia, dimensao) esperado foi computado) e so entao
    move o ponteiro de geracao ativa. Se qualquer coisa falhar, a
    transacao inteira e desfeita e a geracao anterior continua ativa e
    intacta - o historico nunca e perdido.
    """

    config = AggregationConfig.from_json(rule_version.config_json)
    service = EvidenceService(repos)

    pairs: set[tuple[str, Dimension]] = set()
    for event in repos.evidence_events.list_all():
        if event.relation == EvidenceRelation.MERE_PRESENCE:
            continue
        pairs.add((event.competency_id, event.dimension))

    with transaction(repos.conn):
        new_generation = ProjectionGeneration(
            id=new_id(),
            rule_version_id=rule_version.id,
            created_at=utc_now_iso(),
            status=ProjectionGenerationStatus.BUILDING,
            note="Reconstrucao completa a partir do event log.",
        )
        repos.projection_generations.insert(new_generation)

        built_states = [
            service.recompute_state(competency_id, dimension, rule_version, new_generation.id, config)
            for competency_id, dimension in sorted(pairs)
        ]

        rebuilt = repos.competency_states.list_by_generation(new_generation.id)
        if len(rebuilt) != len(pairs):
            raise RuntimeError(
                f"validacao de geracao falhou: esperava {len(pairs)} linha(s), obteve {len(rebuilt)}"
            )

        previous_generation_id = repos.active_projection_generation.get_id()

        repos.projection_generations.set_status(
            new_generation.id, ProjectionGenerationStatus.ACTIVE, activated_at=utc_now_iso()
        )
        repos.active_projection_generation.set(new_generation.id)

        if previous_generation_id is not None and previous_generation_id != new_generation.id:
            # So a geracao ATIVA pode ficar com status 'active' por vez -
            # a anterior vira 'superseded', mas suas linhas continuam no
            # banco para sempre (nunca sao apagadas).
            repos.projection_generations.set_status(
                previous_generation_id, ProjectionGenerationStatus.SUPERSEDED
            )

    return built_states
