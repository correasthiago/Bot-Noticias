"""Contrato de entrada/saida do Avaliador (Secao 16).

O Avaliador (LLM ou regra) recebe uma interacao bruta e candidatos de
competencia e retorna uma lista de `EvaluatorFinding`. Cada finding e
validado estruturalmente antes de poder gerar EvidenceEvent/EvidenceAssessment.
Uma resposta invalida (campo fora do enum, confianca fora de [0,1],
inconclusive=True sem classification='inconclusive', etc.) e descartada
inteiramente - Principio: "Resposta invalida do LLM NAO altera estado"
(Secao 16, Teste T7).

Findings estruturalmente validos ainda podem ser EXCLUIDOS da agregacao
mais adiante (Secao 11 do pacote de correcao v0.2): `inconclusive=True`,
confianca abaixo do minimo configurado na RuleVersion, ou
`alternative_cause` pendente nunca promovem/rebaixam CompetencyState nem
disparam revisao de memoria - ver `evidence.service.recompute_state`.
"""

from __future__ import annotations

from dataclasses import dataclass

from central_universal.domain.enums import (
    Dimension,
    EvidenceRelation,
    EvidenceType,
    HelpLevel,
    ProductionResult,
)


@dataclass(frozen=True)
class EvaluatorInput:
    raw_interaction_id: str
    learner_input: str
    tutor_output: str
    candidate_competency_ids: tuple[str, ...]
    help_level: HelpLevel
    production_result: ProductionResult
    context: str
    rule_version: str
    # Qual dimensao esta atividade foi desenhada para exercitar (Secao 17:
    # a acao pedagogica escolhida pelo Decisor ja implica uma dimensao-
    # alvo - MINIMAL_EXPLANATION mira comprehension, GUIDED_RETRIEVAL mira
    # retrieval, etc.). None quando a atividade nao tem uma unica dimensao
    # obvia (ex.: DISCRETE_VALIDATION, revisao longitudinal sem decisao).
    # Isto e um GUIA para o avaliador, nunca uma imposicao - um avaliador
    # real ainda decide por si mesmo qual dimensao a evidencia sustenta.
    target_dimension: Dimension | None = None


@dataclass(frozen=True)
class EvaluatorFinding:
    """Um item da lista retornada pelo avaliador (Secao 16)."""

    competency_id: str
    dimension: Dimension
    classification: EvidenceType
    result: str
    confidence: float
    justification: str
    relation: EvidenceRelation
    alternative_cause: str | None = None
    inconclusive: bool = False


@dataclass(frozen=True)
class EvaluatorOutput:
    findings: tuple[EvaluatorFinding, ...]
    raw_payload: dict | None = None


class InvalidEvaluatorOutput(ValueError):
    """Levantada quando a saida do avaliador nao passa na validacao estrutural."""


def validate_finding(candidate: dict, valid_competency_ids: set[str]) -> EvaluatorFinding:
    """Valida um dict cru (por ex. vindo de um LLM) e devolve um
    EvaluatorFinding tipado, ou levanta InvalidEvaluatorOutput.

    Esta funcao e a fronteira de confianca: nada depois dela precisa
    voltar a duvidar do formato dos dados.
    """

    try:
        competency_id = str(candidate["competency_id"])
        dimension = Dimension(candidate["dimension"])
        classification = EvidenceType(candidate["classification"])
        result = str(candidate["result"])
        confidence = float(candidate["confidence"])
        justification = str(candidate["justification"])
        relation = EvidenceRelation(candidate["relation"])
        alternative_cause = candidate.get("alternative_cause")
        inconclusive = bool(candidate.get("inconclusive", False))
    except (KeyError, ValueError, TypeError) as exc:
        raise InvalidEvaluatorOutput(f"finding malformado: {exc}") from exc

    if competency_id not in valid_competency_ids:
        raise InvalidEvaluatorOutput(
            f"competency_id '{competency_id}' nao esta entre os candidatos validos"
        )
    if not (0.0 <= confidence <= 1.0):
        raise InvalidEvaluatorOutput(f"confidence fora de [0,1]: {confidence}")
    if not justification.strip():
        raise InvalidEvaluatorOutput("justification vazia")
    if classification == EvidenceType.INCONCLUSIVE and not inconclusive:
        # coerencia interna: uma classificacao inconclusive deve marcar o flag
        inconclusive = True
    if inconclusive and classification != EvidenceType.INCONCLUSIVE:
        # Secao 11 do pacote de correcao v0.2: inconclusive=True SO pode
        # coexistir com classification='inconclusive'. Qualquer outra
        # combinacao e rejeitada aqui, na fronteira - o mesmo invariante e
        # reforcado por CHECK constraint no banco (defesa em profundidade).
        raise InvalidEvaluatorOutput(
            f"inconclusive=True exige classification='inconclusive', recebeu '{classification.value}'"
        )

    return EvaluatorFinding(
        competency_id=competency_id,
        dimension=dimension,
        classification=classification,
        result=result,
        confidence=confidence,
        justification=justification,
        relation=relation,
        alternative_cause=str(alternative_cause) if alternative_cause else None,
        inconclusive=inconclusive,
    )


def validate_evaluator_payload(
    payload: dict, valid_competency_ids: set[str]
) -> EvaluatorOutput:
    """Valida o payload inteiro. Se QUALQUER finding for invalido, a lista
    inteira e rejeitada (fail-closed): melhor nao gerar evidencia nenhuma
    do que gerar evidencia parcialmente inconsistente.
    """

    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list) or not raw_findings:
        raise InvalidEvaluatorOutput("payload sem lista 'findings' nao vazia")

    findings = tuple(
        validate_finding(item, valid_competency_ids) for item in raw_findings
    )
    return EvaluatorOutput(findings=findings, raw_payload=payload)
