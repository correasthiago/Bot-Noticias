"""Decisor V0: codigo puro, deterministico e auditavel (Principio 9).

Nao ha chamada de LLM em nenhum caminho deste modulo. Toda decisao e uma
funcao pura de um `CompetencySnapshot` para um resultado com
`rule_applied` (um slug estavel, usado em testes) e `justification` (texto
legivel, guardado em DecisionEvent - Principio 20: "o sistema deve
explicar por que tomou uma decisao").
"""

from __future__ import annotations

from dataclasses import dataclass, field

from central_universal.domain.enums import (
    ALL_DIMENSIONS,
    CompetencyDimensionState,
    DecisionType,
    Dimension,
    RoutingDecision,
)

_WEAK_STATES = (
    CompetencyDimensionState.NOT_ASSESSED,
    CompetencyDimensionState.INSUFFICIENT_EVIDENCE,
)
_NOT_YET_INDEPENDENT = _WEAK_STATES + (CompetencyDimensionState.ACQUIRING,)


@dataclass(frozen=True)
class DimensionSnapshot:
    state: CompetencyDimensionState = CompetencyDimensionState.NOT_ASSESSED
    possible_regression: bool = False
    has_unresolved_contradiction: bool = False


@dataclass(frozen=True)
class CompetencySnapshot:
    competency_id: str
    dimensions: dict[Dimension, DimensionSnapshot] = field(default_factory=dict)
    prerequisites_satisfied: bool = True
    unmet_prerequisite_ids: tuple[str, ...] = ()
    memory_recall_due: bool = False

    def state_of(self, dimension: Dimension) -> DimensionSnapshot:
        return self.dimensions.get(dimension, DimensionSnapshot())


@dataclass(frozen=True)
class DecisionOutcome:
    rule_applied: str
    justification: str


@dataclass(frozen=True)
class RoutingOutcome(DecisionOutcome):
    routing: RoutingDecision


@dataclass(frozen=True)
class ActionOutcome(DecisionOutcome):
    decision_type: DecisionType


def route_competency(snapshot: CompetencySnapshot) -> RoutingOutcome:
    """Secao 18: SKIP / VALIDATE / STUDY.

    Nunca decide STUDY apenas pela posicao da competencia no grafo - a
    unica excecao pedagogicamente legitima a essa regra e quando os
    PRE-REQUISITOS realmente nao tem evidencia suficiente (nao e posicao,
    e fato constatado).
    """

    if not snapshot.prerequisites_satisfied:
        return RoutingOutcome(
            routing=RoutingDecision.STUDY,
            rule_applied="prerequisites_pending",
            justification=(
                "Pre-requisito(s) ainda sem evidencia suficiente "
                f"({', '.join(snapshot.unmet_prerequisite_ids) or 'nao especificado'}): "
                "e necessario trata-los antes de avancar com seguranca."
            ),
        )

    if any(snapshot.state_of(d).has_unresolved_contradiction for d in ALL_DIMENSIONS):
        return RoutingOutcome(
            routing=RoutingDecision.VALIDATE,
            rule_applied="contradictory_evidence",
            justification=(
                "Existe evidencia contraditoria ainda nao resolvida para esta "
                "competencia: uma sondagem discreta decide sem reensinar do zero."
            ),
        )

    if any(snapshot.state_of(d).possible_regression for d in ALL_DIMENSIONS):
        return RoutingOutcome(
            routing=RoutingDecision.VALIDATE,
            rule_applied="suspected_regression",
            justification=(
                "Uma possivel regressao foi sinalizada por evidencia recente: "
                "e preciso confirmar antes de rebaixar formalmente ou de pular."
            ),
        )

    core_dimensions = (Dimension.COMPREHENSION, Dimension.RETRIEVAL, Dimension.ACCURACY)
    if any(snapshot.state_of(d).state in _WEAK_STATES for d in core_dimensions):
        return RoutingOutcome(
            routing=RoutingDecision.STUDY,
            rule_applied="core_gap",
            justification=(
                "Uma ou mais dimensoes fundamentais (compreensao, recuperacao ou "
                "precisao) ainda nao tem evidencia suficiente: ha lacuna real, "
                "nao apenas posicao no curso."
            ),
        )

    progressing_dimensions = (
        Dimension.COMPREHENSION,
        Dimension.RETRIEVAL,
        Dimension.ACCURACY,
        Dimension.AUTOMATICITY,
        Dimension.TRANSFER,
    )
    if any(
        snapshot.state_of(d).state == CompetencyDimensionState.ACQUIRING
        for d in progressing_dimensions
    ):
        return RoutingOutcome(
            routing=RoutingDecision.STUDY,
            rule_applied="actively_acquiring",
            justification=(
                "A competencia esta em fase ativa de aquisicao em pelo menos "
                "uma dimensao: continuar estudando e a acao correta."
            ),
        )

    retention = snapshot.state_of(Dimension.RETENTION)
    if retention.state in _NOT_YET_INDEPENDENT or snapshot.memory_recall_due:
        return RoutingOutcome(
            routing=RoutingDecision.VALIDATE,
            rule_applied="retention_pending",
            justification=(
                "As dimensoes de dominio ativo estao fortes, mas a retencao "
                "longitudinal ainda nao foi confirmada (ou uma recuperacao "
                "espacada esta agendada agora): validar em vez de reensinar "
                "ou pular cegamente."
            ),
        )

    return RoutingOutcome(
        routing=RoutingDecision.SKIP,
        rule_applied="strong_and_current",
        justification=(
            "Todas as dimensoes estao demonstradas/consolidadas, sem contradicao "
            "ou regressao pendente, e a retencao ja foi confirmada com evidencia "
            "longitudinal: seguro pular esta competencia por ora."
        ),
    )


