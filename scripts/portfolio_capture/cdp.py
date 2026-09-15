"""Minimal Chrome DevTools Protocol driver for clean, repeatable screenshots.

Launches headless Chrome (or Edge) with a throwaway profile, so no extension,
cache or stored state can leak into a capture, and drives it over the DevTools
websocket. Needs only ``websockets``, which ``uvicorn[standard]`` installs.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets


def _find_browser() -> str:
    candidates = [
        os.environ.get("TRANSITPULSE_CHROME", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise SystemExit("Chrome or Edge not found; set TRANSITPULSE_CHROME to the browser executable.")


CHROME = _find_browser()


class Chrome:
    def __init__(self, port: int = 9333, extra_flags: list[str] | None = None):
        self.port = port
        self.profile = tempfile.mkdtemp(prefix="tp-chrome-")
        flags = [
            CHROME,
            "--headless=new",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={self.profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "--window-size=1920,1080",
            "--enable-unsafe-swiftshader",
            "--disable-extensions",
            "--proxy-server=direct://",
            "--proxy-bypass-list=*",
            *(extra_flags or []),
            "about:blank",
        ]
        self.proc = subprocess.Popen(flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.time() + 20
        while True:
            try:
                with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
                    targets = json.load(r)
                pages = [t for t in targets if t.get("type") == "page"]
                if pages:
                    self.ws_url = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                if time.time() > deadline:
                    raise
                time.sleep(0.3)

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)


class Page:
    def __init__(self, ws):
        self.ws = ws
        self.ids = itertools.count(1)
        self.pending: dict[int, asyncio.Future] = {}
        self.console: list[str] = []
        self.reader = asyncio.create_task(self._read())

    @classmethod
    async def open(cls, ws_url: str) -> "Page":
        ws = await websockets.connect(ws_url, max_size=64 * 1024 * 1024)
        page = cls(ws)
        await page.send("Page.enable")
        await page.send("Runtime.enable")
        return page

    async def _read(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            if "id" in msg and msg["id"] in self.pending:
                fut = self.pending.pop(msg["id"])
                if "error" in msg:
                    fut.set_exception(RuntimeError(msg["error"]))
                else:
                    fut.set_result(msg.get("result", {}))
            elif msg.get("method") == "Runtime.consoleAPICalled":
                args = msg["params"].get("args", [])
                self.console.append(
                    f"{msg['params'].get('type')}: " + " ".join(str(a.get("value", a.get("description", ""))) for a in args)
                )
            elif msg.get("method") == "Runtime.exceptionThrown":
                self.console.append("exception: " + json.dumps(msg["params"]["exceptionDetails"].get("exception", {}).get("description", ""))[:400])

    async def send(self, method: str, params: dict | None = None, timeout: float = 60):
        mid = next(self.ids)
        fut = asyncio.get_running_loop().create_future()
        self.pending[mid] = fut
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        return await asyncio.wait_for(fut, timeout)

    async def viewport(self, width: int, height: int):
        await self.send(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
        )

    async def goto(self, url: str):
        await self.send("Page.navigate", {"url": url})

    async def js(self, expression: str, timeout: float = 60):
        result = await self.send(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
            timeout=timeout,
        )
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"].get("exception", {}).get("description", result["exceptionDetails"]))
        return result.get("result", {}).get("value")

    async def wait_for(self, expression: str, timeout: float = 45, label: str = ""):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if await self.js(f"Boolean({expression})"):
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.4)
        raise TimeoutError(f"timed out waiting for {label or expression}")

    async def click(self, x: float, y: float):
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            await self.send(
                "Input.dispatchMouseEvent",
                {"type": kind, "x": x, "y": y, "button": "left" if kind != "mouseMoved" else "none", "clickCount": 1},
            )

    async def move_mouse_away(self):
        await self.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 1, "y": 1})

    async def screenshot(self, path: Path, clip: dict | None = None):
        params = {"format": "png", "captureBeyondViewport": False}
        if clip:
            params["clip"] = {**clip, "scale": 1}
        data = await self.send("Page.captureScreenshot", params)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data["data"]))
        return path

    async def close(self):
        self.reader.cancel()
        await self.ws.close()
