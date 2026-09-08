"""Command line entry point for importing a static GTFS archive."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from transitpulse_api.config import settings
from transitpulse_api.database import get_engine
from transitpulse_api.gtfs import GtfsImportError, import_gtfs_zip


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import a versioned static GTFS feed")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--file", type=Path, help="Read an existing GTFS ZIP")
    source.add_argument("--url", help="Download a GTFS ZIP (defaults to the configured Edmonton source)")
    parser.add_argument("--provider", default=settings.gtfs_provider)
    return parser


def _read_archive(file_path: Path | None, source_url: str) -> bytes:
    if file_path is not None:
        try:
            return file_path.read_bytes()
        except OSError as error:
            raise GtfsImportError(f"Could not read {file_path}: {error}") from error
    try:
        request = Request(
            source_url,
            headers={"User-Agent": "TransitPulse/0.1 GTFS importer"},
        )
        with urlopen(request, timeout=settings.gtfs_download_timeout_seconds) as response:  # noqa: S310
            return response.read()
    except OSError as error:
        raise GtfsImportError(f"Could not download GTFS feed: {error}") from error


def main() -> int:
    args = _parser().parse_args()
    source_url = args.url or settings.gtfs_source_url
    try:
        archive = _read_archive(args.file, source_url)
        with Session(get_engine()) as session:
            result = import_gtfs_zip(
                session,
                archive,
                provider=args.provider,
                source_url=source_url if args.file is None else args.file.resolve().as_uri(),
                downloaded_at=datetime.now(timezone.utc),
            )
    except GtfsImportError as error:
        print(f"GTFS import failed: {error}")
        return 2
    print(
        json.dumps(
            {
                "feed_id": str(result.feed_id),
                "duplicate": result.duplicate,
                "checksum_sha256": result.checksum_sha256,
                "counts": result.counts,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
