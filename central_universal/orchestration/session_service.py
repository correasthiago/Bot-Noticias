"""Orquestra o ciclo completo de uma sessao de aprendizagem (Secao 1 e 4):

diagnostico -> competencia -> atividade -> interacao -> evidencia bruta ->
avaliacao -> estado da competencia -> memoria -> decisao -> proxima
atividade -> persistencia longitudinal.

Este e o unico modulo que conhece TODOS os outros servicos de dominio ao
mesmo tempo. Nenhuma regra pedagogica nova e criada aqui - ele so chama,
na ordem certa, os modulos que ja a implementam.
"""

from __future__ import annotations

from dataclasses import dataclass

from central_universal.decision.engine import ActionOutcome, RoutingOutcome
from central_universal.decision.service import DecisionService
from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import (
    Activity,
    CompetencyState,
    LearningSession,
    ProviderEvent,
    RawInteraction,
    RuleVersion,
)
from central_universal.domain.enums import (
    DecisionType,
    HelpLevel,
    ProductionResult,
    RoutingDecision,
    SessionStatus,
)
from central_universal.domain.ids import new_id
from central_universal.evaluator.contract import EvaluatorInput, EvaluatorOutput
from central_universal.evaluator.service import EvaluatorService
from central_universal.evidence.service import EvidenceService
from central_universal.memory.fsrs_adapter import (
    MemoryAdapter,
    MemoryReviewError,
    rating_from_production_result,
)
from central_universal.persistence.db import transaction
from central_universal.persistence.repositories import Repositories
from central_universal.providers.base import Provider
from central_universal.tutor.contract import TutorInput, TutorOutput
from central_universal.tutor.service import TutorService

_SUPPORT_LEVEL_BY_ACTION = {
    DecisionType.MINIMAL_EXPLANATION: HelpLevel.A3,
    DecisionType.GUIDED_RETRIEVAL: HelpLevel.A2,
    DecisionType.CONTRASTIVE_PRACTICE: HelpLevel.A1,
    DecisionType.CONTEXTUAL_PRODUCTION: HelpLevel.A0,
    DecisionType.NOVEL_CONTEXT_TRANSFER: HelpLevel.A0,
    DecisionType.SCHEDULE_RECALL: HelpLevel.A0,
    DecisionType.DISCRETE_VALIDATION: HelpLevel.A0,
    DecisionType.TARGETED_REGRESSION_CHECK: HelpLevel.A1,
    DecisionType.EXIT_ACTIVE_FOCUS: HelpLevel.A0,
}


@dataclass
class InteractionOutcome:
    raw_interaction: RawInteraction
    created_now: bool
    evaluator_output: EvaluatorOutput | None
    competency_states: list[CompetencyState]
    memory_result: object | None
    evaluator_provider_event: ProviderEvent | None = None
    next_routing: RoutingOutcome | None = None
    next_action: ActionOutcome | None = None


