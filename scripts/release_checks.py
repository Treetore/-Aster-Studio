"""Release smoke / integration / system checks (no GPU generation).

Usage:
    python scripts/release_checks.py [--base http://127.0.0.1:8190]

The script assumes ComfyUI and the Web service are already running. It never
submits a generation job, so it is safe to run before packaging a release.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS: list[dict] = []


def record(group: str, name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"group": group, "name": name, "ok": bool(ok), "detail": detail})
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {group}/{name}: {detail}")


def get(base: str, path: str, timeout: float = 30) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(base + path, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc).encode()


def post_json(base: str, path: str, payload: dict, method: str = "POST") -> tuple[int, dict]:
    req = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(), method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": str(exc)}


def check_http(base: str) -> None:
    for path, need in (("/api/health", b"aster-studio"), ("/studio", b"Aster"),
                       ("/static/studio.css", b""), ("/static/studio.js", b""),
                       ("/api/setup/status", b"first_run"), ("/api/doctor", b"ok")):
        code, body = get(base, path)
        record("smoke", path, code == 200 and need in body, f"HTTP {code}, {len(body)} bytes")
    code, body = get(base, "/api/models/market", timeout=60)
    try:
        data = json.loads(body)
        record("integration", "model-market", code == 200 and data.get("count", 0) >= 500,
               f"{data.get('count')} models")
    except Exception:  # noqa: BLE001
        record("integration", "model-market", False, f"HTTP {code}")


def check_workspace(base: str) -> None:
    code, data = post_json(base, "/api/projects", {"name": "release-check", "description": "smoke"})
    pid = (data.get("project") or {}).get("id")
    record("integration", "project-create", code == 200 and data.get("ok") and bool(pid), str(data)[:120])
    if pid:
        req = urllib.request.Request(base + f"/api/projects/{pid}", method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                deleted = json.loads(r.read()).get("ok")
            record("integration", "project-delete", bool(deleted), "")
        except Exception as exc:  # noqa: BLE001
            record("integration", "project-delete", False, str(exc)[:120])
    for path in ("/api/presets", "/api/i18n?lang=en", "/api/queue"):
        code, body = get(base, path)
        record("integration", path, code == 200, f"HTTP {code}, {len(body)} bytes")


def check_workflow() -> None:
    sys.path.insert(0, str(ROOT))
    import system_profile
    import workflow
    ok = True
    for name in workflow.TEMPLATES:
        api = workflow.load_template(name)
        if not isinstance(api, dict) or not api:
            ok = False
    record("integration", "templates-load", ok, f"{len(workflow.TEMPLATES)} templates")
    # high mode permits 15s at 720p; low mode blocks it
    original = (ROOT / "data" / "host_mode.json")
    backup = original.read_text(encoding="utf-8") if original.is_file() else None
    try:
        system_profile.set_mode_override("high")
        system_profile._cache["data"] = None
        resolved = workflow.resolve("minimax_h3_t2v", {
            "prompt": "release check", "width": 1280, "height": 736,
            "duration_s": 15, "steps": 8, "optimize": "max"})["resolved"]
        record("system", "high-mode-15s", resolved.get("length", 0) > 0, f"length={resolved.get('length')}")
        system_profile.set_mode_override("low_vram")
        system_profile._cache["data"] = None
        blocked = False
        try:
            workflow.resolve("minimax_h3_t2v", {
                "prompt": "release check", "width": 1280, "height": 736,
                "duration_s": 15, "steps": 8, "optimize": "max"})
        except ValueError:
            blocked = True
        record("system", "low-mode-15s-blocked", blocked, "expected segmented routing")
    finally:
        if backup is None:
            original.unlink(missing_ok=True)
        else:
            original.write_text(backup, encoding="utf-8")
        system_profile._cache["data"] = None


def check_startup() -> None:
    ps = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
          str(ROOT / "start_all.ps1"), "-DryRun"]
    try:
        p = subprocess.run(ps, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        out = (p.stdout or "") + (p.stderr or "")
        record("smoke", "start-all-dry-run", p.returncode == 0 and "ComfyUI root" in out,
               f"exit={p.returncode}")
    except Exception as exc:  # noqa: BLE001
        record("smoke", "start-all-dry-run", False, str(exc)[:120])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8190")
    args = ap.parse_args()
    check_http(args.base)
    check_workspace(args.base)
    check_workflow()
    check_startup()
    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    (ROOT / "data" / "release_checks.json").write_text(
        json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
