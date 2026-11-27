"""DeepSeek agent bridge for handraw-style prompt optimization.

The API key is never committed: it can come from DEEPSEEK_API_KEY, the project
.env file, or data/deepseek_key.txt written by the local settings UI.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
KEY_FILE = ROOT / "data" / "deepseek_key.txt"
ZH_CACHE = ROOT / "data" / "handraw_zh.json"
BASE = "https://api.deepseek.com"
MODEL = "deepseek-chat"


def get_key(explicit: str = "") -> str:
    key = (explicit or "").strip()
    if key:
        return key
    env = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if env:
        return env
    try:
        return KEY_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def masked() -> str:
    key = get_key()
    return (key[:4] + "…" + key[-4:]) if len(key) > 12 else ("已配置" if key else "")


def save_key(key: str) -> None:
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text((key or "").strip(), encoding="utf-8")


def clear_key() -> None:
    KEY_FILE.unlink(missing_ok=True)


def _chat(messages: list[dict], key: str, timeout: int = 90) -> str:
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")
    r = requests.post(
        BASE + "/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": MODEL, "messages": messages, "temperature": 0.7, "max_tokens": 1200},
        timeout=timeout,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"DeepSeek HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()


def optimize(theme: str, style: dict, color: dict | None, lang: str = "zh", key: str = "") -> dict:
    key = get_key(key)
    is_zh = str(lang).lower().startswith("zh")
    style_text = (
        f"编号 {style.get('number')}；作者/参考 {style.get('reference')}；"
        f"风格名 {style.get('generation_name')}；正向特征 {style.get('traits')}"
    )
    color_text = ""
    if color:
        color_text = str(color.get("prompt_zh" if is_zh else "prompt_en") or "")
    system = (
        "你是严格的图像提示词优化器。基于已安装的 handraw-style 风格库，"
        "把用户的画面主题改写成可直接提交给 ComfyUI 生图模型的完整提示词。"
        "保留用户主题的事实、文字、数量与构图要求；不要添加与原主题冲突的主体；"
        "只输出最终提示词，不要解释、不要 Markdown、不要引号包裹。"
    )
    user = (
        f"目标语言：{'中文' if is_zh else 'English'}\n"
        f"风格资料：{style_text}\n"
        f"主题色：{color_text or '未指定'}\n"
        f"用户主题：{theme or '【未填写主题】'}\n"
        "请输出一段连贯的最终生图提示词。"
    )
    text = _chat([{"role": "system", "content": system}, {"role": "user", "content": user}], key)
    return {"ok": True, "agent": True, "provider": "deepseek", "model": MODEL,
            "prompt": text.strip()}


def translate_names(items: list[dict], key: str = "") -> dict:
    """Translate style generation names to Chinese and cache them.

    items: [{"number": "FA-001", "name": "Playful Deadpan Doodle"}, ...]
    """
    key = get_key(key)
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")
    cache: dict[str, str] = {}
    if ZH_CACHE.is_file():
        try:
            cache = json.loads(ZH_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    pending = [x for x in items if x.get("number") and not cache.get(x["number"])]
    for start in range(0, len(pending), 25):
        batch = pending[start:start + 25]
        payload = json.dumps([{"id": x["number"], "en": x["name"]} for x in batch], ensure_ascii=False)
        system = (
            "你是图像风格名翻译器。把英文风格名翻译成简洁、专业、适合界面展示的中文名。"
            "保留编号与专有名词；不要解释。只输出 JSON 数组，元素为 "
            '{"id":"编号","zh":"中文名"}，不要 Markdown。'
        )
        raw = _chat([
            {"role": "system", "content": system},
            {"role": "user", "content": payload},
        ], key, timeout=120)
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
        try:
            rows = json.loads(raw)
        except Exception:
            m = re.search(r"\[.*\]", raw, re.S)
            rows = json.loads(m.group(0)) if m else []
        for row in rows:
            if isinstance(row, dict) and row.get("id") and row.get("zh"):
                cache[str(row["id"])] = str(row["zh"]).strip()[:80]
    ZH_CACHE.parent.mkdir(parents=True, exist_ok=True)
    ZH_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "translated": len(cache), "total": len(items)}
