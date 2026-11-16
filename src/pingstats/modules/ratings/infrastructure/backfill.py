"""Internal CLI for safe historical rating backfill."""

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import datetime

from pingstats.core.config import Settings
from pingstats.core.logging import configure_logging
from pingstats.infrastructure.database.engine import Database
from pingstats.modules.ratings.application.backfill import (
    RatingBackfillBlockedError,
    RatingBackfillConflictError,
    RatingBackfillReport,
    RatingHistoricalBackfill,
)
from pingstats.modules.ratings.infrastructure.uow import SqlAlchemyRatingsUnitOfWork


async def run_backfill(
    *,
    dry_run: bool,
    completed_from: datetime | None = None,
    completed_before: datetime | None = None,
    settings: Settings | None = None,
) -> RatingBackfillReport:
    resolved = settings or Settings()
    configure_logging(resolved.log_level)
    database = Database.from_settings(resolved)

    def factory() -> SqlAlchemyRatingsUnitOfWork:
        return SqlAlchemyRatingsUnitOfWork(database.session_factory)

    try:
        return await RatingHistoricalBackfill(
            completed_from=completed_from,
            completed_before=completed_before,
        ).run(factory, dry_run=dry_run)
    finally:
        await database.dispose()


def _aware_datetime(raw: str) -> datetime:
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise argparse.ArgumentTypeError("completed_at bounds must include a timezone")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill PingStats rating history")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Report without database writes")
    mode.add_argument("--apply", action="store_true", help="Persist inputs and run rating workflow")
    parser.add_argument(
        "--from-completed-at",
        type=_aware_datetime,
        help="Inclusive ISO-8601 completed_at lower bound",
    )
    parser.add_argument(
        "--before-completed-at",
        type=_aware_datetime,
        help="Exclusive ISO-8601 completed_at upper bound",
    )
    arguments = parser.parse_args()
    try:
        report = asyncio.run(
            run_backfill(
                dry_run=arguments.dry_run,
                completed_from=arguments.from_completed_at,
                completed_before=arguments.before_completed_at,
            )
        )
    except RatingBackfillConflictError as exc:
        print(json.dumps(asdict(exc.report), sort_keys=True))
        raise SystemExit(2) from exc
    except RatingBackfillBlockedError as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        raise SystemExit(3) from exc
    print(json.dumps(asdict(report), sort_keys=True))


if __name__ == "__main__":
    main()
