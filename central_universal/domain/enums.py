"""Enumerações do dominio pedagogico.

Estes valores sao contrato estavel entre evidence, decision, memory e web.
Mudar um valor aqui e uma mudanca de regra pedagogica: deve vir acompanhada
de nova RuleVersion (Principio Constitucional 18).
"""

from __future__ import annotations

from enum import Enum


class Dimension(str, Enum):
    COMPREHENSION = "comprehension"
    RETRIEVAL = "retrieval"
    ACCURACY = "accuracy"
    AUTOMATICITY = "automaticity"
    TRANSFER = "transfer"
    RETENTION = "retention"


ALL_DIMENSIONS: tuple[Dimension, ...] = (
    Dimension.COMPREHENSION,
    Dimension.RETRIEVAL,
    Dimension.ACCURACY,
    Dimension.AUTOMATICITY,
    Dimension.TRANSFER,
    Dimension.RETENTION,
)


class CompetencyDimensionState(str, Enum):
    NOT_ASSESSED = "not_assessed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    ACQUIRING = "acquiring"
    DEMONSTRATED = "demonstrated"
    CONSOLIDATED = "consolidated"


class EvidenceType(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    CONTRADICTORY = "contradictory"
    INCONCLUSIVE = "inconclusive"


class EvidenceRelation(str, Enum):
    TARGET = "target"
    QUALIFIED_INCIDENTAL = "qualified_incidental"
    MERE_PRESENCE = "mere_presence"


class HelpLevel(str, Enum):
    A0 = "A0"  # espontaneo, sem ajuda
    A1 = "A1"  # contexto conduz a resposta, estrutura nao fornecida
    A2 = "A2"  # pista explicita
    A3 = "A3"  # modelo/resposta parcial ou totalmente fornecida


INDEPENDENT_HELP_LEVELS: tuple[HelpLevel, ...] = (HelpLevel.A0, HelpLevel.A1)
ASSISTED_HELP_LEVELS: tuple[HelpLevel, ...] = (HelpLevel.A2, HelpLevel.A3)


class ProductionResult(str, Enum):
    SPONTANEOUS_CORRECT = "spontaneous_correct"
    SPONTANEOUS_SELF_CORRECTION = "spontaneous_self_correction"
    CORRECT_AFTER_HINT = "correct_after_hint"
    CORRECT_AFTER_EXTERNAL_CORRECTION = "correct_after_external_correction"
    INCORRECT = "incorrect"
    INCONCLUSIVE = "inconclusive"


class SessionStatus(str, Enum):
    ACTIVE = "active"
    ENDED = "ended"


class ProviderFunction(str, Enum):
    TUTOR = "tutor"
    EVALUATOR = "evaluator"


class DecisionType(str, Enum):
    """Acoes pedagogicas que o Decisor V0 pode escolher (Secao 17)."""

    MINIMAL_EXPLANATION = "minimal_explanation"
    GUIDED_RETRIEVAL = "guided_retrieval"
    CONTRASTIVE_PRACTICE = "contrastive_practice"
    CONTEXTUAL_PRODUCTION = "contextual_production"
    NOVEL_CONTEXT_TRANSFER = "novel_context_transfer"
    SCHEDULE_RECALL = "schedule_recall"
    DISCRETE_VALIDATION = "discrete_validation"
    TARGETED_REGRESSION_CHECK = "targeted_regression_check"
    EXIT_ACTIVE_FOCUS = "exit_active_focus"


class RoutingDecision(str, Enum):
    """Secao 18 - decisao explicita sobre como tratar uma competencia."""

    SKIP = "SKIP"
    VALIDATE = "VALIDATE"
    STUDY = "STUDY"


class MemoryCardState(str, Enum):
    """Espelha fsrs.State sem vazar o tipo da biblioteca para o dominio."""

    LEARNING = "learning"
    REVIEW = "review"
    RELEARNING = "relearning"
