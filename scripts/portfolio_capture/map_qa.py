"""Fresh-profile map QA for TransitPulse. Prints PASS/FAIL per check.

Usage (with the app running): python scripts/portfolio_capture/map_qa.py

Checks the basemap worker, layer toggles, route framing in every mode, popups,
and, in a second browser with the CARTO tile host blocked by DNS, that the
no-basemap state is reported accurately (notice shown, no attribution, transit
data drawn).
"""

import asyncio
import json
import tempfile
from pathlib import Path

from cdp import Chrome, Page
from scenes import HELPERS, BASE, boot, mode, select_live_route, settle

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((name, bool(ok), detail))


VIS = "(id) => window.__tp.map().getLayoutProperty(id, 'visibility') ?? 'visible'"


async def route_visible_fraction(page: Page) -> dict:
    return json.loads(await page.js("""(() => {
      const m = window.__tp.map();
      const canvas = m.getCanvas().getBoundingClientRect();
      const band = document.querySelector('.network-intelligence, .analytics-workspace');
      const visibleBottom = band ? Math.min(canvas.bottom, band.getBoundingClientRect().top) : canvas.bottom;
      const feats = m.querySourceFeatures('selected-route');
      let inside = 0, total = 0;
      for (const f of feats) {
        const lines = f.geometry.type === 'MultiLineString' ? f.geometry.coordinates : [f.geometry.coordinates];
        for (const line of lines) for (const c of line) {
          const p = m.project(c); total++;
          const x = canvas.left + p.x, y = canvas.top + p.y;
          if (x >= canvas.left && x <= canvas.right && y >= canvas.top && y <= visibleBottom) inside++;
        }
      }
      return JSON.stringify({ inside, total, fraction: total ? inside / total : 0 });
    })()"""))


