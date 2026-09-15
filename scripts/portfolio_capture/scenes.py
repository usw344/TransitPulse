"""Scripted TransitPulse scenes, captured from a fresh headless Chrome profile.

Usage: python scripts/portfolio_capture/scenes.py <out_dir> <width> <height> [scene ...]

The app must be running at TRANSITPULSE_WEB_URL (default http://localhost:3000/).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from cdp import Chrome, Page

BASE = os.environ.get("TRANSITPULSE_WEB_URL", "http://localhost:3000/")

HELPERS = r"""
window.__tp = {
  map() {
    const el = document.querySelector('.map');
    const key = el && Object.keys(el).find(k => k.startsWith('__reactFiber$'));
    let fiber = key ? el[key] : null;
    while (fiber) {
      let hook = fiber.memoizedState;
      while (hook && typeof hook === 'object' && 'next' in hook) {
        const v = hook.memoizedState;
        if (v && v.current && typeof v.current.queryRenderedFeatures === 'function') return v.current;
        hook = hook.next;
      }
      fiber = fiber.return;
    }
    return null;
  },
  clickText(selector, text) {
    const nodes = [...document.querySelectorAll(selector)];
    const hit = nodes.find(n => n.textContent.trim().toLowerCase().includes(text.toLowerCase()));
    if (!hit) return false;
    hit.click();
    return true;
  },
  setRange(id, value) {
    const input = document.getElementById(id);
    if (!input) return false;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(input, String(value));
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  },
  setText(selector, value) {
    const input = document.querySelector(selector);
    if (!input) return false;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(input, String(value));
    input.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  },
  featureScreenPoint(layer, predicate) {
    const map = this.map();
    if (!map) return null;
    const rect = map.getCanvas().getBoundingClientRect();
    const band = document.querySelector('.network-intelligence, .analytics-workspace');
    const bandTop = band ? band.getBoundingClientRect().top : rect.bottom;
    const features = map.queryRenderedFeatures({ layers: [layer] });
    for (const f of features) {
      if (predicate && !predicate(f)) continue;
      const p = map.project(f.geometry.coordinates);
      const x = rect.left + p.x, y = rect.top + p.y;
      if (x > rect.left + 260 && x < rect.right - 380 && y > rect.top + 120 && y < bandTop - 120) {
        return { x, y, props: f.properties };
      }
    }
    return null;
  },
};
true
"""


# Historical windows for the REPLAY and ANALYTICS shots (Edmonton local time).
# Recorded history is append-only, so these stay valid while the database lives.
REPLAY_WINDOW = ("2026-09-12T08:00", "2026-09-12T08:10")
ANALYTICS_WINDOW = ("2026-09-12T06:00", "2026-09-12T12:00")


async def set_datetime(page: Page, aria_label: str, value: str):
    ok = await page.js(f"""(() => {{
      const input = document.querySelector('input[aria-label="{aria_label}"]');
      if (!input) return false;
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      setter.call(input, {json.dumps(value)});
      input.dispatchEvent(new Event('input', {{ bubbles: true }}));
      input.dispatchEvent(new Event('change', {{ bubbles: true }}));
      return true;
    }})()""")
    assert ok, f"no input {aria_label}"
    await asyncio.sleep(0.5)


async def boot(page: Page, width: int, height: int, path: str = ""):
    await page.viewport(width, height)
    # datetime-local inputs use the browser's zone; the app displays Edmonton time.
    await page.send("Emulation.setTimezoneOverride", {"timezoneId": "America/Edmonton"})
    await page.goto(BASE + path)
    await page.wait_for("document.readyState === 'complete'", label="document")
    await page.js(HELPERS)
    await page.wait_for("document.querySelector('.maplibregl-ctrl-attrib')", timeout=60, label="basemap load")
    await page.wait_for("document.querySelectorAll('.route-row').length > 10", timeout=60, label="route list")
    await page.wait_for("document.querySelector('.issue-list button')", timeout=60, label="priority queue")


async def settle(page: Page, seconds: float = 2.5):
    # Wait for map tiles/animations to finish, then park the mouse.
    await page.move_mouse_away()
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if await page.js("(() => { const m = window.__tp.map(); return m ? (m.loaded() && !m.isMoving()) : true; })()"):
                break
        except Exception:
            pass
        await asyncio.sleep(0.3)
    await asyncio.sleep(seconds)


async def mode(page: Page, name: str):
    assert await page.js(f"window.__tp.clickText('.primary-modes button', {json.dumps(name)})"), name


async def select_live_route(page: Page, label: str):
    await page.js(f"window.__tp.setText('#route-search', {json.dumps(label)})")
    await asyncio.sleep(0.4)
    ok = await page.js(f"window.__tp.clickText('.route-list .route-row', {json.dumps(label)})")
    assert ok, f"route {label} not found"
    await page.wait_for("document.querySelector('.route-detail h2')", label="route detail")
    await page.js("window.__tp.setText('#route-search', '')")


async def scene_live(page, out, w, h):
    await settle(page, 3)
    await page.screenshot(out / "live-network.png")


async def scene_live_route(page, out, w, h):
    await select_live_route(page, "002")
    await page.wait_for("document.querySelector('.operations-reason') && !document.querySelector('.operations-reason').textContent.startsWith('Loading')", label="operations")
    await settle(page, 3)
    point = await page.js("JSON.stringify(window.__tp.featureScreenPoint('live-vehicles-dot'))")
    point = json.loads(point) if point else None
    if point:
        await page.click(point["x"], point["y"])
        await asyncio.sleep(0.8)
    await page.move_mouse_away()
    await asyncio.sleep(0.8)
    await page.screenshot(out / "live-route-popup.png")


async def scene_replay(page, out, w, h):
    await select_live_route(page, "004")
    await mode(page, "Replay")
    await page.wait_for("document.querySelector('.replay-timeline') && !document.querySelector('.replay-timeline').disabled", timeout=60, label="replay window")
    await page.wait_for("document.querySelectorAll('.network-intelligence .issue-list button').length > 0", timeout=60, label="replay vehicles")
    # A genuinely earlier window, not the last five minutes before "now".
    await set_datetime(page, "Replay start", REPLAY_WINDOW[0])
    await set_datetime(page, "Replay end", REPLAY_WINDOW[1])
    await page.wait_for("(document.querySelector('.network-intelligence .timeline-labels')?.textContent || '').includes('8:00:00')", timeout=60, label="replay window loaded")
    await page.wait_for("document.querySelectorAll('.network-intelligence .issue-list button').length > 0", timeout=60, label="replay vehicles in window")
    # Scrub to mid-window so the playhead is visibly inside the recording.
    await page.js("window.__tp.setRange ? (() => { const i = document.querySelector('.replay-timeline'); const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set; s.call(i,'620'); i.dispatchEvent(new Event('input',{bubbles:true})); return true; })() : false")
    await settle(page, 3)
    await page.screenshot(out / "replay.png")


async def scene_analytics(page, out, w, h):
    await select_live_route(page, "004")
    await mode(page, "Analytics")
    await page.wait_for("document.querySelector('.analytics-grid') || document.querySelector('.analytics-empty.insufficient')", timeout=90, label="analytics")
    # A six-hour morning window: long enough to read as history, under the API's
    # 10,000-observation cap for this route. End first, so no intermediate range
    # exceeds the cap.
    await set_datetime(page, "Reliability analysis end", ANALYTICS_WINDOW[1])
    await set_datetime(page, "Reliability analysis start", ANALYTICS_WINDOW[0])
    await page.wait_for("document.querySelectorAll('.analytics-grid .timeline-bar-slot').length >= 6", timeout=90, label="six-hour analytics")
    await settle(page, 3)
    await page.screenshot(out / "analytics.png")


async def open_scenarios(page, key_label: str | None = None):
    await mode(page, "Scenarios")
    await page.wait_for("document.querySelector('.scenario-route-list .route-row')", timeout=60, label="scenario routes")
    await page.wait_for("document.querySelector('.estimate-grid')", timeout=60, label="scenario estimate")
    if key_label:
        await page.js(f"window.__tp.setText('#scenario-search', {json.dumps(key_label)})")
        await asyncio.sleep(0.4)
        ok = await page.js(f"(() => {{ const b = [...document.querySelectorAll('.scenario-route-list .route-row')].find(n => n.title === 'Route {key_label}'); if (!b) return false; b.click(); return true; }})()")
        assert ok, f"scenario route {key_label}"
        await page.wait_for(f"document.querySelector('.scenario-column h2') && document.querySelector('.scenario-column h2').textContent.includes('Route {key_label}')", label="scenario baseline")
        await page.js("window.__tp.setText('#scenario-search', '')")


async def wait_estimate(page):
    await asyncio.sleep(0.6)
    await page.wait_for("!document.querySelector('.scenario-column.pending')", label="estimate settled")
    await asyncio.sleep(0.4)


async def scene_scenarios_default(page, out, w, h):
    await open_scenarios(page)
    await wait_estimate(page)
    await settle(page, 3)
    await page.screenshot(out / "scenarios-default.png")


async def scene_scenario_a(page, out, w, h):
    await open_scenarios(page, "002")
    await page.js("window.__tp.setRange('scenario-Route length', 15.6)")
    await page.js("window.__tp.setRange('scenario-Stops', 48)")
    await wait_estimate(page)
    await settle(page, 3)
    await page.screenshot(out / "scenario-design-change.png")


async def scene_scenario_b(page, out, w, h):
    await open_scenarios(page, "002")
    await page.js("window.__tp.setRange('scenario-Peak headway', 8)")
    await wait_estimate(page)
    await settle(page, 3)
    await page.screenshot(out / "scenario-frequency-change.png")


async def scene_scenario_unchanged(page, out, w, h):
    await open_scenarios(page, "002")
    await wait_estimate(page)
    await settle(page, 3)
    await page.screenshot(out / "scenario-unchanged.png")


async def element_clip(page, selector):
    import json as _json
    return _json.loads(await page.js(f"JSON.stringify((() => {{ const r = document.querySelector({_json.dumps(selector)}).getBoundingClientRect(); return {{x: r.left, y: r.top, width: r.width, height: r.height}}; }})())"))


async def scene_scenario_a_detail(page, out, w, h):
    await open_scenarios(page, "002")
    await page.js("window.__tp.setRange('scenario-Route length', 15.6)")
    await page.js("window.__tp.setRange('scenario-Stops', 48)")
    await wait_estimate(page)
    # Scroll the estimate column to its confidence explanation and the model
    # card to its limits, which sit below the fold of the band.
    await page.js("(() => { const cols = document.querySelectorAll('.scenario-band .scenario-column'); cols[0].scrollTop = 10000; cols[1].scrollTop = 10000; cols[2].scrollTop = 10000; return true; })()")
    await settle(page, 1.5)
    await page.screenshot(out / "scenario-design-change-detail.png")


async def scene_map_focus(page, out, w, h):
    await select_live_route(page, "004")
    await page.wait_for("document.querySelector('.operations-reason') && !document.querySelector('.operations-reason').textContent.startsWith('Loading')", label="operations")
    await settle(page, 3)
    clip = await element_clip(page, ".map")
    band = await element_clip(page, ".network-intelligence")
    clip["height"] = min(clip["height"], band["y"] - clip["y"])
    await page.screenshot(out / "map-live-route.png", clip=clip)


async def scene_scenario_band(page, out, w, h):
    # A taller window gives the comparison band room to show whole panels
    # instead of cutting their scrolling content mid-line.
    await page.viewport(w, 1860)
    await asyncio.sleep(1.5)
    await open_scenarios(page, "002")
    await page.js("window.__tp.setRange('scenario-Route length', 15.6)")
    await page.js("window.__tp.setRange('scenario-Stops', 48)")
    await wait_estimate(page)
    await settle(page, 1.5)
    await page.screenshot(out / "scenario-comparison-panel.png", clip=await element_clip(page, ".scenario-band"))


async def scene_research(page, out, w, h):
    await page.goto(BASE + "research/model-lab")
    await page.wait_for("document.readyState === 'complete'")
    await asyncio.sleep(5)
    await page.screenshot(out / "research-model-lab.png")


SCENES = {
    "scenario_a_detail": scene_scenario_a_detail,
    "map_focus": scene_map_focus,
    "scenario_band": scene_scenario_band,
    "research": scene_research,
    "live": scene_live,
    "live_route": scene_live_route,
    "replay": scene_replay,
    "analytics": scene_analytics,
    "scenarios_default": scene_scenarios_default,
    "scenario_a": scene_scenario_a,
    "scenario_b": scene_scenario_b,
    "scenario_unchanged": scene_scenario_unchanged,
}


async def main():
    out = Path(sys.argv[1])
    width, height = int(sys.argv[2]), int(sys.argv[3])
    names = sys.argv[4:] or list(SCENES)
    out.mkdir(parents=True, exist_ok=True)
    for index, name in enumerate(names):
        # A fresh browser profile per scene: no carried-over state, no GPU
        # context exhaustion from repeated reloads.
        chrome = Chrome(port=9333 + index)
        try:
            page = await Page.open(chrome.ws_url)
            await boot(page, width, height)
            t = time.time()
            await SCENES[name](page, out, width, height)
            errors = [c for c in page.console if c.startswith(("error", "exception"))]
            print(f"{name}: ok in {time.time() - t:.1f}s; console errors: {errors[:3]}")
            await page.close()
        except Exception as error:
            print(f"{name}: FAILED {error!r}")
        finally:
            chrome.close()


if __name__ == "__main__":
    asyncio.run(main())
