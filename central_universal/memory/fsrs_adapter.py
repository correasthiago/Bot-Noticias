"""Adapter isolando Py-FSRS do resto do sistema (Secao 13).

Nenhum outro modulo deve importar o pacote `fsrs` diretamente - so este
arquivo conhece `fsrs.Card`, `fsrs.Scheduler`, `fsrs.Rating`, `fsrs.State`
e `fsrs.ReviewLog`.

v0.2 (pacote de correcao pos Red Team): o `ProductionResult` de uma
interacao NUNCA mais dispara uma revisao FSRS sozinho. O UNICO gatilho
legitimo e um `MemoryObservation` explicito (Secao 1 do pacote de
correcao), emitido somente quando `evaluate_recall_eligibility` confirma
que:

- a atividade foi PLANEJADA como recuperacao (`Activity.is_planned_recall`);
- ocorreu apos um intervalo relevante desde a ultima revisao (nao e um
  repique no mesmo instante);
- recebeu uma avaliacao VALIDA e CONCLUSIVA (nem inconclusive, nem com
  causa alternativa pendente, nem `mere_presence`).

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
from central_universal.domain.enums import EvidenceRelation, MemoryCardState, ProductionResult
from central_universal.domain.ids import new_id
from central_universal.persistence.db import transaction
from central_universal.persistence.repositories import Repositories

_STATE_MAP = {
    fsrs.State.Learning: MemoryCardState.LEARNING,
    fsrs.State.Review: MemoryCardState.REVIEW,
    fsrs.State.Relearning: MemoryCardState.RELEARNING,
}

# Mapeamento provisorio e documentado (DECISIONS.md) de resultado de
# producao para a nota de 1-4 que o FSRS espera - usado SO depois que a
# elegibilidade ja foi confirmada, nunca como gatilho por si so.
_RATING_MAP = {
    ProductionResult.SPONTANEOUS_CORRECT: fsrs.Rating.Easy,
    ProductionResult.SPONTANEOUS_SELF_CORRECTION: fsrs.Rating.Good,
    ProductionResult.CORRECT_AFTER_HINT: fsrs.Rating.Hard,
    ProductionResult.CORRECT_AFTER_EXTERNAL_CORRECTION: fsrs.Rating.Again,
    ProductionResult.INCORRECT: fsrs.Rating.Again,
    # INCONCLUSIVE nao aparece aqui de proposito: incerteza e um estado
    # legitimo (Principio 14) e nao deve forcar uma nota de memoria.
}


def rating_from_production_result(result: ProductionResult) -> int | None:
    rating = _RATING_MAP.get(result)
    return int(rating) if rating is not None else None


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
    assessment: EvidenceAssessment | None,
    production_result: ProductionResult,
    memory_state: MemoryState | None,
    now: datetime | None = None,
) -> MemoryObservationEligibility:
    """Decide se esta interacao pode gerar um MemoryObservation.

    Cobre explicitamente a Secao 2 do pacote de correcao v0.2: nao revisa
    quando o avaliador falhou (assessment is None), o payload foi
    invalido (idem), a classificacao e inconclusiva, a evidencia e
    mere_presence, a atividade nao e uma recuperacao planejada, ou ha
    causa alternativa nao resolvida.
    """

    if not activity.is_planned_recall:
        return MemoryObservationEligibility(False, "atividade nao foi planejada como recuperacao agendada")

    if evidence_relation == EvidenceRelation.MERE_PRESENCE:
        return MemoryObservationEligibility(False, "evidencia e mere_presence, nao uma tentativa de recuperacao valida")

    if assessment is None:
        return MemoryObservationEligibility(False, "sem avaliacao valida associada (avaliador falhou ou payload invalido)")

    if assessment.inconclusive:
        return MemoryObservationEligibility(False, "avaliacao inconclusiva")

    if assessment.alternative_cause:
        return MemoryObservationEligibility(False, "causa alternativa pendente nao resolvida")

    rating = rating_from_production_result(production_result)
    if rating is None:
        return MemoryObservationEligibility(False, "producao inconclusiva nao produz nota de memoria")

    interval_days: float | None = None
    if memory_state is not None and memory_state.last_review_at:
        reference = now or utc_now()
        interval_days = (reference - parse_iso(memory_state.last_review_at)).total_seconds() / 86400.0
        if interval_days <= 0:
            return MemoryObservationEligibility(
                False, "intervalo desde a ultima revisao nao e relevante (mesmo instante ou negativo)"
            )

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
