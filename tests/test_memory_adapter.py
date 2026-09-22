from __future__ import annotations

from datetime import datetime, timedelta, timezone

import fsrs
import pytest

from central_universal.memory.fsrs_adapter import (
    MemoryAdapter,
    MemoryReviewError,
    rating_from_production_result,
)
from central_universal.domain.enums import ProductionResult
from central_universal.persistence.repositories import Repositories


def test_get_or_create_state_is_idempotent(repos: Repositories, make_competency):
    adapter = MemoryAdapter(repos)
    competency_id = make_competency()
    first = adapter.get_or_create_state(competency_id)
    second = adapter.get_or_create_state(competency_id)
    assert first.id == second.id
    assert first.fsrs_card_json == second.fsrs_card_json


def test_review_advances_due_date_and_logs(repos: Repositories, make_competency):
    adapter = MemoryAdapter(repos)
    competency_id = make_competency()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    result = adapter.review(competency_id, rating=int(fsrs.Rating.Good), review_datetime=now)
    assert not isinstance(result, MemoryReviewError)
    assert result.due_at is not None

    logs = repos.memory_review_logs.list_for_competency(competency_id)
    assert len(logs) == 1
    assert logs[0].rating == int(fsrs.Rating.Good)


def test_review_failure_does_not_corrupt_existing_state(repos: Repositories, make_competency, monkeypatch):
    adapter = MemoryAdapter(repos)
    competency_id = make_competency()
    good_state = adapter.get_or_create_state(competency_id)

    def boom(*args, **kwargs):
        raise RuntimeError("simulated fsrs failure")

    monkeypatch.setattr(adapter.scheduler, "review_card", boom)
    outcome = adapter.review(competency_id, rating=int(fsrs.Rating.Good))

    assert isinstance(outcome, MemoryReviewError)
    still_there = repos.memory_states.get(competency_id)
    assert still_there is not None
    assert still_there.fsrs_card_json == good_state.fsrs_card_json  # inalterado


def test_card_reconstructable_from_stored_json(repos: Repositories, make_competency):
    adapter = MemoryAdapter(repos)
    competency_id = make_competency()
    adapter.review(competency_id, rating=int(fsrs.Rating.Good))
    state = repos.memory_states.get(competency_id)
    card = fsrs.Card.from_json(state.fsrs_card_json)
    assert card.stability == state.stability
    assert card.difficulty == state.difficulty


def test_is_recall_due_reflects_scheduler(repos: Repositories, make_competency):
    adapter = MemoryAdapter(repos)
    competency_id = make_competency()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    adapter.review(competency_id, rating=int(fsrs.Rating.Good), review_datetime=now)

    assert adapter.is_recall_due(competency_id, now=now) is False
    far_future = now + timedelta(days=3650)
    assert adapter.is_recall_due(competency_id, now=far_future) is True


def test_rating_from_production_result_maps_known_values():
    assert rating_from_production_result(ProductionResult.SPONTANEOUS_CORRECT) == int(fsrs.Rating.Easy)
    assert rating_from_production_result(ProductionResult.INCORRECT) == int(fsrs.Rating.Again)
    assert rating_from_production_result(ProductionResult.INCONCLUSIVE) is None
