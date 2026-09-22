from __future__ import annotations

from datetime import datetime, timedelta, timezone

from central_universal.domain.enums import (
    CompetencyDimensionState,
    Dimension,
    EvidenceRelation,
    EvidenceType,
    HelpLevel,
)
from central_universal.evidence.aggregation import UsableEvidence, classify_dimension

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def ev(day_offset=0, evidence_type=EvidenceType.POSITIVE, relation=EvidenceRelation.TARGET,
       help_level=HelpLevel.A0, cluster="c1", minute_offset=0):
    return UsableEvidence(
        evidence_type=evidence_type,
        relation=relation,
        help_level=help_level,
        evidence_cluster_id=cluster,
        created_at=BASE + timedelta(days=day_offset, minutes=minute_offset),
    )


def test_no_evidence_is_not_assessed():
    result = classify_dimension(Dimension.ACCURACY, [])
    assert result.state == CompetencyDimensionState.NOT_ASSESSED


def test_single_cluster_is_acquiring_not_demonstrated():
    events = [ev(cluster="c1")]
    result = classify_dimension(Dimension.ACCURACY, events)
    assert result.state == CompetencyDimensionState.ACQUIRING


def test_two_independent_clusters_demonstrate_non_retention_dimension():
    events = [ev(cluster="c1"), ev(cluster="c2", minute_offset=5)]
    result = classify_dimension(Dimension.ACCURACY, events)
    assert result.state == CompetencyDimensionState.DEMONSTRATED


def test_three_independent_clusters_consolidate_non_retention_dimension():
    events = [ev(cluster="c1"), ev(cluster="c2"), ev(cluster="c3")]
    result = classify_dimension(Dimension.COMPREHENSION, events)
    assert result.state == CompetencyDimensionState.CONSOLIDATED


def test_retention_same_day_repeats_never_consolidate():
    # 10 acertos "iguais" no mesmo dia, clusters distintos ate, mas 1 dia so.
    events = [ev(cluster=f"c{i}", minute_offset=i) for i in range(10)]
    result = classify_dimension(Dimension.RETENTION, events)
    assert result.state != CompetencyDimensionState.CONSOLIDATED
    assert result.state != CompetencyDimensionState.DEMONSTRATED


def test_retention_requires_multiple_distinct_days():
    events = [ev(day_offset=0, cluster="c1"), ev(day_offset=5, cluster="c2"), ev(day_offset=10, cluster="c3")]
    result = classify_dimension(Dimension.RETENTION, events)
    assert result.state == CompetencyDimensionState.CONSOLIDATED


def test_a3_help_never_counts_as_independent_retrieval():
    events = [
        ev(cluster="c1", help_level=HelpLevel.A3),
        ev(cluster="c2", help_level=HelpLevel.A3),
        ev(cluster="c3", help_level=HelpLevel.A3),
    ]
    result = classify_dimension(Dimension.RETRIEVAL, events)
    assert result.state == CompetencyDimensionState.ACQUIRING


def test_isolated_negative_does_not_erase_consolidated_state():
    events = [ev(cluster="c1"), ev(cluster="c2"), ev(cluster="c3"),
              ev(cluster="c4", evidence_type=EvidenceType.NEGATIVE, minute_offset=99)]
    result = classify_dimension(Dimension.ACCURACY, events)
    assert result.state == CompetencyDimensionState.CONSOLIDATED
    assert result.possible_regression is True


def test_corroborated_regression_downgrades_one_tier():
    events = [
        ev(cluster="c1"), ev(cluster="c2"), ev(cluster="c3"),
        ev(cluster="c4", evidence_type=EvidenceType.NEGATIVE, minute_offset=97),
        ev(cluster="c5", evidence_type=EvidenceType.NEGATIVE, minute_offset=98),
    ]
    result = classify_dimension(Dimension.ACCURACY, events)
    assert result.state == CompetencyDimensionState.DEMONSTRATED
    assert result.possible_regression is True


def test_qualified_incidental_alone_only_supports_acquiring():
    events = [ev(relation=EvidenceRelation.QUALIFIED_INCIDENTAL, cluster="c1")]
    result = classify_dimension(Dimension.TRANSFER, events)
    assert result.state == CompetencyDimensionState.ACQUIRING


def test_unresolved_contradiction_keeps_insufficient_evidence():
    events = [ev(cluster="c1", evidence_type=EvidenceType.CONTRADICTORY)]
    result = classify_dimension(Dimension.COMPREHENSION, events)
    assert result.state == CompetencyDimensionState.INSUFFICIENT_EVIDENCE


def test_strong_positive_evidence_overrides_minority_contradiction():
    events = [ev(cluster="c1"), ev(cluster="c2"), ev(cluster="c3"),
              ev(cluster="c4", evidence_type=EvidenceType.CONTRADICTORY, minute_offset=50)]
    result = classify_dimension(Dimension.COMPREHENSION, events)
    assert result.state == CompetencyDimensionState.CONSOLIDATED
