"""Standard-library logging configuration."""

import logging


def configure_logging(log_level: str) -> None:
    """Configure a concise local log format at the requested level."""
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        force=True,
    )
