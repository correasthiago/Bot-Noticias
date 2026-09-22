"""Verificacoes automaticas de integridade (Secao 23).

Cada funcao `check_*` cobre um item explicito da especificacao e devolve
uma lista de `IntegrityFinding` (vazia se tudo estiver ok). `run_all`
agrega tudo para o endpoint/comando `integrity_check`.

Estas verificacoes sao so-leitura: nunca escrevem no banco.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from central_universal.domain.enums import CompetencyDimensionState, Dimension, EvidenceRelation, HelpLevel
from central_universal.evidence.aggregation import UsableEvidence, classify_dimension
from central_universal.domain.clock import parse_iso
from central_universal.persistence.repositories import Repositories


@dataclass(frozen=True)
class IntegrityFinding:
    check: str
    severity: str  # "error" | "warning"
    message: str
    ref_id: str | None = None


@dataclass(frozen=True)
class IntegrityReport:
    findings: tuple[IntegrityFinding, ...]

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)


def check_prerequisite_cycles(repos: Repositories) -> list[IntegrityFinding]:
    edges: dict[str, list[str]] = {}
    for rel in repos.prerequisites.list_all():
        edges.setdefault(rel.competency_id, []).append(rel.prerequisite_id)

    findings: list[IntegrityFinding] = []
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}

    def visit(node: str, path: list[str]) -> None:
        color[node] = GRAY
        for neighbour in edges.get(node, []):
            if color.get(neighbour, WHITE) == WHITE:
                visit(neighbour, path + [neighbour])
            elif color.get(neighbour) == GRAY:
                cycle = " -> ".join(path + [neighbour])
                findings.append(
                    IntegrityFinding(
                        check="prerequisite_cycles",
                        severity="error",
                        message=f"Ciclo de pre-requisitos detectado: {cycle}",
                        ref_id=node,
                    )
                )
        color[node] = BLACK

    for competency_id in list(edges.keys()):
        if color.get(competency_id, WHITE) == WHITE:
            visit(competency_id, [competency_id])

    return findings


def check_dangling_references(repos: Repositories) -> list[IntegrityFinding]:
    """Defesa em profundidade: FOREIGN KEY ja bloqueia isto no SQLite, mas
    conferimos explicitamente as referencias embutidas em JSON
    (Activity.competency_targets), que o SQLite nao valida."""

    findings: list[IntegrityFinding] = []
    valid_competency_ids = {c.id for c in repos.competencies.list_all()}

    for row in repos.conn.execute("SELECT id, competency_targets FROM activity;").fetchall():
        targets = json.loads(row["competency_targets"])
        for target_id in targets:
            if target_id not in valid_competency_ids:
                findings.append(
                    IntegrityFinding(
                        check="dangling_references",
                        severity="error",
                        message=f"Activity {row['id']} referencia competencia inexistente {target_id}",
                        ref_id=row["id"],
                    )
                )
    return findings


def check_orphan_events(repos: Repositories) -> list[IntegrityFinding]:
    """EvidenceEvent sem nenhuma EvidenceAssessment: nosso EvidenceService
    sempre cria os dois juntos, entao um evento orfao indica corrupcao ou
    escrita fora do caminho oficial."""

    findings: list[IntegrityFinding] = []
    for event in repos.evidence_events.list_all():
        assessments = repos.evidence_assessments.get_for_evidence_event(event.id)
        if not assessments:
            findings.append(
                IntegrityFinding(
                    check="orphan_events",
                    severity="error",
                    message=f"EvidenceEvent {event.id} nao tem nenhuma EvidenceAssessment associada",
                    ref_id=event.id,
                )
            )
    return findings


def check_immutability_triggers_present(repos: Repositories) -> list[IntegrityFinding]:
    required = {
        "trg_raw_interaction_immutable_update",
        "trg_raw_interaction_immutable_delete",
        "trg_evidence_event_immutable_update",
        "trg_evidence_event_immutable_delete",
        "trg_evidence_assessment_immutable_update",
        "trg_evidence_assessment_immutable_delete",
    }
    rows = repos.conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger';"
    ).fetchall()
    present = {r["name"] for r in rows}
    missing = required - present
    if missing:
        return [
            IntegrityFinding(
                check="immutability_triggers",
                severity="error",
                message=f"Triggers de imutabilidade ausentes: {', '.join(sorted(missing))}",
            )
        ]
    return []


def check_retention_same_day_promotion(repos: Repositories) -> list[IntegrityFinding]:
    """Nenhuma competency_state de RETENTION pode estar demonstrated/
    consolidated com evidencia forte de um unico dia (Principio 10)."""

    findings: list[IntegrityFinding] = []
    for competency in repos.competencies.list_all():
        state = repos.competency_states.current(competency.id, Dimension.RETENTION)
        if state is None or state.state not in (
            CompetencyDimensionState.DEMONSTRATED,
            CompetencyDimensionState.CONSOLIDATED,
        ):
            continue
        events = repos.evidence_events.list_for_competency(competency.id, Dimension.RETENTION)
        strong_days = set()
        for event in events:
            if event.relation != EvidenceRelation.TARGET or event.help_level not in (
                HelpLevel.A0,
                HelpLevel.A1,
            ):
                continue
            assessments = repos.evidence_assessments.get_for_evidence_event(event.id)
            if not assessments:
                continue
            latest = max(assessments, key=lambda a: a.created_at)
            if latest.classification.value == "positive":
                strong_days.add(parse_iso(event.created_at).date())
        min_days = 3 if state.state == CompetencyDimensionState.CONSOLIDATED else 2
        if len(strong_days) < min_days:
            findings.append(
                IntegrityFinding(
                    check="retention_same_day_promotion",
                    severity="error",
                    message=(
                        f"Competencia {competency.code}: retention={state.state.value} "
                        f"mas so ha {len(strong_days)} dia(s) distinto(s) de evidencia forte."
                    ),
                    ref_id=competency.id,
                )
            )
    return findings


def check_retrieval_independence(repos: Repositories) -> list[IntegrityFinding]:
    """Nenhuma competency_state de RETRIEVAL pode estar demonstrated/
    consolidated apoiada so em evidencia A2/A3 (Secao 9)."""

    findings: list[IntegrityFinding] = []
    for competency in repos.competencies.list_all():
        state = repos.competency_states.current(competency.id, Dimension.RETRIEVAL)
        if state is None or state.state not in (
            CompetencyDimensionState.DEMONSTRATED,
            CompetencyDimensionState.CONSOLIDATED,
        ):
            continue
        events = repos.evidence_events.list_for_competency(competency.id, Dimension.RETRIEVAL)
        independent_clusters = set()
        for event in events:
            if event.relation != EvidenceRelation.TARGET or event.help_level not in (
                HelpLevel.A0,
                HelpLevel.A1,
            ):
                continue
            assessments = repos.evidence_assessments.get_for_evidence_event(event.id)
            if assessments and max(assessments, key=lambda a: a.created_at).classification.value == "positive":
                independent_clusters.add(event.evidence_cluster_id)
        min_clusters = 3 if state.state == CompetencyDimensionState.CONSOLIDATED else 2
        if len(independent_clusters) < min_clusters:
            findings.append(
                IntegrityFinding(
                    check="retrieval_independence",
                    severity="error",
                    message=(
                        f"Competencia {competency.code}: retrieval={state.state.value} "
                        f"sem evidencia A0/A1 independente suficiente "
                        f"({len(independent_clusters)} cluster(s))."
                    ),
                    ref_id=competency.id,
                )
            )
    return findings


def check_mere_presence_promotion(repos: Repositories) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for competency in repos.competencies.list_all():
        for dimension in Dimension:
            state = repos.competency_states.current(competency.id, dimension)
            if state is None or state.state == CompetencyDimensionState.NOT_ASSESSED:
                continue
            events = repos.evidence_events.list_for_competency(competency.id, dimension)
            non_mere_presence = [e for e in events if e.relation != EvidenceRelation.MERE_PRESENCE]
            if events and not non_mere_presence:
                findings.append(
                    IntegrityFinding(
                        check="mere_presence_promotion",
                        severity="error",
                        message=(
                            f"Competencia {competency.code}/{dimension.value} tem estado "
                            f"{state.state.value} apoiado apenas em evidencia mere_presence."
                        ),
                        ref_id=competency.id,
                    )
                )
    return findings


def check_evaluations_without_rule_version(repos: Repositories) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    valid_versions = {rv.id for rv in repos.rule_versions.list_all()}
    for assessment in repos.evidence_assessments.list_all():
        if assessment.rule_version_id not in valid_versions:
            findings.append(
                IntegrityFinding(
                    check="evaluations_without_rule_version",
                    severity="error",
                    message=f"EvidenceAssessment {assessment.id} referencia RuleVersion inexistente",
                    ref_id=assessment.id,
                )
            )
    return findings


def check_decision_events_without_justification(repos: Repositories) -> list[IntegrityFinding]:
    findings: list[IntegrityFinding] = []
    for event in repos.decision_events.list_recent(limit=100000):
        if not event.justification or not event.justification.strip():
            findings.append(
                IntegrityFinding(
                    check="decision_events_without_justification",
                    severity="error",
                    message=f"DecisionEvent {event.id} sem justificativa",
                    ref_id=event.id,
                )
            )
    return findings


def check_state_matches_recomputation(repos: Repositories) -> list[IntegrityFinding]:
    """Estado derivado incompativel com historico: recalcula cada
    (competencia, dimensao) a partir do event log e compara com o que
    esta persistido como 'atual'. Qualquer divergencia e um estado
    impossivel/corrompido."""

    findings: list[IntegrityFinding] = []
    for competency in repos.competencies.list_all():
        for dimension in Dimension:
            events = repos.evidence_events.list_for_competency(competency.id, dimension)
            usable = []
            for event in events:
                if event.relation == EvidenceRelation.MERE_PRESENCE:
                    continue
                assessments = repos.evidence_assessments.get_for_evidence_event(event.id)
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
            recomputed = classify_dimension(dimension, usable)
            current = repos.competency_states.current(competency.id, dimension)
            current_state = current.state if current else CompetencyDimensionState.NOT_ASSESSED
            if current_state != recomputed.state:
                findings.append(
                    IntegrityFinding(
                        check="state_matches_recomputation",
                        severity="error",
                        message=(
                            f"Competencia {competency.code}/{dimension.value}: estado "
                            f"persistido={current_state.value} diverge do recalculado="
                            f"{recomputed.state.value}."
                        ),
                        ref_id=competency.id,
                    )
                )
    return findings


ALL_CHECKS = (
    check_prerequisite_cycles,
    check_dangling_references,
    check_orphan_events,
    check_immutability_triggers_present,
    check_retention_same_day_promotion,
    check_retrieval_independence,
    check_mere_presence_promotion,
    check_evaluations_without_rule_version,
    check_decision_events_without_justification,
    check_state_matches_recomputation,
)


def run_all(repos: Repositories) -> IntegrityReport:
    findings: list[IntegrityFinding] = []
    for check in ALL_CHECKS:
        findings.extend(check(repos))
    return IntegrityReport(findings=tuple(findings))
