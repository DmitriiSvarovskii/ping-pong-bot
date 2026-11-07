"""Metadata contract tests for game session persistence."""

from sqlalchemy import CheckConstraint, Index, UniqueConstraint

from pingstats.infrastructure.database.base import Base
from pingstats.modules.game_sessions.infrastructure.models import (  # noqa: F401
    GameSessionCurrentPairModel,
    GameSessionModel,
    GameSessionParticipantModel,
    GameSessionProcessedResultModel,
    GameSessionQueueEventModel,
    GameSessionQueueModel,
)
from pingstats.modules.matches.infrastructure.models import MatchModel  # noqa: F401


def test_game_session_tables_and_columns_are_registered() -> None:
    assert {
        "game_sessions",
        "game_session_participants",
        "game_session_queue",
        "game_session_queue_events",
        "game_session_current_pairs",
        "game_session_processed_results",
    }.issubset(Base.metadata.tables)

    sessions = Base.metadata.tables["game_sessions"]
    assert set(sessions.columns.keys()) == {
        "id",
        "organizer_id",
        "status",
        "name",
        "points_to_win",
        "sets_to_win",
        "is_ranked",
        "created_at",
        "started_at",
        "completed_at",
        "version",
    }
    assert str(sessions.c.points_to_win.server_default.arg) == "11"
    assert str(sessions.c.sets_to_win.server_default.arg) == "1"
    assert str(sessions.c.is_ranked.server_default.arg) == "true"


def test_participation_and_queue_constraints_support_history_and_atomic_reorder() -> None:
    participants = Base.metadata.tables["game_session_participants"]
    active_index = next(
        item
        for item in participants.indexes
        if item.name == "uq_game_session_participants_active_user"
    )
    assert isinstance(active_index, Index)
    assert active_index.unique
    assert str(active_index.dialect_options["postgresql"]["where"]) == "left_at IS NULL"

    queue = Base.metadata.tables["game_session_queue"]
    unique_constraints = {
        item.name: item for item in queue.constraints if isinstance(item, UniqueConstraint)
    }
    for name in (
        "uq_game_session_queue_session_position",
        "uq_game_session_queue_session_participant",
    ):
        assert unique_constraints[name].deferrable
        assert unique_constraints[name].initially == "IMMEDIATE"

    checks = {item.name for item in queue.constraints if isinstance(item, CheckConstraint)}
    assert "ck_game_session_queue_position_positive" in checks


def test_matches_metadata_requires_consistent_game_session_link() -> None:
    matches = Base.metadata.tables["matches"]
    assert matches.c.game_session_id.nullable
    assert matches.c.sequence_number.nullable
    assert any(
        item.name == "ck_matches_game_session_context_consistent" for item in matches.constraints
    )
    assert any(item.name == "uq_matches_game_session_sequence" for item in matches.constraints)
    active_index = next(
        item for item in matches.indexes if item.name == "uq_matches_active_game_session"
    )
    assert active_index.unique
    assert "status = 'active'" in str(active_index.dialect_options["postgresql"]["where"])


def test_current_pair_and_processed_result_constraints_are_registered() -> None:
    pair = Base.metadata.tables["game_session_current_pairs"]
    assert pair.primary_key.columns.keys() == ["session_id"]
    assert any(
        item.name == "ck_game_session_current_pairs_players_distinct" for item in pair.constraints
    )

    processed = Base.metadata.tables["game_session_processed_results"]
    assert any(
        item.name == "uq_game_session_processed_results_session_result"
        for item in processed.constraints
    )
