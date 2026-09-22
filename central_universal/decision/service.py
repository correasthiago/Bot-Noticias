"""Liga o Decisor puro (engine.py) ao banco: monta CompetencySnapshot a
partir de CompetencyState/MemoryState e grava o DecisionEvent resultante.
"""

from __future__ import annotations

from central_universal.decision.engine import (
    ActionOutcome,
    CompetencySnapshot,
    DimensionSnapshot,
    RoutingOutcome,
    choose_next_action,
    route_competency,
)
from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import DecisionEvent, RuleVersion
from central_universal.domain.enums import ALL_DIMENSIONS, Dimension
from central_universal.domain.ids import new_id
from central_universal.persistence.repositories import Repositories


class DecisionService:
    def __init__(self, repos: Repositories) -> None:
        self.repos = repos

    def build_snapshot(self, competency_id: str, memory_recall_due: bool = False) -> CompetencySnapshot:
        dims: dict = {}
        for dimension in ALL_DIMENSIONS:
            state = self.repos.competency_states.current(competency_id, dimension)
            if state is not None:
                dims[dimension] = DimensionSnapshot(
                    state=state.state,
                    possible_regression=state.possible_regression,
                    has_unresolved_contradiction=state.has_unresolved_contradiction,
                )

        prereq_ids = self.repos.prerequisites.prerequisites_of(competency_id)
        unmet: list[str] = []
        for prereq_id in prereq_ids:
            # Um pre-requisito e considerado satisfeito quando accuracy (o
            # minimo de uso correto e independente) ja esta pelo menos
            # demonstrada. Nao usamos "consolidated" aqui para nao travar o
            # aprendiz em uma exigencia de retencao longitudinal so para
            # liberar o proximo topico.
            acc = self.repos.competency_states.current(prereq_id, Dimension.ACCURACY)
            if acc is None or acc.state.value in ("not_assessed", "insufficient_evidence", "acquiring"):
                unmet.append(prereq_id)

        return CompetencySnapshot(
            competency_id=competency_id,
            dimensions=dims,
            prerequisites_satisfied=not unmet,
            unmet_prerequisite_ids=tuple(unmet),
            memory_recall_due=memory_recall_due,
        )

    def preview_routing(self, competency_id: str, memory_recall_due: bool = False) -> RoutingOutcome:
        """Calcula o roteamento SEM gravar DecisionEvent. Usado por telas
        de leitura (ex.: tela inicial, mapa) para nao poluir o log de
        decisoes so porque a pagina foi visualizada."""

        snapshot = self.build_snapshot(competency_id, memory_recall_due=memory_recall_due)
        return route_competency(snapshot)

    def decide(
        self,
        *,
        competency_id: str,
        learner_id: str,
        rule_version: RuleVersion,
        session_id: str | None = None,
        memory_recall_due: bool = False,
    ) -> tuple[RoutingOutcome, ActionOutcome | None]:
        snapshot = self.build_snapshot(competency_id, memory_recall_due=memory_recall_due)
        routing = route_competency(snapshot)

        self._record(
            session_id=session_id,
            learner_id=learner_id,
            competency_id=competency_id,
            routing=routing.routing.value,
            decision_type=None,
            rule_applied=routing.rule_applied,
            justification=routing.justification,
            rule_version_id=rule_version.id,
        )

        action: ActionOutcome | None = None
        if routing.routing.value != "SKIP":
            action = choose_next_action(snapshot)
            self._record(
                session_id=session_id,
                learner_id=learner_id,
                competency_id=competency_id,
                routing=routing.routing.value,
                decision_type=action.decision_type.value,
                rule_applied=action.rule_applied,
                justification=action.justification,
                rule_version_id=rule_version.id,
            )

        return routing, action

    def _record(
        self,
        *,
        session_id: str | None,
        learner_id: str,
        competency_id: str | None,
        routing: str,
        decision_type: str | None,
        rule_applied: str,
        justification: str,
        rule_version_id: str,
    ) -> DecisionEvent:
        event = DecisionEvent(
            id=new_id(),
            session_id=session_id,
            learner_id=learner_id,
            competency_id=competency_id,
            routing=routing,
            decision_type=decision_type,
            rule_applied=rule_applied,
            justification=justification,
            rule_version_id=rule_version_id,
            created_at=utc_now_iso(),
        )
        self.repos.decision_events.insert(event)
        return event
