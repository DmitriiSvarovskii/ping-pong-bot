"""Alembic baseline configuration tests."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from pingstats.infrastructure.database.base import Base
from pingstats.modules.friendships.infrastructure import models as friendship_models  # noqa: F401
from pingstats.modules.game_sessions.infrastructure import models as session_models  # noqa: F401
from pingstats.modules.matches.infrastructure import models as match_models  # noqa: F401
from pingstats.modules.users.infrastructure import models as user_models  # noqa: F401


def test_alembic_configuration_and_metadata_are_available() -> None:
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)

    assert config.get_main_option("script_location").endswith("/alembic")
    assert script.get_current_head() == "20261107_0007"
    assert set(Base.metadata.tables) == {
        "friend_requests",
        "friendships",
        "game_session_participants",
        "game_session_current_pairs",
        "game_session_processed_results",
        "game_session_queue",
        "game_session_queue_events",
        "game_sessions",
        "match_participants",
        "match_result_revisions",
        "match_sets",
        "matches",
        "users",
    }


def test_initial_migration_is_non_destructive() -> None:
    migration_path = Path("alembic/versions/20261107_0001_initial_alembic_baseline.py")
    migration = migration_path.read_text(encoding="utf-8").lower()

    assert "op.create_table" not in migration
    assert "op.drop_table" not in migration
    assert "drop table" not in migration
