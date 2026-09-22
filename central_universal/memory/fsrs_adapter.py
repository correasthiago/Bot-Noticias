"""Adapter isolando Py-FSRS do resto do sistema (Secao 13).

Nenhum outro modulo deve importar o pacote `fsrs` diretamente - so este
arquivo conhece `fsrs.Card`, `fsrs.Scheduler`, `fsrs.Rating`, `fsrs.State`
e `fsrs.ReviewLog`.

v0.2 (pacote de correcao pos Red Team): o `ProductionResult` de uma
interacao NUNCA dispara uma revisao FSRS sozinho. O UNICO gatilho
legitimo e um `MemoryObservation` explicito (Secao 1 do pacote de
correcao), emitido somente quando `evaluate_recall_eligibility` confirma
que:

- a atividade foi PLANEJADA como recuperacao (`Activity.is_planned_recall`,
  derivado pelo orquestrador quando o Decisor escolhe SCHEDULE_RECALL -
  nunca setado diretamente por um chamador);
- existe uma EvidenceAssessment para a dimensao RETENTION, com relacao
  `target` (nunca incidental nem mere_presence);
- essa avaliacao e valida e conclusiva (nao inconclusive, sem
  alternative_cause pendente) e tem confianca >= `recall_min_confidence`;
- o intervalo desde a ultima revisao e >= `recall_min_interval_seconds`
  (ambos configuraveis via `RuleVersion.config_json` - Secao 2 do pacote
  de correcao v0.2.1).

v0.2.1 (auditoria pos-correcao): a NOTA FSRS (1-4) deixou de vir de
`ProductionResult` (removido - `rating_from_production_result` nao
existe mais). Ela e derivada EXCLUSIVAMENTE de
`EvidenceAssessment.classification`/`confidence` da propria avaliacao de
recuperacao, via `derive_recall_rating`.

`memory_state` (o "card atual") e `memory_review_log` (o historico) sao
escritos numa UNICA transacao junto com o proprio `MemoryObservation`:
qualquer falha em qualquer uma das tres gravacoes desfaz as tres, e o
`memory_state` anterior permanece byte a byte identico (Secao 3 e T14).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import fsrs

from central_universal.domain.clock import parse_iso, to_utc_iso, utc_now
from central_universal.domain.entities import (
    Activity,
    EvidenceAssessment,
    MemoryObservation,
    MemoryReviewLog,
    MemoryState,
)
from central_universal.domain.enums import Dimension, EvidenceRelation, MemoryCardState
from central_universal.domain.ids import new_id
from central_universal.evidence.aggregation import AggregationConfig
from central_universal.persistence.db import transaction
from central_universal.persistence.repositories import Repositories

_STATE_MAP = {
    fsrs.State.Learning: MemoryCardState.LEARNING,
    fsrs.State.Review: MemoryCardState.REVIEW,
    fsrs.State.Relearning: MemoryCardState.RELEARNING,
}


def derive_recall_rating(assessment: EvidenceAssessment, config: AggregationConfig) -> int | None:
    """Deriva a nota FSRS (1-4) EXCLUSIVAMENTE da avaliacao especifica de
    recuperacao (classificacao + confianca) - nunca do `ProductionResult`
    bruto da interacao (Secao 2 do pacote de correcao v0.2.1).

    Uma avaliacao NEGATIVE sempre vira "Again" (o FSRS precisa desse sinal
    para modelar esquecimento). Uma avaliacao POSITIVE vira Easy/Good/Hard
    conforme a confianca do avaliador, usando os limiares configurados na
    RuleVersion. Contraditoria/inconclusiva nunca chegam aqui - ja sao
    filtradas por `evaluate_recall_eligibility`.
    """

    from central_universal.domain.enums import EvidenceType

    if assessment.classification == EvidenceType.NEGATIVE:
        return int(fsrs.Rating.Again)
    if assessment.classification == EvidenceType.POSITIVE:
        if assessment.confidence >= config.recall_rating_easy_min_confidence:
            return int(fsrs.Rating.Easy)
        if assessment.confidence >= config.recall_rating_good_min_confidence:
            return int(fsrs.Rating.Good)
        return int(fsrs.Rating.Hard)
    return None


@dataclass(frozen=True)
class MemoryObservationEligibility:
    """Resultado da checagem de elegibilidade (Secao 1 e 2 do pacote de
    correcao v0.2). `eligible=False` sempre vem com `reason` legivel -
    nada aqui e um "nao" silencioso."""

    eligible: bool
    reason: str
    interval_days: float | None = None
    rating: int | None = None


@dataclass
class MemoryReviewError:
    message: str


def evaluate_recall_eligibility(
    *,
    activity: Activity,
    evidence_relation: EvidenceRelation | None,
    evidence_dimension: Dimension | None,
    assessment: EvidenceAssessment | None,
    memory_state: MemoryState | None,
    config: AggregationConfig | None = None,
    now: datetime | None = None,
) -> MemoryObservationEligibility:
    """Decide se esta interacao pode gerar um MemoryObservation.

    Cobre explicitamente a Secao 2 do pacote de correcao v0.2/v0.2.1: nao
    revisa quando o avaliador falhou (assessment is None), a evidencia nao
    e da dimensao RETENTION, a relacao nao e `target` (evidencia
    incidental/mere_presence NUNCA conta), a classificacao e inconclusiva,
    a confianca e baixa demais, ha causa alternativa pendente, a atividade
    nao e uma recuperacao planejada, ou o intervalo desde a ultima revisao
    e menor que o minimo definido na regra.
    """

    config = config or AggregationConfig()

    if not activity.is_planned_recall:
        return MemoryObservationEligibility(False, "atividade nao foi planejada como recuperacao agendada")

    if assessment is None:
        return MemoryObservationEligibility(False, "sem avaliacao valida associada (avaliador falhou ou payload invalido)")

    if evidence_dimension != Dimension.RETENTION:
        got = evidence_dimension.value if evidence_dimension else "nenhuma"
        return MemoryObservationEligibility(
            False, f"avaliacao e da dimensao '{got}', recuperacao exige uma avaliacao de 'retention'"
        )

    if evidence_relation != EvidenceRelation.TARGET:
        got = evidence_relation.value if evidence_relation else "nenhuma"
        return MemoryObservationEligibility(
            False, f"relacao da evidencia e '{got}', recuperacao exige relacao 'target' (nunca incidental)"
        )

    if assessment.inconclusive:
        return MemoryObservationEligibility(False, "avaliacao inconclusiva")

    if assessment.alternative_cause:
        return MemoryObservationEligibility(False, "causa alternativa pendente nao resolvida")

    if assessment.confidence < config.recall_min_confidence:
        return MemoryObservationEligibility(
            False,
            f"confianca {assessment.confidence:.2f} abaixo do minimo exigido pela regra "
            f"({config.recall_min_confidence:.2f})",
        )

    rating = derive_recall_rating(assessment, config)
    if rating is None:
        return MemoryObservationEligibility(False, "classificacao da avaliacao nao produz uma nota de recuperacao valida")

    interval_seconds: float | None = None
    if memory_state is not None and memory_state.last_review_at:
        reference = now or utc_now()
        interval_seconds = (reference - parse_iso(memory_state.last_review_at)).total_seconds()
        if interval_seconds < config.recall_min_interval_seconds:
            return MemoryObservationEligibility(
                False,
                f"intervalo de {interval_seconds:.1f}s desde a ultima revisao e menor que o minimo "
                f"definido pela regra ({config.recall_min_interval_seconds:.1f}s)",
            )

    interval_days = interval_seconds / 86400.0 if interval_seconds is not None else None
    return MemoryObservationEligibility(True, "elegivel", interval_days=interval_days, rating=rating)


class MemoryAdapter:
    def __init__(self, repos: Repositories, scheduler: fsrs.Scheduler | None = None) -> None:
        self.repos = repos
        self.scheduler = scheduler or fsrs.Scheduler()

    def get_or_create_state(self, competency_id: str) -> MemoryState:
        existing = self.repos.memory_states.get(competency_id)
        if existing is not None:
            return existing

        card = fsrs.Card()
        state = self._state_from_card(competency_id, card, existing_id=new_id())
        self.repos.memory_states.upsert(state)
        return state

    def observe_and_review(
        self,
        *,
        competency_id: str,
        raw_interaction_id: str,
        evidence_assessment_id: str,
        eligibility: MemoryObservationEligibility,
        review_datetime: datetime | None = None,
        review_duration: int | None = None,
    ) -> MemoryState | MemoryReviewError:
        """UNICO ponto de entrada que efetivamente chama o FSRS e grava
        MemoryObservation. So deve ser chamado com `eligibility.eligible`
        True - o chamador (orchestration) e responsavel por checar
        `evaluate_recall_eligibility` antes."""

        if not eligibility.eligible or eligibility.rating is None:
            return MemoryReviewError(message=f"observacao nao elegivel: {eligibility.reason}")

        try:
            with transaction(self.repos.conn):
                existing = self.repos.memory_states.get(competency_id)
                if existing is not None:
                    card = fsrs.Card.from_json(existing.fsrs_card_json)
                    state_id = existing.id
                else:
                    card = fsrs.Card()
                    state_id = new_id()

                fsrs_rating = fsrs.Rating(eligibility.rating)
                when = review_datetime or utc_now()
                new_card, review_log = self.scheduler.review_card(
                    card, fsrs_rating, review_datetime=when, review_duration=review_duration
                )

                new_state = self._state_from_card(competency_id, new_card, existing_id=state_id)
                self.repos.memory_states.upsert(new_state)

                log = MemoryReviewLog(
                    id=new_id(),
                    competency_id=competency_id,
                    memory_state_id=new_state.id,
                    review_datetime=to_utc_iso(review_log.review_datetime),
                    rating=int(review_log.rating),
                    fsrs_review_log_json=review_log.to_json(),
                    created_at=to_utc_iso(utc_now()),
                )
                self.repos.memory_review_logs.insert(log)

                observation = MemoryObservation(
                    id=new_id(),
                    competency_id=competency_id,
                    raw_interaction_id=raw_interaction_id,
                    evidence_assessment_id=evidence_assessment_id,
                    planned_recall=True,
                    interval_days=eligibility.interval_days,
                    rating=eligibility.rating,
                    created_at=to_utc_iso(utc_now()),
                )
                self.repos.memory_observations.insert(observation)

                return new_state
        except Exception as exc:  # noqa: BLE001 - fronteira externa deliberada
            return MemoryReviewError(message=str(exc))

    def is_recall_due(self, competency_id: str, now: datetime | None = None) -> bool:
        state = self.repos.memory_states.get(competency_id)
        if state is None or state.due_at is None:
            return False
        reference = now or utc_now()
        return parse_iso(state.due_at) <= reference

    def retrievability(self, competency_id: str, now: datetime | None = None) -> float | None:
        state = self.repos.memory_states.get(competency_id)
        if state is None:
            return None
        card = fsrs.Card.from_json(state.fsrs_card_json)
        return self.scheduler.get_card_retrievability(card, current_datetime=now or utc_now())

    def _state_from_card(self, competency_id: str, card: "fsrs.Card", existing_id: str) -> MemoryState:
        return MemoryState(
            id=existing_id,
            competency_id=competency_id,
            fsrs_card_json=card.to_json(),
            due_at=to_utc_iso(card.due),
            stability=card.stability,
            difficulty=card.difficulty,
            card_state=_STATE_MAP[card.state].value,
            last_review_at=to_utc_iso(card.last_review) if card.last_review else None,
            updated_at=to_utc_iso(utc_now()),
        )
