"""Orquestra o registro de interacoes/evidencias e o recomputo de estado.

Este e o unico ponto de entrada para escrever em raw_interaction,
evidence_event, evidence_assessment e competency_state. Ele:

- garante idempotencia (Secao 24, T8);
- nunca deixa `mere_presence` alterar estado (Secao 8, T5);
- so cria EvidenceAssessment quando o payload do avaliador e valido
  (validacao acontece ANTES de chegar aqui - ver `evaluator.contract`);
- recomputa CompetencyState sempre a partir do historico COMPLETO de
  evidencia daquela (competencia, dimensao), nunca incrementalmente por
  diffs - o que torna a projecao trivialmente reconstruivel (Secao 14, T10).
"""

from __future__ import annotations

from central_universal.domain.clock import parse_iso, utc_now_iso
from central_universal.domain.entities import (
    CompetencyState,
    EvidenceAssessment,
    EvidenceEvent,
    RawInteraction,
    RuleVersion,
)
from central_universal.domain.enums import Dimension, EvidenceRelation, HelpLevel, ProductionResult
from central_universal.domain.ids import new_id
from central_universal.evaluator.contract import EvaluatorOutput
from central_universal.evidence.aggregation import UsableEvidence, classify_dimension
from central_universal.persistence.repositories import Repositories


class EvidenceService:
    def __init__(self, repos: Repositories) -> None:
        self.repos = repos

    def record_interaction(
        self,
        *,
        activity_id: str,
        session_id: str,
        idempotency_key: str,
        learner_input: str,
        tutor_output: str,
        help_level: HelpLevel,
        production_result: ProductionResult,
        evidence_cluster_id: str,
        occurred_at: str | None = None,
    ) -> tuple[RawInteraction, bool]:
        """Retorna (interacao, criada_agora). Submissao repetida da mesma
        idempotency_key devolve a interacao ja existente sem duplicar nada
        (Secao 24, T8)."""

        existing = self.repos.raw_interactions.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing, False

        interaction = RawInteraction(
            id=new_id(),
            activity_id=activity_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            learner_input=learner_input,
            tutor_output=tutor_output,
            help_level=help_level,
            production_result=production_result,
            occurred_at=occurred_at or utc_now_iso(),
            evidence_cluster_id=evidence_cluster_id,
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
    ) -> list[CompetencyState]:
        touched: set[tuple[str, Dimension]] = set()

        for finding in evaluator_output.findings:
            event = EvidenceEvent(
                id=new_id(),
                raw_interaction_id=raw_interaction.id,
                competency_id=finding.competency_id,
                dimension=finding.dimension,
                evidence_type=finding.classification,
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

            # Principio: mere_presence NAO atualiza estado de competencia.
            if finding.relation != EvidenceRelation.MERE_PRESENCE:
                touched.add((finding.competency_id, finding.dimension))

        return [
            self.recompute_state(competency_id, dimension, rule_version)
            for competency_id, dimension in sorted(touched)
        ]

    def recompute_state(
        self, competency_id: str, dimension: Dimension, rule_version: RuleVersion
    ) -> CompetencyState:
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
            usable.append(
                UsableEvidence(
                    evidence_type=latest.classification,
                    relation=event.relation,
                    help_level=event.help_level,
                    evidence_cluster_id=event.evidence_cluster_id,
                    created_at=parse_iso(event.created_at),
                )
            )
            if last_created_at is None or event.created_at > last_created_at:
                last_created_at = event.created_at
                last_assessment_id = latest.id

        result = classify_dimension(dimension, usable)

        state = CompetencyState(
            id=new_id(),
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
    """Reconstroi TODA a projecao competency_state a partir do zero, usando
    apenas raw_interaction + evidence_event + evidence_assessment +
    rule_version. Usada pelo teste de reconstrucao (Secao 14, T10) e pelo
    endpoint de integridade/manutencao.
    """

    service = EvidenceService(repos)
    repos.competency_states.delete_all()

    pairs: set[tuple[str, Dimension]] = set()
    for event in repos.evidence_events.list_all():
        if event.relation == EvidenceRelation.MERE_PRESENCE:
            continue
        pairs.add((event.competency_id, event.dimension))

    return [
        service.recompute_state(competency_id, dimension, rule_version)
        for competency_id, dimension in sorted(pairs)
    ]
