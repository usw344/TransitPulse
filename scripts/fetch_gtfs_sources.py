"""Fetch the registered city GTFS archives and record what was actually fetched.

Run:

    .venv\\Scripts\\python.exe scripts\\fetch_gtfs_sources.py [--force] [--only CITY]

An archive already on disk is **not** re-downloaded unless ``--force`` is given.
Agencies republish these files irregularly and a silent re-fetch mid-programme
would change the bytes a dataset was built from without changing its manifest,
which is the quiet kind of reproducibility failure that is very hard to notice
later.

The output manifest is the provenance record for everything downstream: it
carries the URL used, the moment of retrieval, the byte count and the SHA-256 of
each archive, plus the feed's own declared agency, timezone and calendar window
read back out of the archive.  A dataset build refuses to run without it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from transitpulse_ml.city_registry import CITY_FEEDS, CityFeed  # noqa: E402
from transitpulse_ml.gtfs_normalizer import read_calendar  # noqa: E402

SOURCE_DIR = REPO_ROOT / "artifacts" / "gtfs_sources"
MANIFEST_PATH = SOURCE_DIR / "manifest.json"

_USER_AGENT = "TransitPulse/research (cross-city transit planning study)"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def download(feed: CityFeed, destination: Path) -> None:
    request = urllib.request.Request(feed.download_url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=900) as response:
        destination.write_bytes(response.read())


def describe_archive(path: Path) -> dict[str, object]:
    """Read back the feed's own declared identity, so the manifest is evidence.

    Recording only the URL would let a mislabelled registry entry go unnoticed;
    reading ``agency.txt`` and the calendar window back out of the bytes we
    actually hold makes a mismatch visible at build time.
    """

    import csv
    import io

    with zipfile.ZipFile(path) as archive:
        names = {name.rsplit("/", 1)[-1].lower(): name for name in archive.namelist()}
        agency_name = agency_timezone = None
        if "agency.txt" in names:
            with archive.open(names["agency.txt"]) as raw:
                reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
                first = next(reader, None)
                if first:
                    agency_name = (first.get("agency_name") or "").strip() or None
                    agency_timezone = (first.get("agency_timezone") or "").strip() or None
        calendar = read_calendar(archive)
        start, end = calendar.span
        route_types: dict[str, int] = {}
        if "routes.txt" in names:
            with archive.open(names["routes.txt"]) as raw:
                for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                    key = (row.get("route_type") or "?").strip()
                    route_types[key] = route_types.get(key, 0) + 1
        members = sorted(names)

    return {
        "declared_agency": agency_name,
        "declared_timezone": agency_timezone,
        "calendar_start": start.isoformat() if start else None,
        "calendar_end": end.isoformat() if end else None,
        "route_type_counts": route_types,
        "members": members,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-download archives already present")
    parser.add_argument("--only", default=None, help="restrict to one source_id")
    args = parser.parse_args()

    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    existing = {}
    if MANIFEST_PATH.exists():
        existing = {entry["source_id"]: entry for entry in json.loads(MANIFEST_PATH.read_text())["feeds"]}

    entries: list[dict[str, object]] = []
    for feed in CITY_FEEDS:
        if args.only and feed.source_id != args.only:
            if feed.source_id in existing:
                entries.append(existing[feed.source_id])
            continue
        destination = SOURCE_DIR / feed.archive_name
        if destination.exists() and not args.force:
            retrieved_at = existing.get(feed.source_id, {}).get("retrieved_at")
            if retrieved_at is None:
                # Adopting a file fetched outside this script: the moment of
                # retrieval is genuinely unknown, so record the file's mtime and
                # say where the timestamp came from rather than inventing one.
                retrieved_at = datetime.fromtimestamp(
                    destination.stat().st_mtime, tz=timezone.utc
                ).isoformat()
                timestamp_basis = "file mtime (archive adopted, not fetched by this script)"
            else:
                timestamp_basis = existing[feed.source_id].get("timestamp_basis", "fetched by this script")
            action = "kept"
        else:
            print(f"downloading {feed.source_id} ...", flush=True)
            started = datetime.now(timezone.utc)
            download(feed, destination)
            retrieved_at = started.isoformat()
            timestamp_basis = "fetched by this script"
            action = "downloaded"

        try:
            described = describe_archive(destination)
        except Exception as error:  # noqa: BLE001 - report and continue to the next city
            print(f"  !! {feed.source_id}: unreadable archive: {error}", file=sys.stderr)
            continue

        entry = {
            "source_id": feed.source_id,
            "city": feed.city,
            "agency": feed.agency,
            "country": feed.country,
            "download_url": feed.download_url,
            "dataset_page": feed.dataset_page,
            "licence": feed.licence,
            "licence_url": feed.licence_url,
            "archive_name": feed.archive_name,
            "bytes": destination.stat().st_size,
            "sha256": sha256_of(destination),
            "retrieved_at": retrieved_at,
            "timestamp_basis": timestamp_basis,
            **described,
        }
        entries.append(entry)
        print(
            f"  {action:11s} {feed.source_id:28s} {entry['bytes']:>11,} B  "
            f"{entry['declared_agency']}  {entry['calendar_start']}..{entry['calendar_end']}"
        )

    MANIFEST_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "generated_by": "scripts/fetch_gtfs_sources.py",
                "feeds": entries,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nmanifest -> {MANIFEST_PATH.relative_to(REPO_ROOT)} ({len(entries)} feeds)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
