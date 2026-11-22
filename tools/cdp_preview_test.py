"""Headless CDP smoke test: gallery card -> preview modal -> ArrowRight nav -> wheel zoom."""
from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

import requests
import websockets

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9333
URL = "http://127.0.0.1:8190/studio?tab=gallery"
OUT = Path(r"K:\CONFI\comfy_mcp\logs\cdp_preview.png")


async def main() -> int:
    proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
                             "--user-data-dir=" + str(Path(r"K:\CONFI\comfy_mcp\logs\_chrome_prof")),
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
        print("no cdp target"); proc.kill(); return 2

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

        await send("Page.enable")
        await send("Runtime.enable")
        await send("Page.navigate", {"url": URL})
        await asyncio.sleep(3.0)

        cards = await ev("document.querySelectorAll('#galleryGrid .vcard').length")
        print("cards:", cards)
        await ev("document.querySelector('#galleryGrid .vcard').click()")
        await asyncio.sleep(1.2)
        print("modal on:", await ev("$('modal').classList.contains('on')"))
        print("img display:", await ev("$('modalImg').style.display"))
        print("counter1:", await ev("$('modalCounter').textContent"))
        print("name1:", await ev("$('modalName').textContent"))

        await send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "ArrowRight", "code": "ArrowRight", "windowsVirtualKeyCode": 39})
        await send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "ArrowRight", "code": "ArrowRight", "windowsVirtualKeyCode": 39})
        await asyncio.sleep(0.6)
        print("counter2:", await ev("$('modalCounter').textContent"))
        print("name2:", await ev("$('modalName').textContent"))

        await send("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": 700, "y": 420, "deltaX": 0, "deltaY": -240})
        await asyncio.sleep(0.4)
        print("transform:", await ev("$('modalImg').style.transform"))

        shot = await send("Page.captureScreenshot", {"format": "png"})
        data = base64.b64decode(shot["result"]["data"])
        OUT.write_bytes(data)
        print("screenshot:", OUT, len(data), "bytes")

    proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
