"""Single-process polling recorder used by the Windows launcher."""

from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import time

from sqlalchemy.orm import Session

from transitpulse_api.config import settings
from transitpulse_api.database import get_engine
from transitpulse_api.realtime import ingest_realtime


logger = logging.getLogger("transitpulse.realtime_recorder")


def configure_logging(
    *,
    log_file: Path | None = None,
    max_bytes: int = 2_000_000,
    backup_count: int = 3,
) -> None:
    """Configure bounded recorder logs without changing ingestion behavior."""

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler: logging.Handler
    if log_file is None:
        handler = logging.StreamHandler()
    else:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
    handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def record_once() -> None:
    with Session(get_engine()) as session:
        result = ingest_realtime(session)
    if result.failures:
        logger.warning("Realtime poll completed with source failures: %s", ", ".join(result.failures))
    else:
        logger.info(
            "Recorded current realtime state: feeds=%s observations=%s",
            result.feed_counts,
            result.recorded_counts,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll official Edmonton GTFS-Realtime feeds")
    parser.add_argument("--once", action="store_true", help="Fetch and persist one snapshot, then exit")
    parser.add_argument("--interval", type=int, default=settings.realtime_poll_seconds)
    parser.add_argument("--log-file", type=Path, help="Write a bounded rotating log file instead of stderr")
    parser.add_argument("--log-max-bytes", type=int, default=2_000_000)
    parser.add_argument("--log-backup-count", type=int, default=3)
    args = parser.parse_args()
    if args.interval < 5:
        parser.error("--interval must be at least five seconds")
    if args.log_max_bytes < 1 or args.log_backup_count < 0:
        parser.error("log rotation limits must be non-negative, with --log-max-bytes at least one")
    configure_logging(
        log_file=args.log_file,
        max_bytes=args.log_max_bytes,
        backup_count=args.log_backup_count,
    )
    while True:
        try:
            record_once()
        except Exception:
            logger.exception("Realtime poll failed; keeping recorder alive")
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
