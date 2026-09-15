"""Capture the curated portfolio screenshots into docs/screenshots.

Usage (with the app running): python scripts/portfolio_capture/portfolio.py [scene ...]

Each scene runs in a fresh headless Chrome profile at 1920x1080 via scenes.py,
then the file is renamed to its README name. Rejects any capture whose page
shows a loading state, an error banner or the no-basemap notice.
"""

import asyncio
import shutil
import sys
import tempfile
import time
from pathlib import Path

import scenes
from cdp import Chrome, Page

DEST = Path(__file__).resolve().parents[2] / "docs" / "screenshots"
WORK = Path(tempfile.gettempdir()) / "transitpulse-portfolio-capture"

# scene name -> (file the scene writes, portfolio name)
PLAN = [
    ("live_route", "live-route-popup.png", "01-live-operations.png"),
    ("replay", "replay.png", "02-replay.png"),
    ("analytics", "analytics.png", "03-analytics.png"),
    ("scenario_a", "scenario-design-change.png", "04-scenarios-design-change.png"),
    ("scenario_b", "scenario-frequency-change.png", "05-scenarios-frequency-change.png"),
    ("map_focus", "map-live-route.png", "06-map-live-route.png"),
    ("scenario_band", "scenario-comparison-panel.png", "07-scenario-comparison.png"),
]

BAD_STATE = r"""(() => {
  const text = document.body.innerText;
  const problems = [];
  if (document.querySelector('.map-basemap-notice')) problems.push('no-basemap notice');
  if (document.querySelector('.scenario-error')) problems.push('scenario error');
  for (const phrase of ['Loading', 'not responding', 'Request failed', 'could not be loaded', 'Estimator unavailable']) {
    if (text.includes(phrase)) problems.push(phrase);
  }
  return problems;
})()"""


async def capture(scene: str, attempts: int = 3) -> bool:
    for attempt in range(1, attempts + 1):
        chrome = Chrome(port=9700 + attempt)
        try:
            page = await Page.open(chrome.ws_url)
            await scenes.boot(page, 1920, 1080)
            await scenes.SCENES[scene](page, WORK, 1920, 1080)
            problems = await page.js(BAD_STATE)
            errors = [c for c in page.console if c.startswith(("error", "exception"))]
            await page.close()
            if problems or errors:
                print(f"{scene}: attempt {attempt} rejected: {problems} {errors[:2]}")
                continue
            return True
        except Exception as error:  # noqa: BLE001
            print(f"{scene}: attempt {attempt} failed: {error!r}")
        finally:
            chrome.close()
    return False


async def main():
    WORK.mkdir(exist_ok=True)
    DEST.mkdir(parents=True, exist_ok=True)
    only = set(sys.argv[1:])
    for scene, produced, name in PLAN:
        if only and scene not in only:
            continue
        started = time.time()
        if await capture(scene):
            shutil.copyfile(WORK / produced, DEST / name)
            print(f"{name}: ok ({time.time() - started:.1f}s, {(DEST / name).stat().st_size // 1024} KB)")
        else:
            print(f"{name}: NOT CAPTURED")


asyncio.run(main())