async def main():
    chrome = Chrome(port=9501)
    try:
        page = await Page.open(chrome.ws_url)
        await boot(page, 1920, 1080)
        check("basemap load event fired (attribution present)", await page.js("!!document.querySelector('.maplibregl-ctrl-attrib')"))
        check("no 'No basemap' notice", await page.js("!document.querySelector('.map-basemap-notice')"))
        check("fallback SVG not drawn over basemap", await page.js("!document.querySelector('.transit-map-fallback')"))
        worker = await page.js("fetch('/maplibre/maplibre-gl-worker.mjs').then(r => r.status + ' ' + r.headers.get('content-type'))")
        check("worker module served as JavaScript", "javascript" in (worker or ""), worker)

        # Controls vs analysis band.
        overlap = json.loads(await page.js("""JSON.stringify((() => {
          const c = document.querySelector('.map-controls').getBoundingClientRect();
          const b = document.querySelector('.network-intelligence').getBoundingClientRect();
          return { controlsBottom: c.bottom, bandTop: b.top };
        })())"""))
        check("map controls sit above the analysis band", overlap["controlsBottom"] <= overlap["bandTop"], json.dumps(overlap))

        # Vehicles and bearing.
        await settle(page, 1)
        veh = json.loads(await page.js("""JSON.stringify((() => {
          const m = window.__tp.map();
          const all = m.querySourceFeatures('live-vehicles');
          const withBearing = all.filter(f => f.properties.bearing !== undefined && f.properties.bearing !== null).length;
          const headings = m.queryRenderedFeatures({ layers: ['live-vehicles-heading'] }).length;
          const dots = m.queryRenderedFeatures({ layers: ['live-vehicles-dot'] }).length;
          return { source: all.length, withBearing, headings, dots };
        })())"""))
        check("live vehicles rendered", veh["dots"] > 50, json.dumps(veh))
        check("heading chevrons only where bearing exists", veh["headings"] <= veh["withBearing"] or veh["withBearing"] == 0, json.dumps(veh))

        # Layer toggles.
        for label, layers in (("Routes", ["network-routes-line", "selected-route-line"]), ("Stops", ["selected-stops-dot"]), ("Vehicles", ["live-vehicles-dot", "live-vehicles-heading"])):
            await page.js(f"window.__tp.clickText('.map-control-group.layers button', '{label}')")
            await asyncio.sleep(0.4)
            off = await page.js(f"({VIS}) && [{', '.join(repr(l) for l in layers)}].every(id => ({VIS})(id) === 'none')")
            pressed_off = await page.js(f"[...document.querySelectorAll('.map-control-group.layers button')].find(b => b.textContent === '{label}').getAttribute('aria-pressed') === 'false'")
            await page.js(f"window.__tp.clickText('.map-control-group.layers button', '{label}')")
            await asyncio.sleep(0.4)
            on = await page.js(f"[{', '.join(repr(l) for l in layers)}].every(id => ({VIS})(id) === 'visible')")
            check(f"{label} toggle hides then restores its layers", off and on and pressed_off)

        # Selected route hierarchy + fit.
        await select_live_route(page, "004")
        await settle(page, 1.5)
        frac = await route_visible_fraction(page)
        check("selecting a route frames it inside the visible map", frac["fraction"] > 0.97, json.dumps(frac))
        widths = json.loads(await page.js("""JSON.stringify((() => { const m = window.__tp.map();
          return { selectedZoom12: m.getPaintProperty('selected-route-line','line-width'), network: m.getPaintProperty('network-routes-line','line-width') }; })())"""))
        check("selected route drawn above network (layer order)", await page.js("(() => { const ids = window.__tp.map().getStyle().layers.map(l => l.id); return ids.indexOf('selected-route-line') > ids.indexOf('network-routes-line'); })()"))
        await page.js("window.__tp.clickText('.map-control-group button', 'Fit network')")
        await settle(page, 1)
        zoom_network = await page.js("window.__tp.map().getZoom()")
        bounds_ok = await page.js("(() => { const b = window.__tp.map().getBounds(); return b.getWest() < -113.8 && b.getEast() > -113.25; })()")
        check("Fit network frames the Edmonton network", bounds_ok, f"zoom {zoom_network:.2f}")
        await page.js("window.__tp.clickText('.map-control-group button', 'Fit route')")
        await settle(page, 1)
        frac2 = await route_visible_fraction(page)
        zoom_route = await page.js("window.__tp.map().getZoom()")
        check("Fit route re-frames the selected route", frac2["fraction"] > 0.97 and zoom_route > zoom_network, f"{json.dumps(frac2)} zoom {zoom_route:.2f}")

        # One popup per click.
        point = await page.js("JSON.stringify(window.__tp.featureScreenPoint('selected-stops-dot'))")
        if point and point != "null":
            p = json.loads(point)
            await page.click(p["x"], p["y"])
            await asyncio.sleep(0.6)
            await page.click(p["x"], p["y"])
            await asyncio.sleep(0.6)
            count = await page.js("document.querySelectorAll('.maplibregl-popup').length")
            check("clicking features leaves exactly one popup", count == 1, f"popups={count}")
        else:
            check("clicking features leaves exactly one popup", False, "no stop found on screen")

        # ANALYTICS: shorter canvas, taller workspace; route and controls must both fit.
        await mode(page, "Analytics")
        await page.wait_for("document.querySelector('.analytics-grid') || document.querySelector('.analytics-empty.insufficient')", timeout=90)
        await settle(page, 2)
        frac_a = await route_visible_fraction(page)
        check("ANALYTICS frames the route in its shorter map", frac_a["fraction"] > 0.97, json.dumps(frac_a))
        ctl = json.loads(await page.js("JSON.stringify((() => { const c = document.querySelector('.map-controls').getBoundingClientRect(); const m = document.querySelector('.map').getBoundingClientRect(); const b = document.querySelector('.analytics-workspace').getBoundingClientRect(); return {controlsTop: c.top, controlsBottom: c.bottom, mapTop: m.top, bandTop: b.top}; })())"))
        check("ANALYTICS map controls visible above the workspace", ctl["controlsBottom"] <= ctl["bandTop"] and ctl["controlsTop"] >= ctl["mapTop"], json.dumps(ctl))
        attr = json.loads(await page.js("JSON.stringify((() => { const a = document.querySelector('.maplibregl-ctrl-attrib').getBoundingClientRect(); const c = document.querySelector('.map-controls').getBoundingClientRect(); return {overlap: !(a.right < c.left || a.left > c.right || a.bottom < c.top || a.top > c.bottom)}; })())"))
        check("attribution does not collide with map controls", not attr["overlap"], json.dumps(attr))

        # Mode emphasis.
        await mode(page, "Scenarios")
        await page.wait_for("document.querySelector('.estimate-grid')", timeout=60)
        await settle(page, 1.5)
        check("SCENARIOS hides vehicles", await page.js(f"({VIS})('live-vehicles-dot') === 'none'"))
        frac3 = await route_visible_fraction(page)
        check("SCENARIOS frames its route above the band", frac3["fraction"] > 0.97, json.dumps(frac3))
        await page.close()
    finally:
        chrome.close()

    # No-basemap fallback: block the worker so vector tiles cannot parse.
    # DNS-level: tile and glyph fetches run inside the MapLibre worker, which a
    # page-level URL block does not reach.
    chrome = Chrome(port=9502, extra_flags=["--host-resolver-rules=MAP *.cartocdn.com ~NOTFOUND, MAP tiles.basemaps.cartocdn.com ~NOTFOUND"])
    try:
        page = await Page.open(chrome.ws_url)
        await page.viewport(1440, 900)
        await page.goto(BASE)
        await page.wait_for("document.readyState === 'complete'")
        await page.js(HELPERS)
        await page.wait_for("document.querySelectorAll('.route-row').length > 10", timeout=60)
        await asyncio.sleep(8)
        check("fallback: notice shown when basemap cannot load", await page.js("!!document.querySelector('.map-basemap-notice')"))
        check("fallback: no CARTO/OSM attribution without a basemap", await page.js("!document.querySelector('.maplibregl-ctrl-attrib')"))
        check("fallback: measured network still drawn", await page.js("document.querySelectorAll('.fallback-network path').length > 50 || (window.__tp && window.__tp.map() && window.__tp.map().queryRenderedFeatures({layers:['network-routes-line']}).length > 50)"))
        notice = json.loads(await page.js("JSON.stringify((() => { const n = document.querySelector('.map-basemap-notice').getBoundingClientRect(); const c = document.querySelector('.map-controls').getBoundingClientRect(); return {overlap: !(n.right < c.left || n.left > c.right || n.bottom < c.top || n.top > c.bottom)}; })())"))
        check("fallback: notice does not collide with map controls", not notice["overlap"], json.dumps(notice))
        await page.screenshot(Path(tempfile.gettempdir()) / "transitpulse-mapqa-fallback.png")
        await page.close()
    finally:
        chrome.close()

    width = max(len(n) for n, _, _ in results)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {detail}")
    print(f"{sum(ok for _, ok, _ in results)}/{len(results)} checks passed")


asyncio.run(main())
