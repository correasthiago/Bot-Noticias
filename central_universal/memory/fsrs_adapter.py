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
existe mais). Nessa primeira correcao ela passou a vir da
`EvidenceAssessment.classification`/`confidence` da propria avaliacao de
recuperacao.

Terceira auditoria pos-entrega (Secao 2): usar `confidence` para decidir
Easy/Good/Hard era, na pratica, o MESMO erro de categoria de novo -
`confidence` mede o quanto o AVALIADOR confia no proprio julgamento de
classificacao (correto/incorreto), nunca o quao FACIL foi para o
APRENDIZ recuperar a informacao. Uma avaliacao positiva de alta confianca
virava "Easy" automaticamente mesmo quando a resposta so saiu certa com
uma pista explicita. `derive_recall_rating` agora deriva Easy/Good/Hard
EXCLUSIVAMENTE de dois sinais OBSERVAVEIS do processo de recuperacao da
propria tentativa - `help_level` (quanto suporte foi dado ANTES da
resposta) e `production_result` (COMO a resposta correta foi alcancada) -
nunca de `confidence`. `confidence` continua sendo usada, mas SO como
filtro de elegibilidade (`recall_min_confidence`), nunca como sinal de
facilidade.

A mesma auditoria tambem fechou uma lacuna na PRIMEIRA revisao de uma
competencia: como nao existe `memory_state.last_review_at` para comparar,
o intervalo minimo simplesmente nao era verificado. Agora ele e verificado
contra a PRIMEIRA evidencia ja registrada para a competencia (proxy
observavel de "quando o aprendiz comecou a aprender isto") via
`first_review_min_interval_since_learning_seconds`.

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
from central_universal.domain.enums import (
    Dimension,
    EvidenceRelation,
    EvidenceType,
    HelpLevel,
    MemoryCardState,
    ProductionResult,
)
from central_universal.domain.ids import new_id
from central_universal.evidence.aggregation import AggregationConfig
from central_universal.persistence.db import transaction
from central_universal.persistence.repositories import Repositories

_STATE_MAP = {
    fsrs.State.Learning: MemoryCardState.LEARNING,
    fsrs.State.Review: MemoryCardState.REVIEW,
    fsrs.State.Relearning: MemoryCardState.RELEARNING,
}