def choose_next_action(snapshot: CompetencySnapshot) -> ActionOutcome:
    """Secao 17: qual acao pedagogica concreta aplicar a esta competencia
    quando a resposta de `route_competency` for STUDY ou VALIDATE."""

    if any(snapshot.state_of(d).possible_regression for d in ALL_DIMENSIONS):
        return ActionOutcome(
            decision_type=DecisionType.TARGETED_REGRESSION_CHECK,
            rule_applied="suspected_regression",
            justification=(
                "Regressao suspeita: provocar evidencia direcionada antes de "
                "qualquer rebaixamento formal do estado (Principio 12)."
            ),
        )

    if any(snapshot.state_of(d).has_unresolved_contradiction for d in ALL_DIMENSIONS):
        return ActionOutcome(
            decision_type=DecisionType.DISCRETE_VALIDATION,
            rule_applied="contradictory_evidence",
            justification=(
                "Evidencias contraditorias coexistem para esta competencia: uma "
                "validacao discreta resolve a incerteza sem reensinar do zero "
                "(Principio 13)."
            ),
        )

    comprehension = snapshot.state_of(Dimension.COMPREHENSION).state
    if comprehension in _WEAK_STATES:
        return ActionOutcome(
            decision_type=DecisionType.MINIMAL_EXPLANATION,
            rule_applied="insufficient_comprehension",
            justification="Compreensao insuficiente: oferecer input/explicacao minima antes de cobrar recuperacao.",
        )

    retrieval = snapshot.state_of(Dimension.RETRIEVAL).state
    if retrieval in _NOT_YET_INDEPENDENT:
        return ActionOutcome(
            decision_type=DecisionType.GUIDED_RETRIEVAL,
            rule_applied="comprehension_without_retrieval",
            justification="Compreende mas ainda nao recupera de forma independente: praticar retrieval guiado.",
        )

    accuracy = snapshot.state_of(Dimension.ACCURACY).state
    if accuracy in _NOT_YET_INDEPENDENT:
        return ActionOutcome(
            decision_type=DecisionType.CONTRASTIVE_PRACTICE,
            rule_applied="retrieval_without_accuracy",
            justification="Recupera mas a precisao ainda e insuficiente: pratica contrastiva com feedback.",
        )

    automaticity = snapshot.state_of(Dimension.AUTOMATICITY).state
    if automaticity in _NOT_YET_INDEPENDENT:
        return ActionOutcome(
            decision_type=DecisionType.CONTEXTUAL_PRODUCTION,
            rule_applied="accuracy_without_automaticity",
            justification="Precisao adequada mas automaticidade ainda insuficiente: producao contextual repetida.",
        )

    transfer = snapshot.state_of(Dimension.TRANSFER).state
    if transfer in _NOT_YET_INDEPENDENT:
        return ActionOutcome(
            decision_type=DecisionType.NOVEL_CONTEXT_TRANSFER,
            rule_applied="strong_without_transfer",
            justification="Competencia forte no core mas transferencia ainda ausente: expor a um contexto novo.",
        )

    retention = snapshot.state_of(Dimension.RETENTION).state
    if retention != CompetencyDimensionState.CONSOLIDATED:
        return ActionOutcome(
            decision_type=DecisionType.SCHEDULE_RECALL,
            rule_applied="transfer_without_retention",
            justification=(
                "Transferencia demonstrada mas retencao longitudinal ainda "
                "pendente: retirar do foco ativo e agendar recuperacao espacada."
            ),
        )

    return ActionOutcome(
        decision_type=DecisionType.EXIT_ACTIVE_FOCUS,
        rule_applied="fully_demonstrated",
        justification=(
            "Todas as dimensoes estao suficientemente demonstradas, incluindo "
            "retencao longitudinal: sair do foco ativo e permanecer apenas no "
            "scheduler de longo prazo."
        ),
    )
