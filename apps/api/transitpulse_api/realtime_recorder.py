"""Single-process polling recorder used by the Windows launcher."""

from __future__ import annotations

import argparse
import logging
import time

from sqlalchemy.orm import Session

from transitpulse_api.config import settings
from transitpulse_api.database import get_engine
from transitpulse_api.realtime import ingest_realtime


logger = logging.getLogger("transitpulse.realtime_recorder")


def record_once() -> None:
    with Session(get_engine()) as session:
        result = ingest_realtime(session)
    if result.failures:
        logger.warning("Realtime poll completed with source failures: %s", ", ".join(result.failures))
    else:
        logger.info("Recorded current realtime state: %s", result.feed_counts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll official Edmonton GTFS-Realtime feeds")
    parser.add_argument("--once", action="store_true", help="Fetch and persist one snapshot, then exit")
    parser.add_argument("--interval", type=int, default=settings.realtime_poll_seconds)
    args = parser.parse_args()
    if args.interval < 5:
        parser.error("--interval must be at least five seconds")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
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