def derive_recall_rating(
    assessment: EvidenceAssessment,
    *,
    help_level: HelpLevel,
    production_result: ProductionResult,
) -> int | None:
    """Deriva a nota FSRS (1-4) de SINAIS OBSERVAVEIS do processo de
    recuperacao da propria tentativa - NUNCA da confianca do avaliador
    (Secao 2 da terceira auditoria pos-entrega).

    `confidence` mede o quanto o avaliador confia no proprio julgamento de
    CLASSIFICACAO (a resposta estava certa ou errada?), nunca o quao facil
    foi para o aprendiz RECUPERAR a informacao - uma avaliacao positiva de
    alta confianca nao pode virar "Easy" so por isso (era exatamente o
    erro que a versao anterior desta funcao cometia). `confidence` continua
    relevante, mas so como filtro de ELEGIBILIDADE em
    `evaluate_recall_eligibility` (`recall_min_confidence`).

    Uma avaliacao NEGATIVE sempre vira "Again" (o FSRS precisa desse sinal
    para modelar esquecimento). Uma avaliacao POSITIVE deriva Easy/Good/Hard
    de dois sinais diretamente observaveis sobre COMO a resposta foi
    produzida:

    - `help_level`: quanto suporte foi dado ANTES da resposta (A0 nenhum,
      A1 contexto conduz a resposta, A2 pista explicita, A3 modelo/resposta
      parcial ou total fornecida).
    - `production_result`: como a resposta correta foi alcancada
      (espontanea, com autocorrecao, apos pista, apos correcao externa).

    Politica - CONSERVADORA quando os sinais nao apontam claramente para
    "facil" (nunca assume Easy por omissao ou por ambiguidade):

    - **Easy**: SEM nenhum suporte (`help_level=A0`) E resposta
      espontaneamente correta na primeira tentativa
      (`production_result=SPONTANEOUS_CORRECT`). O UNICO caso que conta
      como recuperacao genuinamente sem esforco - os dois sinais tem que
      concordar.
    - **Hard**: houve suporte explicito ANTES da resposta (`help_level`
      em A2/A3) OU a resposta so veio certa depois de uma pista/correcao
      (`production_result` em CORRECT_AFTER_HINT/
      CORRECT_AFTER_EXTERNAL_CORRECTION) - recuperacao aconteceu, mas com
      esforco/apoio real. Isto vale mesmo se o OUTRO sinal parecer
      favoravel (ex.: `help_level=A0` mas `production_result` registrado
      como apos correcao e uma inconsistencia de dados - tratada pelo
      caminho MENOS otimista, nunca o mais).
    - **Good**: qualquer outro caso POSITIVE que nao se encaixe nos dois
      acima (ex.: espontanea mas com autocorrecao propria, ou contexto
      conduziu a resposta sem pista explicita) - a politica conservadora
      para sinal ambiguo: nunca "Easy", mas tambem nao pune como "Hard"
      quando houve sucesso razoavelmente independente.

    Contraditoria/inconclusiva nunca chegam aqui - ja sao filtradas por
    `evaluate_recall_eligibility`.
    """

    if assessment.classification == EvidenceType.NEGATIVE:
        return int(fsrs.Rating.Again)
    if assessment.classification != EvidenceType.POSITIVE:
        return None

    needed_explicit_support = help_level in (HelpLevel.A2, HelpLevel.A3)
    needed_correction = production_result in (
        ProductionResult.CORRECT_AFTER_HINT,
        ProductionResult.CORRECT_AFTER_EXTERNAL_CORRECTION,
    )
    if needed_explicit_support or needed_correction:
        return int(fsrs.Rating.Hard)

    if help_level == HelpLevel.A0 and production_result == ProductionResult.SPONTANEOUS_CORRECT:
        return int(fsrs.Rating.Easy)

    return int(fsrs.Rating.Good)


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
    help_level: HelpLevel | None = None,
    production_result: ProductionResult | None = None,
    memory_state: MemoryState | None,
    first_evidence_at: str | None = None,
    config: AggregationConfig | None = None,
    now: datetime | None = None,
) -> MemoryObservationEligibility:
    """Decide se esta interacao pode gerar um MemoryObservation.

    Cobre explicitamente a Secao 2 do pacote de correcao v0.2/v0.2.1/
    terceira auditoria: nao revisa quando o avaliador falhou (assessment
    is None), a evidencia nao e da dimensao RETENTION, a relacao nao e
    `target` (evidencia incidental/mere_presence NUNCA conta), a
    classificacao e inconclusiva, a confianca e baixa demais, ha causa
    alternativa pendente, a atividade nao e uma recuperacao planejada, os
    sinais observaveis (`help_level`/`production_result`) da propria
    tentativa estao ausentes, ou o intervalo desde a ultima revisao (ou,
    na PRIMEIRA revisao, desde a primeira evidencia/aprendizagem) e menor
    que o minimo definido na regra.
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

    if help_level is None or production_result is None:
        return MemoryObservationEligibility(
            False,
            "sinais observaveis da tentativa (help_level/production_result) ausentes - "
            "impossivel derivar uma nota de recuperacao com a politica conservadora vigente",
        )

    rating = derive_recall_rating(assessment, help_level=help_level, production_result=production_result)
    if rating is None:
        return MemoryObservationEligibility(False, "classificacao da avaliacao nao produz uma nota de recuperacao valida")

    reference = now or utc_now()
    interval_seconds: float | None = None
    if memory_state is not None and memory_state.last_review_at:
        interval_seconds = (reference - parse_iso(memory_state.last_review_at)).total_seconds()
        if interval_seconds < config.recall_min_interval_seconds:
            return MemoryObservationEligibility(
                False,
                f"intervalo de {interval_seconds:.1f}s desde a ultima revisao e menor que o minimo "
                f"definido pela regra ({config.recall_min_interval_seconds:.1f}s)",
            )
    elif first_evidence_at is not None:
        # PRIMEIRA revisao desta competencia (sem memory_state ainda): o
        # intervalo e verificado desde a PRIMEIRA evidencia registrada -
        # nunca deixado sem checagem so porque nao ha uma revisao anterior
        # para comparar (Secao 2 da terceira auditoria pos-entrega).
        interval_seconds = (reference - parse_iso(first_evidence_at)).total_seconds()
        if interval_seconds < config.first_review_min_interval_since_learning_seconds:
            return MemoryObservationEligibility(
                False,
                f"intervalo de {interval_seconds:.1f}s desde a primeira evidencia (aprendizagem) e menor "
                f"que o minimo exigido pela regra para a primeira revisao "
                f"({config.first_review_min_interval_since_learning_seconds:.1f}s)",
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
