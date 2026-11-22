"""setup/doctor.py + safety.py 离线自检测试。

原则：
* 不依赖 GPU / 模型 / ComfyUI：文件系统用 ``tmp_path``，torch / 端口 / ffmpeg 全部打桩。
* 只断言结构、blocking 语义与 easy/pro 双文案，不打印 emoji（GBK 控制台安全）。
* 不启动 ComfyUI，不生成媒体。
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from comfy_mcp import safety as safety_mod  # noqa: E402
from comfy_mcp.setup import doctor  # noqa: E402

CATALOG_IMAGE = [
    {"id": "qwen-image", "name": "Qwen Image", "kind": "image",
     "target": "diffusion_models", "file": "qwen_image.safetensors"},
]
CATALOG_VIDEO = [
    {"id": "h3-base", "name": "MiniMax H3", "kind": "video",
     "target": "diffusion_models", "file": "minimax_h3.safetensors"},
]
TORCH_OK = {"torch": True, "cuda": True, "torch_version": "2.6.0", "cuda_version": "12.4",
            "device": "Fake GPU", "error": None}
TORCH_NO_TORCH = {"torch": False, "cuda": False, "torch_version": None,
                  "cuda_version": None, "error": "ModuleNotFoundError: No module named 'torch'"}
TORCH_NO_CUDA = {"torch": True, "cuda": False, "torch_version": "2.6.0+cpu",
                 "cuda_version": None, "error": None}


# --------------------------------------------------------------------------- helpers
def build_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    models: list[dict] | None = None,
    selected: list[str] | None = None,
    settings: dict | None = None,
    assets: bool = True,
    comfy_main: bool = True,
    templates: bool = True,
    materialize_models: bool = True,
    data_writable: bool = True,
    ffmpeg: bool = False,
    port: bool = False,
    torch: dict | None = None,
    python_version: tuple[int, int, int] = (3, 12, 4),
) -> dict:
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True, exist_ok=True)
    if assets:
        (root / "webapp").mkdir(exist_ok=True)
        (root / "webapp" / "studio.html").write_text("<html></html>", encoding="utf-8")
        (root / "launcher").mkdir(exist_ok=True)
        (root / "launcher" / "index.html").write_text("<html></html>", encoding="utf-8")
    if templates:
        (root / "templates").mkdir(exist_ok=True)
        (root / "templates" / "qwen_image.api.json").write_text("{}", encoding="utf-8")

    models = list(models if models is not None else CATALOG_IMAGE)
    selected = list(selected if selected is not None else [m["id"] for m in models])

    comfy = tmp_path / "ComfyUI"
    comfy.mkdir(parents=True, exist_ok=True)
    if comfy_main:
        (comfy / "main.py").write_text("# comfyui", encoding="utf-8")
    if materialize_models:
        for model in models:
            if model["id"] not in selected:
                continue
            target = comfy / "models" / model["target"] / model["file"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"stub")

    if settings is not None:
        (root / "data" / "settings.json").write_text(
            json.dumps(settings, ensure_ascii=False), encoding="utf-8")
    if not data_writable:
        # 把 data 目录换成一个同名文件：mkdir/write 必然失败
        for child in sorted((root / "data").iterdir()):
            if child.is_file():
                child.unlink()
        (root / "data").rmdir()
        (root / "data").write_text("not a directory", encoding="utf-8")

    monkeypatch.setattr(doctor, "ROOT", root)
    monkeypatch.setattr(doctor, "STYLE_FILE", root / "data" / "user_mode.json")
    monkeypatch.setattr(doctor, "SETTINGS_FILE", root / "data" / "settings.json")
    monkeypatch.setattr(doctor, "TEMPLATES_DIR", root / "templates")
    monkeypatch.setattr(doctor, "comfy_root", lambda: comfy)
    monkeypatch.setattr(doctor, "catalog", lambda: list(models))
    monkeypatch.setattr(doctor, "selected_models", lambda: list(selected))
    monkeypatch.setattr(doctor, "_python_version", lambda: tuple(python_version))
    monkeypatch.setattr(doctor, "_ffmpeg_available", lambda _settings=None: bool(ffmpeg))
    monkeypatch.setattr(doctor, "_torch_probe", lambda: dict(torch or TORCH_OK))
    monkeypatch.setattr(doctor, "_port_open", lambda *_a, **_kw: bool(port))
    doctor._reset_cache()
    return {"root": root, "comfy": comfy, "models": models, "selected": selected}


def codes(result: dict) -> set[str]:
    return {item["code"] for item in result["checks"]}


def issue(result: dict, code: str) -> dict | None:
    for item in result["issues"]:
        if item["code"] == code:
            return item
    return None


# ---------------------------------------------------------------------- doctor 结构
def test_doctor_checks_expected_structure(tmp_path, monkeypatch):
    ctx = build_project(tmp_path, monkeypatch, ffmpeg=True, port=True)
    result = doctor.check(force=True, style="easy")

    assert codes(result) == {
        "python_version", "comfyui_main", "web_assets", "ffmpeg",
        "models", "data_writable", "templates", "torch", "comfy_port",
    }
    assert result["ok"] is True
    assert result["issues"] == []
    assert result["summary"] == {"blocking": 0, "warnings": 0, "passed": 9, "total": 9}
    assert result["comfy_root"] == str(ctx["comfy"])
    assert result["python"] == "3.12.4"
    assert all(item["ok"] for item in result["checks"])


def test_doctor_image_only_ffmpeg_is_warning(tmp_path, monkeypatch):
    build_project(tmp_path, monkeypatch, ffmpeg=False)
    result = doctor.check(force=True)

    item = issue(result, "ffmpeg_missing")
    assert item is not None
    assert item["blocking"] is False
    assert item["level"] == "warning"
    assert result["ok"] is True
    assert item in result["warnings"]


def test_doctor_video_model_makes_ffmpeg_blocking(tmp_path, monkeypatch):
    build_project(tmp_path, monkeypatch, models=CATALOG_VIDEO, ffmpeg=False)
    result = doctor.check(force=True)

    item = issue(result, "ffmpeg_missing")
    assert item is not None and item["blocking"] is True and item["level"] == "error"
    assert result["ok"] is False
    assert result["summary"]["blocking"] == 1


def test_doctor_settings_can_require_video(tmp_path, monkeypatch):
    build_project(tmp_path, monkeypatch, models=CATALOG_IMAGE, ffmpeg=False,
                  settings={"ffmpeg": {"required": True}})
    result = doctor.check(force=True)
    item = issue(result, "ffmpeg_missing")
    assert item is not None and item["blocking"] is True
    assert result["ok"] is False


@pytest.mark.parametrize("settings", [
    {"video": {"enabled": True}},
    {"features": {"video": True}},
    {"generation": {"video_required": True}},
])
def test_doctor_video_hint_variants(tmp_path, monkeypatch, settings):
    build_project(tmp_path, monkeypatch, models=CATALOG_IMAGE, ffmpeg=False, settings=settings)
    assert doctor.check(force=True)["ok"] is False


def test_doctor_non_blocking_warnings_do_not_fail(tmp_path, monkeypatch):
    build_project(tmp_path, monkeypatch, templates=False, torch=TORCH_NO_TORCH,
                  ffmpeg=True, port=False)
    result = doctor.check(force=True)

    assert result["ok"] is True
    for code in ("templates_missing", "torch_missing", "comfyui_not_running"):
        item = issue(result, code)
        assert item is not None, code
        assert item["blocking"] is False
        assert item["level"] == "warning"
    assert result["summary"]["blocking"] == 0


def test_doctor_cuda_unavailable_is_warning(tmp_path, monkeypatch):
    build_project(tmp_path, monkeypatch, torch=TORCH_NO_CUDA, ffmpeg=True, port=True)
    result = doctor.check(force=True)
    item = issue(result, "cuda_unavailable")
    assert item is not None and item["blocking"] is False
    assert result["ok"] is True


def test_doctor_blocking_issues(tmp_path, monkeypatch):
    ctx = build_project(
        tmp_path, monkeypatch,
        models=CATALOG_IMAGE, materialize_models=False,
        assets=False, comfy_main=False, data_writable=False,
        python_version=(3, 9, 18), ffmpeg=True, port=True,
    )
    result = doctor.check(force=True)

    assert result["ok"] is False
    blocking = {item["code"] for item in result["issues"] if item["blocking"]}
    assert {"python_version", "comfyui_missing", "asset_missing",
            "model_missing", "data_not_writable"} <= blocking
    assert all(item["level"] == "error" for item in result["issues"] if item["blocking"])
    assert issue(result, "comfyui_missing")["path"] == str(ctx["comfy"])


def test_doctor_easy_and_pro_messages_differ(tmp_path, monkeypatch):
    build_project(tmp_path, monkeypatch, ffmpeg=False)
    easy = doctor.check(force=True, style="easy")
    pro = doctor.check(force=True, style="pro")

    easy_issue = issue(easy, "ffmpeg_missing")
    pro_issue = issue(pro, "ffmpeg_missing")
    assert easy["style"] == "easy" and pro["style"] == "pro"
    assert easy_issue["message"] != pro_issue["message"]
    assert easy_issue["fix"] and pro_issue["fix"]
    assert {i["code"] for i in easy["issues"]} == {i["code"] for i in pro["issues"]}


def test_doctor_cache_and_force(tmp_path, monkeypatch):
    build_project(tmp_path, monkeypatch, ffmpeg=True, port=True)
    assert doctor.check(force=True)["issues"] == []

    monkeypatch.setattr(doctor, "_ffmpeg_available", lambda _settings=None: False)
    cached = doctor.check()
    assert cached["issues"] == []           # 20s 缓存命中，未重新扫描

    fresh = doctor.check(force=True)
    assert issue(fresh, "ffmpeg_missing") is not None
    doctor._reset_cache()


def test_doctor_never_requires_comfyui_running(tmp_path, monkeypatch):
    """ComfyUI 未启动只应产生非阻塞告警。"""
    build_project(tmp_path, monkeypatch, ffmpeg=True, port=False)
    result = doctor.check(force=True)
    assert result["ok"] is True
    assert issue(result, "comfyui_not_running")["blocking"] is False


# ---------------------------------------------------------------------- safety 探测
def test_python_supported_thresholds():
    assert safety_mod.python_supported((3, 10, 0)) is True
    assert safety_mod.python_supported((3, 12, 4)) is True
    assert safety_mod.python_supported((3, 9, 18)) is False
    assert safety_mod.python_supported((2, 7, 18)) is False
    assert safety_mod.python_supported(("bad",)) is False
    assert safety_mod.python_supported() is True  # 本进程 >= 3.10


def test_comfy_python_returns_existing_path():
    resolved = safety_mod.comfy_python()
    assert isinstance(resolved, str) and resolved
    assert os.path.isfile(resolved)


def test_port_open_detects_listener():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert safety_mod.port_open("127.0.0.1", port, timeout=0.5) is True
    finally:
        server.close()
    assert safety_mod.port_open("127.0.0.1", port, timeout=0.2) is False


def test_probe_torch_parses_subprocess_json(monkeypatch):
    class _Proc:
        returncode = 0
        stdout = 'noise\n{"torch": true, "cuda": true, "torch_version": "9.9", ' \
                 '"cuda_version": "12.4", "device": "Fake"}\n'
        stderr = ""

    monkeypatch.setattr(safety_mod.subprocess, "run", lambda *a, **k: _Proc())
    info = safety_mod.probe_torch("python", use_cache=False)
    assert info["torch"] is True and info["cuda"] is True
    assert info["torch_version"] == "9.9"
    assert info["device"] == "Fake"


def test_probe_torch_handles_missing_executable():
    info = safety_mod.probe_torch(str(Path("definitely-missing-python.exe")))
    assert info["torch"] is False and info["cuda"] is False
    assert info["error"]


def test_probe_torch_handles_garbage_output(monkeypatch):
    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "Traceback: boom"

    monkeypatch.setattr(safety_mod.subprocess, "run", lambda *a, **k: _Proc())
    info = safety_mod.probe_torch("python", use_cache=False)
    assert info["torch"] is False
    assert "boom" in info["error"] or "probe exit" in info["error"]


def test_safety_selfcheck_shape(monkeypatch):
    monkeypatch.setattr(safety_mod, "probe_torch", lambda *a, **k: dict(TORCH_OK))
    monkeypatch.setattr(safety_mod, "port_open", lambda *a, **k: True)
    result = safety_mod.selfcheck()
    assert set(result) == {"python", "torch", "comfy_port"}
    assert result["python"]["supported"] is True
    assert result["torch"]["cuda"] is True
    assert result["comfy_port"]["open"] is True
