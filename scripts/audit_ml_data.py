"""Repository-root launcher for the read-only ML data audit."""

from __future__ import annotations

import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "apps" / "api"))

from transitpulse_ml.audit import main  # noqa: E402


if __name__ == "__main__":
    main()
