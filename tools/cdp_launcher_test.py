"""Headless CDP smoke test for the launcher UI."""
from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import time
from pathlib import Path

import requests
import websockets

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9334
URL = "http://127.0.0.1:8191/"
OUT = Path(r"K:\CONFI\comfy_mcp\logs\launcher_ui.png")
OUT2 = Path(r"K:\CONFI\comfy_mcp\logs\launcher_models.png")


async def main() -> int:
    proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
                             "--user-data-dir=" + str(Path(r"K:\CONFI\comfy_mcp\logs\_chrome_prof2")),
                             "--no-first-run", "--disable-gpu", "--window-size=1400,900", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws_url = None
    for _ in range(40):
        try:
            lst = requests.get(f"http://127.0.0.1:{PORT}/json/list", timeout=2).json()
            page = next((t for t in lst if t.get("type") == "page"), None)
            if page:
                ws_url = page["webSocketDebuggerUrl"]
                break
        except Exception:
            pass
        time.sleep(0.5)
    if not ws_url:
        print("no target"); proc.kill(); return 2
    mid = 0
    async with websockets.connect(ws_url, max_size=20_000_000) as ws:
        async def send(method, params=None):
            nonlocal mid
            mid += 1
            await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == mid:
                    return msg

        async def ev(expr):
            r = await send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
            return (r.get("result") or {}).get("result", {}).get("value")

        await send("Page.enable"); await send("Runtime.enable")
        await send("Page.navigate", {"url": URL})
        await asyncio.sleep(3.0)
        print("title:", await ev("document.title"))
        print("comfy card:", await ev("$('sComfy').textContent"))
        print("gpu:", await ev("$('gName').textContent"))
        print("pills:", await ev("$('pillComfyTxt').textContent + ' | ' + $('pillGpuTxt').textContent"))
        shot = await send("Page.captureScreenshot", {"format": "png"})
        OUT.write_bytes(base64.b64decode(shot["result"]["data"]))

        await ev("document.querySelector('#nav button[data-p=models]').click()")
        await asyncio.sleep(1.5)
        print("models total:", await ev("$('modelTotal').textContent"))
        print("model rows:", await ev("document.querySelectorAll('#modelTable tbody tr').length"))
        shot2 = await send("Page.captureScreenshot", {"format": "png"})
        OUT2.write_bytes(base64.b64decode(shot2["result"]["data"]))
        print("shots:", OUT, OUT2)
    proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
