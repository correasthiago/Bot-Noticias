from __future__ import annotations

from central_universal.decision.engine import (
    CompetencySnapshot,
    DimensionSnapshot,
    choose_next_action,
    route_competency,
)
from central_universal.domain.enums import CompetencyDimensionState as S
from central_universal.domain.enums import DecisionType, Dimension as D
from central_universal.domain.enums import RoutingDecision as R


def snap(**dims) -> CompetencySnapshot:
    return CompetencySnapshot(competency_id="c1", dimensions=dims)


def test_rule1_insufficient_comprehension():
    outcome = choose_next_action(snap(comprehension=DimensionSnapshot(S.NOT_ASSESSED)))
    assert outcome.decision_type == DecisionType.MINIMAL_EXPLANATION


def test_rule2_comprehension_without_retrieval():
    outcome = choose_next_action(snap(
        comprehension=DimensionSnapshot(S.DEMONSTRATED),
        retrieval=DimensionSnapshot(S.ACQUIRING),
    ))
    assert outcome.decision_type == DecisionType.GUIDED_RETRIEVAL


def test_rule3_retrieval_without_accuracy():
    outcome = choose_next_action(snap(
        comprehension=DimensionSnapshot(S.DEMONSTRATED),
        retrieval=DimensionSnapshot(S.DEMONSTRATED),
        accuracy=DimensionSnapshot(S.ACQUIRING),
    ))
    assert outcome.decision_type == DecisionType.CONTRASTIVE_PRACTICE


def test_rule4_accuracy_without_automaticity():
    outcome = choose_next_action(snap(
        comprehension=DimensionSnapshot(S.DEMONSTRATED),
        retrieval=DimensionSnapshot(S.DEMONSTRATED),
        accuracy=DimensionSnapshot(S.DEMONSTRATED),
        automaticity=DimensionSnapshot(S.ACQUIRING),
    ))
    assert outcome.decision_type == DecisionType.CONTEXTUAL_PRODUCTION


def test_rule5_strong_without_transfer():
    outcome = choose_next_action(snap(
        comprehension=DimensionSnapshot(S.CONSOLIDATED),
        retrieval=DimensionSnapshot(S.CONSOLIDATED),
        accuracy=DimensionSnapshot(S.CONSOLIDATED),
        automaticity=DimensionSnapshot(S.DEMONSTRATED),
        transfer=DimensionSnapshot(S.ACQUIRING),
    ))
    assert outcome.decision_type == DecisionType.NOVEL_CONTEXT_TRANSFER


def test_rule6_transfer_without_retention():
    outcome = choose_next_action(snap(
        comprehension=DimensionSnapshot(S.CONSOLIDATED),
        retrieval=DimensionSnapshot(S.CONSOLIDATED),
        accuracy=DimensionSnapshot(S.CONSOLIDATED),
        automaticity=DimensionSnapshot(S.CONSOLIDATED),
        transfer=DimensionSnapshot(S.DEMONSTRATED),
        retention=DimensionSnapshot(S.ACQUIRING),
    ))
    assert outcome.decision_type == DecisionType.SCHEDULE_RECALL


def test_rule7_contradiction_triggers_discrete_validation_before_ladder():
    outcome = choose_next_action(snap(
        comprehension=DimensionSnapshot(S.NOT_ASSESSED),
        retrieval=DimensionSnapshot(S.DEMONSTRATED, has_unresolved_contradiction=True),
    ))
    assert outcome.decision_type == DecisionType.DISCRETE_VALIDATION


def test_rule8_regression_has_top_priority():
    outcome = choose_next_action(snap(
        comprehension=DimensionSnapshot(S.NOT_ASSESSED),
        accuracy=DimensionSnapshot(S.CONSOLIDATED, possible_regression=True),
    ))
    assert outcome.decision_type == DecisionType.TARGETED_REGRESSION_CHECK


def test_rule9_everything_demonstrated_exits_focus():
    outcome = choose_next_action(snap(
        comprehension=DimensionSnapshot(S.CONSOLIDATED),
        retrieval=DimensionSnapshot(S.CONSOLIDATED),
        accuracy=DimensionSnapshot(S.CONSOLIDATED),
        automaticity=DimensionSnapshot(S.CONSOLIDATED),
        transfer=DimensionSnapshot(S.CONSOLIDATED),
        retention=DimensionSnapshot(S.CONSOLIDATED),
    ))
    assert outcome.decision_type == DecisionType.EXIT_ACTIVE_FOCUS


def test_routing_skip_when_everything_strong_and_current():
    outcome = route_competency(snap(
        comprehension=DimensionSnapshot(S.CONSOLIDATED),
        retrieval=DimensionSnapshot(S.CONSOLIDATED),
        accuracy=DimensionSnapshot(S.CONSOLIDATED),
        automaticity=DimensionSnapshot(S.CONSOLIDATED),
        transfer=DimensionSnapshot(S.CONSOLIDATED),
        retention=DimensionSnapshot(S.CONSOLIDATED),
    ))
    assert outcome.routing == R.SKIP


def test_routing_study_on_core_gap_regardless_of_course_position():
    # Mesma competencia, sem nenhuma pista de "posicao no curso": so a
    # ausencia real de evidencia decide.
    outcome = route_competency(snap(comprehension=DimensionSnapshot(S.NOT_ASSESSED)))
    assert outcome.routing == R.STUDY
    assert outcome.rule_applied == "core_gap"


def test_routing_validate_on_pending_retention():
    outcome = route_competency(snap(
        comprehension=DimensionSnapshot(S.CONSOLIDATED),
        retrieval=DimensionSnapshot(S.CONSOLIDATED),
        accuracy=DimensionSnapshot(S.CONSOLIDATED),
        automaticity=DimensionSnapshot(S.CONSOLIDATED),
        transfer=DimensionSnapshot(S.CONSOLIDATED),
        retention=DimensionSnapshot(S.ACQUIRING),
    ))
    assert outcome.routing == R.VALIDATE


def test_routing_study_when_prerequisites_unmet_even_if_dimensions_look_fine():
    snapshot = CompetencySnapshot(
        competency_id="c1",
        dimensions={D.COMPREHENSION: DimensionSnapshot(S.CONSOLIDATED)},
        prerequisites_satisfied=False,
        unmet_prerequisite_ids=("simple_present",),
    )
    outcome = route_competency(snapshot)
    assert outcome.routing == R.STUDY
    assert outcome.rule_applied == "prerequisites_pending"


def test_every_decision_has_nonempty_justification():
    outcomes = [
        choose_next_action(snap()),
        route_competency(snap()),
    ]
    for outcome in outcomes:
        assert outcome.justification.strip() != ""
        assert outcome.rule_applied.strip() != ""
