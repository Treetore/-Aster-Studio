"""模型切换释放策略。

策略：每次生成完成后记录所用模型族；下一条指令若模型族不同，
先调用 ComfyUI /free 卸载所有模型并释放显存，再提交新任务。
同族（如 moss_image ↔ moss_image_edit、qwen_image ↔ qwen_image_edit）不释放。

只调用 /free，不重启服务，不影响隧道/映射。
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

ROOT = Path(r"K:\CONFI\comfy_mcp")
DATA = ROOT / "data"
LOGS = ROOT / "logs"
POLICY_FILE = DATA / "model_policy.json"
STATE_FILE = DATA / "model_policy_state.json"
LOG_FILE = LOGS / "model_policy.log"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _log(msg: str) -> None:
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{_now()}] {msg}\n")
    except Exception:
        pass


def policy_enabled() -> bool:
    try:
        cfg = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
        return bool(cfg.get("release_on_model_switch", True))
    except Exception:
        return True


def family_for(template: str, kind: str = "", params: dict | None = None) -> str:
    """把模板归到“同一个底层模型”的族；族不同才需要卸载。"""
    template = (template or "").lower()
    kind = (kind or "").lower()
    if template.startswith("minimax_h3_r2v"):
        fam = "h3_ref2va"
    elif template.startswith("minimax_h3") or kind.startswith("video") or kind == "image_h3":
        fam = "h3_fl2va"
    elif "moss" in template or kind.startswith("image_moss"):
        fam = "moss"
    elif "qwen" in template or kind.startswith("image_qwen"):
        fam = "qwen_image"
    else:
        fam = kind or template
    unet = str((params or {}).get("unet_name") or "").strip()
    if unet:
        fam += "|" + unet
    return fam


def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def mark_done(template: str, kind: str = "", params: dict | None = None) -> dict:
    st = {"family": family_for(template, kind, params), "template": template,
          "updated_at": _now()}
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return st


def maybe_release(free_fn: Callable[[], object] | None,
                  stats_fn: Callable[[], dict] | None,
                  template: str, kind: str = "", params: dict | None = None) -> dict:
    """若模型族变化则释放；返回 {released, from, to, wait_s}。"""
    if not policy_enabled() or free_fn is None:
        return {"released": False, "reason": "disabled"}
    new_fam = family_for(template, kind, params)
    last = read_state()
    old_fam = str(last.get("family") or "")
    if not old_fam or old_fam == new_fam:
        return {"released": False, "family": new_fam, "from": old_fam}
    t0 = time.time()
    _log(f"模型切换 {old_fam} -> {new_fam}（{last.get('template')} -> {template}），卸载全部模型")
    try:
        free_fn()
    except Exception as exc:  # noqa: BLE001
        _log(f"释放失败: {exc}")
        return {"released": False, "error": str(exc), "from": old_fam, "to": new_fam}
    # 等待显存真正释放（最多 20s）
    waited = 0.0
    if stats_fn is not None:
        for _ in range(20):
            time.sleep(1)
            waited += 1
            try:
                st = stats_fn() or {}
                dev = (st.get("devices") or [{}])[0]
                free = dev.get("vram_free")
                if isinstance(free, (int, float)) and free >= 900 * 1024 * 1024:
                    break
            except Exception:
                break
    _log(f"已释放（等待 {waited:.0f}s，来源 {old_fam}）")
    return {"released": True, "from": old_fam, "to": new_fam,
            "wait_s": round(time.time() - t0, 1)}
