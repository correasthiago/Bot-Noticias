"""Verificacoes automaticas de integridade (Secao 23; ampliadas pelo
pacote de correcao v0.2, Secao 15).

Cada funcao `check_*` cobre um item explicito da especificacao e devolve
uma lista de `IntegrityFinding` (vazia se tudo estiver ok). `run_all`
agrega tudo para o endpoint/comando `integrity_check`.

Estas verificacoes sao so-leitura: nunca escrevem no banco.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from central_universal.domain.clock import parse_iso
from central_universal.domain.enums import (
    CompetencyDimensionState,
    Dimension,
    EvidenceRelation,
    HelpLevel,
    ProjectionGenerationStatus,
)
from central_universal.evidence.aggregation import AggregationConfig, UsableEvidence, classify_dimension
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
    """Defesa em profundidade (Secao 12 do pacote de correcao v0.2): a
    escrita normal ja bloqueia ciclos em `PrerequisiteRepository.insert`;
    esta checagem pega qualquer ciclo que tenha entrado por outro caminho
    (SQL bruto, migration futura malfeita)."""

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
    sempre cria os dois juntos (Secao 4 do pacote de correcao v0.2), entao
    um evento orfao indica corrupcao ou escrita fora do caminho oficial."""

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
    """Secao 13 do pacote de correcao v0.2: RuleVersion, DecisionEvent,
    ProviderEvent, MemoryReviewLog e BackupEvent, alem das tabelas de
    evidencia originais e de competency_state (agora append-only por
    geracao), precisam ter trigger de imutabilidade no banco."""

    required = {
        "trg_raw_interaction_immutable_update",
        "trg_raw_interaction_immutable_delete",
        "trg_evidence_event_immutable_update",
        "trg_evidence_event_immutable_delete",
        "trg_evidence_assessment_immutable_update",
        "trg_evidence_assessment_immutable_delete",
        "trg_competency_state_immutable_update",
        "trg_competency_state_immutable_delete",
        "trg_rule_version_immutable_update",
        "trg_rule_version_immutable_delete",
        "trg_decision_event_immutable_update",
        "trg_decision_event_immutable_delete",
        "trg_provider_event_immutable_update",
        "trg_provider_event_immutable_delete",
        "trg_memory_review_log_immutable_update",
        "trg_memory_review_log_immutable_delete",
        "trg_memory_observation_immutable_update",
        "trg_memory_observation_immutable_delete",
        "trg_backup_event_immutable_update",
        "trg_backup_event_immutable_delete",
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


def _usable_evidence_for(repos: Repositories, competency_id: str, dimension: Dimension) -> list[UsableEvidence]:
    """Reproduz EXATAMENTE o filtro que `evidence.service.recompute_state`
    aplica antes de agregar - incluindo a exclusao de achados inconclusive/
    com causa alternativa pendente (Secao 11 do pacote de correcao v0.2).
    Usada tanto pelos checks especificos abaixo quanto pela recomputacao
    de auditoria."""

    usable: list[UsableEvidence] = []
    for event in repos.evidence_events.list_for_competency(competency_id, dimension):
        if event.relation == EvidenceRelation.MERE_PRESENCE:
            continue
        assessments = repos.evidence_assessments.get_for_evidence_event(event.id)
        if not assessments:
            continue
        latest = max(assessments, key=lambda a: a.created_at)
        if latest.inconclusive or latest.alternative_cause:
            continue
        usable.append(
            UsableEvidence(
                evidence_type=latest.classification,
                relation=event.relation,
                help_level=event.help_level,
                evidence_cluster_id=event.evidence_cluster_id,
                confidence=latest.confidence,
                created_at=parse_iso(event.created_at),
            )
        )
    return usable


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
        usable = _usable_evidence_for(repos, competency.id, Dimension.RETENTION)
        strong_days = {
            e.created_at.date()
            for e in usable
            if e.evidence_type.value == "positive" and e.relation == EvidenceRelation.TARGET
            and e.help_level in (HelpLevel.A0, HelpLevel.A1)
        }
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
        usable = _usable_evidence_for(repos, competency.id, Dimension.RETRIEVAL)
        independent_clusters = {
            e.evidence_cluster_id
            for e in usable
            if e.evidence_type.value == "positive" and e.relation == EvidenceRelation.TARGET
            and e.help_level in (HelpLevel.A0, HelpLevel.A1)
        }
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


def check_generation_consistency(repos: Repositories) -> list[IntegrityFinding]:
    """Secao 15 do pacote de correcao v0.2: 'geracao ativa' consistente -
    no maximo uma geracao com status 'active', o ponteiro
    active_projection_generation (se existir) aponta para uma geracao que
    realmente existe e esta com status 'active', e nenhuma
    competency_state referencia uma geracao inexistente."""

    findings: list[IntegrityFinding] = []
    generations = repos.projection_generations.list_all()
    generation_ids = {g.id for g in generations}
    active_generations = [g for g in generations if g.status == ProjectionGenerationStatus.ACTIVE]

    if len(active_generations) > 1:
        findings.append(
            IntegrityFinding(
                check="generation_consistency",
                severity="error",
                message=(
                    f"{len(active_generations)} geracoes marcadas como 'active' simultaneamente: "
                    f"{', '.join(g.id for g in active_generations)}"
                ),
            )
        )

    pointer_id = repos.active_projection_generation.get_id()
    if pointer_id is not None:
        if pointer_id not in generation_ids:
            findings.append(
                IntegrityFinding(
                    check="generation_consistency",
                    severity="error",
                    message=f"active_projection_generation aponta para geracao inexistente {pointer_id}",
                )
            )
        else:
            pointed = next(g for g in generations if g.id == pointer_id)
            if pointed.status != ProjectionGenerationStatus.ACTIVE:
                findings.append(
                    IntegrityFinding(
                        check="generation_consistency",
                        severity="error",
                        message=(
                            f"active_projection_generation aponta para {pointer_id}, "
                            f"mas o status dela e '{pointed.status.value}', nao 'active'"
                        ),
                    )
                )

    orphan_generation_ids = {
        row["generation_id"]
        for row in repos.conn.execute("SELECT DISTINCT generation_id FROM competency_state;").fetchall()
    } - generation_ids
    for gen_id in orphan_generation_ids:
        findings.append(
            IntegrityFinding(
                check="generation_consistency",
                severity="error",
                message=f"competency_state referencia geracao inexistente {gen_id}",
                ref_id=gen_id,
            )
        )

    return findings


def check_assessment_origin_consistency(repos: Repositories) -> list[IntegrityFinding]:
    """Secao 15: `CompetencyState.last_evidence_assessment_id`, quando
    presente, precisa apontar para uma EvidenceAssessment que exista e
    cujo EvidenceEvent seja da MESMA (competencia, dimensao) do
    CompetencyState - nunca uma avaliacao de outra competencia/dimensao."""

    findings: list[IntegrityFinding] = []
    for competency in repos.competencies.list_all():
        for dimension in Dimension:
            state = repos.competency_states.current(competency.id, dimension)
            if state is None or state.last_evidence_assessment_id is None:
                continue
            assessment = repos.evidence_assessments.get(state.last_evidence_assessment_id)
            if assessment is None:
                findings.append(
                    IntegrityFinding(
                        check="assessment_origin_consistency",
                        severity="error",
                        message=(
                            f"CompetencyState {state.id} ({competency.code}/{dimension.value}) "
                            f"aponta para EvidenceAssessment inexistente {state.last_evidence_assessment_id}"
                        ),
                        ref_id=state.id,
                    )
                )
                continue
            origin_event = repos.evidence_events.get(assessment.evidence_event_id)
            if origin_event is None or origin_event.competency_id != competency.id or origin_event.dimension != dimension:
                findings.append(
                    IntegrityFinding(
                        check="assessment_origin_consistency",
                        severity="error",
                        message=(
                            f"CompetencyState {state.id} ({competency.code}/{dimension.value}) "
                            f"aponta para uma avaliacao de origem que nao pertence a esta "
                            "(competencia, dimensao)."
                        ),
                        ref_id=state.id,
                    )
                )
    return findings


def check_memory_card_review_log_consistency(repos: Repositories) -> list[IntegrityFinding]:
    """Secao 15: consistencia entre o card FSRS 'atual' (memory_state) e
    o historico de reviews (memory_review_log) - o card so pode ter
    last_review_at vazio se nao houver NENHUM review logado, e se houver
    reviews, last_review_at deve bater com o review mais recente."""

    findings: list[IntegrityFinding] = []
    for state in repos.memory_states.list_all():
        latest_log = repos.memory_review_logs.latest_for_competency(state.competency_id)
        if latest_log is None:
            if state.last_review_at is not None:
                findings.append(
                    IntegrityFinding(
                        check="memory_card_review_log_consistency",
                        severity="error",
                        message=(
                            f"MemoryState da competencia {state.competency_id} tem last_review_at "
                            "mas nao ha nenhum MemoryReviewLog correspondente."
                        ),
                        ref_id=state.competency_id,
                    )
                )
            continue
        if state.last_review_at is None:
            findings.append(
                IntegrityFinding(
                    check="memory_card_review_log_consistency",
                    severity="error",
                    message=(
                        f"MemoryState da competencia {state.competency_id} nao tem last_review_at "
                        "mas ha MemoryReviewLog registrado."
                    ),
                    ref_id=state.competency_id,
                )
            )
        elif parse_iso(state.last_review_at) != parse_iso(latest_log.review_datetime):
            findings.append(
                IntegrityFinding(
                    check="memory_card_review_log_consistency",
                    severity="error",
                    message=(
                        f"MemoryState.last_review_at ({state.last_review_at}) diverge do "
                        f"MemoryReviewLog mais recente ({latest_log.review_datetime}) para "
                        f"a competencia {state.competency_id}."
                    ),
                    ref_id=state.competency_id,
                )
            )
    return findings


def check_state_matches_recomputation(repos: Repositories) -> list[IntegrityFinding]:
    """Estado derivado incompativel com historico: recalcula cada
    (competencia, dimensao) a partir do event log - usando a MESMA
    RuleVersion (e portanto a mesma AggregationConfig) que o estado
    persistido diz ter usado - e compara estado, regressao e contradicao
    com o que esta persistido como 'atual'. Qualquer divergencia e um
    estado impossivel/corrompido (Secao 15 do pacote de correcao v0.2)."""

    findings: list[IntegrityFinding] = []
    for competency in repos.competencies.list_all():
        for dimension in Dimension:
            current = repos.competency_states.current(competency.id, dimension)
            usable = _usable_evidence_for(repos, competency.id, dimension)

            if current is None:
                rule_version = repos.active_rule_version.get()
            else:
                rule_version = repos.rule_versions.get(current.rule_version_id)

            config = (
                AggregationConfig.from_json(rule_version.config_json)
                if rule_version is not None
                else AggregationConfig()
            )
            recomputed = classify_dimension(dimension, usable, config)

            current_state = current.state if current else CompetencyDimensionState.NOT_ASSESSED
            current_regression = current.possible_regression if current else False
            current_contradiction = current.has_unresolved_contradiction if current else False

            mismatches = []
            if current_state != recomputed.state:
                mismatches.append(f"state persistido={current_state.value} vs recalculado={recomputed.state.value}")
            if current_regression != recomputed.possible_regression:
                mismatches.append(
                    f"possible_regression persistido={current_regression} vs recalculado={recomputed.possible_regression}"
                )
            if current_contradiction != recomputed.has_unresolved_contradiction:
                mismatches.append(
                    f"has_unresolved_contradiction persistido={current_contradiction} "
                    f"vs recalculado={recomputed.has_unresolved_contradiction}"
                )

            if mismatches:
                findings.append(
                    IntegrityFinding(
                        check="state_matches_recomputation",
                        severity="error",
                        message=(
                            f"Competencia {competency.code}/{dimension.value}: " + "; ".join(mismatches)
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
    check_generation_consistency,
    check_assessment_origin_consistency,
    check_memory_card_review_log_consistency,
    check_state_matches_recomputation,
)


def run_all(repos: Repositories) -> IntegrityReport:
    findings: list[IntegrityFinding] = []
    for check in ALL_CHECKS:
        findings.extend(check(repos))
    return IntegrityReport(findings=tuple(findings))