class SessionOrchestrator:
    def __init__(self, repos: Repositories, provider: Provider, rule_version: RuleVersion) -> None:
        self.repos = repos
        self.provider = provider
        self.rule_version = rule_version
        self.evidence_service = EvidenceService(repos)
        self.decision_service = DecisionService(repos)
        self.memory_adapter = MemoryAdapter(repos)
        self.tutor_service = TutorService(repos, provider)
        self.evaluator_service = EvaluatorService(repos, provider)

    # ---- ciclo de vida da sessao -----------------------------------

    def start_session(self, learner_id: str) -> LearningSession:
        session = LearningSession(
            id=new_id(), learner_id=learner_id, started_at=utc_now_iso(), status=SessionStatus.ACTIVE
        )
        self.repos.sessions.insert(session)
        return session

    def end_session(self, session_id: str) -> None:
        self.repos.sessions.end_session(session_id, utc_now_iso())

    # ---- escolha de foco (Secao 18: SKIP/VALIDATE/STUDY) -----------

    def choose_focus_competency(
        self, learner_id: str
    ) -> tuple[str, RoutingOutcome, ActionOutcome | None] | None:
        """Percorre as competencias do grafo e devolve a primeira que NAO
        pode ser pulada. Retorna None se tudo puder ser pulado agora."""

        for competency in self.repos.competencies.list_all():
            recall_due = self.memory_adapter.is_recall_due(competency.id)
            preview = self.decision_service.preview_routing(competency.id, memory_recall_due=recall_due)
            if preview.routing != RoutingDecision.SKIP:
                # So gravamos DecisionEvent para a competencia efetivamente
                # escolhida, nao para cada uma varrida na busca.
                routing, action = self.decision_service.decide(
                    competency_id=competency.id,
                    learner_id=learner_id,
                    rule_version=self.rule_version,
                    memory_recall_due=recall_due,
                )
                return competency.id, routing, action
        return None

    # ---- atividade ----------------------------------------------------

    def start_activity(
        self, *, session_id: str, competency_id: str, action: ActionOutcome | None
    ) -> tuple[Activity, TutorOutput | None]:
        competency = self.repos.competencies.get(competency_id)
        if competency is None:
            raise ValueError(f"competencia desconhecida: {competency_id}")

        support_level = _SUPPORT_LEVEL_BY_ACTION.get(
            action.decision_type if action else None, HelpLevel.A0
        )
        objective = action.justification if action else "Revisao geral de longo prazo."
        tutor_input = TutorInput(
            objective=objective,
            target_competency_ids=(competency_id,),
            context=f"Competencia: {competency.name} ({competency.code})",
            allowed_support_level=support_level,
            prompt_seed=f"Pratique: {competency.name}",
        )
        tutor_output, _provider_event = self.tutor_service.run(tutor_input)

        activity = Activity(
            id=new_id(),
            session_id=session_id,
            competency_targets=[competency_id],
            activity_type=(action.decision_type.value if action else "longitudinal_review"),
            prompt=(tutor_output.utterance if tutor_output else competency.name),
            support_level=support_level,
            created_at=utc_now_iso(),
            tutor_provider_event_id=(tutor_output.provider_event_id if tutor_output else None),
        )
        # Secao 25: "Se Tutor falhar, sessao permanece recuperavel" - mesmo
        # sem tutor_output a atividade e criada (com prompt de fallback),
        # entao o fluxo pode continuar/ser tentado de novo.
        self.repos.activities.insert(activity)
        return activity, tutor_output

    # ---- interacao / evidencia / memoria / decisao -----------------

    def submit_interaction(
        self,
        *,
        activity_id: str,
        session_id: str,
        idempotency_key: str,
        learner_input: str,
        help_level: HelpLevel,
        production_result: ProductionResult,
        tutor_output_text: str = "",
    ) -> InteractionOutcome:
        activity = self.repos.activities.get(activity_id)
        if activity is None:
            raise ValueError(f"atividade desconhecida: {activity_id}")

        # Secao 11: interacoes da mesma atividade compartilham cluster de
        # evidencia, para nao contar respostas quase identicas como provas
        # independentes.
        cluster_id = activity_id

        raw_interaction, created_now = self.evidence_service.record_interaction(
            activity_id=activity_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            learner_input=learner_input,
            tutor_output=tutor_output_text,
            help_level=help_level,
            production_result=production_result,
            evidence_cluster_id=cluster_id,
        )

        if not created_now:
            # Secao 24 / T8: submissao repetida nao duplica nada. Devolvemos
            # a interacao ja existente sem reavaliar.
            return InteractionOutcome(
                raw_interaction=raw_interaction,
                created_now=False,
                evaluator_output=None,
                competency_states=[],
                memory_result=None,
            )

        evaluator_input = EvaluatorInput(
            raw_interaction_id=raw_interaction.id,
            learner_input=learner_input,
            tutor_output=tutor_output_text,
            candidate_competency_ids=tuple(activity.competency_targets),
            help_level=help_level,
            production_result=production_result,
            context=activity.prompt,
            rule_version=self.rule_version.version,
        )
        evaluator_output, eval_provider_event = self.evaluator_service.run(
            evaluator_input, set(activity.competency_targets)
        )

        competency_states: list[CompetencyState] = []
        if evaluator_output is not None:
            # Secao 25 / T9: todo o lote de evidencia desta submissao e
            # atomico - ou entra inteiro, ou nao entra nada.
            with transaction(self.repos.conn):
                competency_states = self.evidence_service.record_evaluation(
                    raw_interaction=raw_interaction,
                    evaluator_output=evaluator_output,
                    rule_version=self.rule_version,
                    evaluator_provider_event_id=eval_provider_event.id,
                )
        # Se evaluator_output for None (avaliador falhou ou devolveu algo
        # invalido), a RawInteraction ja gravada permanece como esta e a
        # avaliacao fica pendente (Secao 25) - nada mais e feito aqui.

        memory_result: object | None = None
        rating = rating_from_production_result(production_result)
        if rating is not None and activity.competency_targets:
            # Falha do FSRS nao apaga evidencia (T14): review() ja isola a
            # falha internamente e retorna um MemoryReviewError em vez de
            # levantar excecao ate aqui.
            memory_result = self.memory_adapter.review(activity.competency_targets[0], rating=rating)

        next_routing: RoutingOutcome | None = None
        next_action: ActionOutcome | None = None
        if activity.competency_targets:
            session = self.repos.sessions.get(session_id)
            target_id = activity.competency_targets[0]
            recall_due = self.memory_adapter.is_recall_due(target_id)
            next_routing, next_action = self.decision_service.decide(
                competency_id=target_id,
                learner_id=session.learner_id if session else "",
                rule_version=self.rule_version,
                session_id=session_id,
                memory_recall_due=recall_due,
            )

        return InteractionOutcome(
            raw_interaction=raw_interaction,
            created_now=True,
            evaluator_output=evaluator_output,
            competency_states=competency_states,
            memory_result=memory_result,
            evaluator_provider_event=eval_provider_event,
            next_routing=next_routing,
            next_action=next_action,
        )
