"""Create a lightweight milestone snapshot archive.

Snapshots are plain filesystem archives that give each milestone a
self-contained restore point, independent of version control.  A snapshot
preserves only what would be expensive to reconstruct — source, migrations,
scripts, small docs — and deliberately never duplicates version control,
virtual environments, package/build caches, database storage, logs, or
generated datasets and model artifacts, which either regenerate or are already
persisted under ``artifacts/``.

Usage::

    python scripts/create_milestone_snapshot.py <slug>

Refuses to overwrite an existing archive.  Keep at most three normal
milestone snapshots and prune the oldest only after verifying the newer ones.
"""

import argparse, os, pathlib, zipfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("slug", help="short name, e.g. 2026-09-11-validated-ui-foundation")
args = parser.parse_args()

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "milestone_snapshots"
OUT_DIR.mkdir(exist_ok=True)
OUT = OUT_DIR / f"{args.slug}.zip"
if OUT.exists():
    raise SystemExit(f"refusing to overwrite existing snapshot: {OUT}")

# Things that would be expensive to reconstruct.
TREES = ["apps", "migrations", "scripts", "docs", ".github"]
FILES = [
    "currentHandoff.md", "SUPERVISOR.md", "README.md", "WORKLOG.md", "PROJECT_COMPLETE.md",
    "M7_GATE5_ACCEPTANCE.md", "alembic.ini", "docker-compose.yml",
    "START_TRANSITPULSE.bat", ".env.example", ".gitignore", ".gitattributes",
]
# Never duplicate: VCS, environments, caches, DB storage, logs, derived artifacts.
SKIP_DIRS = {
    ".git", ".venv", "node_modules", ".next", "__pycache__", ".pytest_cache",
    ".transitpulse-logs", "artifacts", "milestone_snapshots", ".mypy_cache",
    ".ruff_cache", "coverage", "dist", "build",
    # Package/build caches are reconstructable and enormous.
    ".npm-cache", ".cache", ".turbo", ".swc", ".yarn",
}
# Generated copies that regenerate on install/build, by path relative to ROOT.
# The MapLibre worker is copied out of node_modules on every predev/prebuild.
SKIP_RELATIVE = {"apps/web/public/maplibre"}
# A source snapshot has no business holding a multi-megabyte binary.  Anything
# over this is reported and skipped rather than silently bloating the archive.
MAX_FILE_BYTES = 2 * 1024 * 1024
SKIP_SUFFIX = {".pyc", ".pyo", ".tsbuildinfo", ".log", ".zip"}
# .env holds a live DB password; never snapshot secrets.
SKIP_NAMES = {".env"}

count = 0
total = 0
oversized: list[tuple[int, str]] = []
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for tree in TREES:
        base = ROOT / tree
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [
                d for d in dirnames
                if d not in SKIP_DIRS
                and not d.endswith(".egg-info")
                and (pathlib.Path(dirpath) / d).relative_to(ROOT).as_posix() not in SKIP_RELATIVE
            ]
            for name in filenames:
                if name in SKIP_NAMES or pathlib.Path(name).suffix in SKIP_SUFFIX:
                    continue
                full = pathlib.Path(dirpath) / name
                size = full.stat().st_size
                if size > MAX_FILE_BYTES:
                    oversized.append((size, full.relative_to(ROOT).as_posix()))
                    continue
                z.write(full, full.relative_to(ROOT).as_posix())
                count += 1
                total += size
    for name in FILES:
        f = ROOT / name
        if f.exists():
            z.write(f, name)
            count += 1
            total += f.stat().st_size

print(f"snapshot : {OUT.relative_to(ROOT).as_posix()}")
print(f"files    : {count}")
print(f"raw MB   : {total/1048576:.2f}")
print(f"zip MB   : {OUT.stat().st_size/1048576:.2f}")
with zipfile.ZipFile(OUT) as z:
    bad = [n for n in z.namelist() if any(p in SKIP_DIRS for p in pathlib.PurePosixPath(n).parts)]
    print("leaked heavy paths:", bad[:5] if bad else "none")
    print("contains .env:", any(pathlib.PurePosixPath(n).name == ".env" for n in z.namelist()))
    print("zip integrity  :", z.testzip() or "OK")
if oversized:
    print("skipped oversized:")
    for size, name in sorted(oversized, reverse=True)[:10]:
        print(f"   {size/1048576:7.2f} MB  {name}")
