"""Local adapter for the installed yang0/handraw-style skill.

The Skill package stays self-contained under optional/skills/handraw-style; this
module only reads its JSON metadata and builds prompts for the image module.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PACKAGE_CANDIDATES = [
    ROOT / "optional" / "skills" / "handraw-style",
    Path(r"K:\CONFI\_handraw_tmp"),
]
_cache: dict[str, object] = {}


def package_root() -> Path | None:
    for p in PACKAGE_CANDIDATES:
        if (p / "skills" / "handdraw-style-prompter" / "references" / "styles.json").is_file():
            return p
    return None


def _references() -> Path | None:
    p = package_root()
    return p / "skills" / "handdraw-style-prompter" / "references" if p else None


def _load(name: str):
    key = f"load:{name}"
    if key in _cache:
        return _cache[key]
    ref = _references()
    if not ref:
        return []
    data = json.loads((ref / name).read_text(encoding="utf-8"))
    _cache[key] = data
    return data


def styles() -> list[dict]:
    out = []
    for s in _load("styles.json"):
        out.append({
            "number": s.get("number"),
            "group": s.get("group"),
            "reference": s.get("reference"),
            "generation_name": s.get("generation_name"),
            "traits": s.get("traits"),
        })
    return out


def colors() -> list[dict]:
    return [{
        "id": c.get("id"),
        "name_zh": c.get("name_zh"),
        "name_en": c.get("name_en"),
        "category_zh": c.get("category_zh"),
        "prompt_zh": c.get("prompt_zh"),
        "prompt_en": c.get("prompt_en"),
    } for c in _load("colors.json")]


def _normalize_id(value: str, prefix: str = "") -> str:
    raw = str(value or "").strip().upper().lstrip("#")
    if prefix and re.fullmatch(r"\d+", raw):
        return f"{prefix}{int(raw):03d}"
    return raw


def resolve_style(value: str) -> dict:
    key = _normalize_id(value)
    for s in styles():
        if str(s["number"]).upper() == key or str(s["number"]).upper().endswith(key):
            return s
    raise ValueError(f"unknown style: {value}")


def resolve_color(value: str) -> dict | None:
    key = _normalize_id(value, "C-")
    if not key:
        return None
    for c in colors():
        if str(c["id"]).upper() == key:
            return c
    raise ValueError(f"unknown color: {value}")


def build_prompt(style_ids: list[str], color_ids: list[str] | None,
                 theme: str, lang: str = "zh") -> dict:
    if not style_ids:
        raise ValueError("style is required")
    chosen = [resolve_style(s) for s in style_ids]
    chosen_colors = [resolve_color(c) for c in (color_ids or []) if c]
    theme = (theme or "").strip()
    is_zh = str(lang).lower().startswith("zh")
    parts: list[str] = []
    for s in chosen:
        if is_zh:
            parts.append(f"{s['generation_name']}（参考 {s['reference']}，编号 {s['number']}）")
            if s.get("traits"):
                parts.append(str(s["traits"]))
        else:
            parts.append(f"{s['generation_name']} (after {s['reference']}, style {s['number']})")
            if s.get("traits"):
                parts.append(str(s["traits"]))
    for c in chosen_colors:
        if c:
            parts.append(str(c.get("prompt_zh" if is_zh else "prompt_en") or ""))
    if theme:
        parts.append(("画面主题：" + theme) if is_zh else ("Subject: " + theme))
    else:
        parts.append("【请在此输入画面主题】" if is_zh else "[Enter the image theme here]")
    prompt = "；".join(p for p in parts if p) + ("。" if is_zh else ".")
    return {
        "prompt": prompt,
        "styles": chosen,
        "colors": chosen_colors,
        "lang": "zh" if is_zh else "en",
    }
