"""一条命令完成安装检查 / 可选拉取 ComfyUI / 依赖检查，并打印后续步骤。

只做「环境准备」，不下载大模型、不启动 ComfyUI、不执行任何生成任务。

用法::

    python setup/install.py                  # 交互式：缺 ComfyUI 时询问是否拉取
    python setup/install.py --yes            # 非交互，自动确认
    python setup/install.py --skip-comfy     # 只检查依赖与路径，不拉取 ComfyUI
    python setup/install.py --json           # 机器可读输出
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402  (同时会加载项目根 .env)

# 运行本服务所需的第三方包（torch / ComfyUI 自身依赖由 ComfyUI 便携版负责）
REQUIRED_MODULES = [
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn[standard]"),
    ("requests", "requests"),
    ("psutil", "psutil"),
    ("pydantic", "pydantic"),
    ("multipart", "python-multipart"),
    ("aiohttp", "aiohttp"),
]

BANNER = "=" * 62


def _print(title: str) -> None:
    print(f"\n{BANNER}\n{title}\n{BANNER}")


def detect_host() -> dict:
    """调用 system_profile.detect()；psutil 缺失时降级为最小信息。"""
    try:
        from system_profile import detect

        data = detect()
        data["_source"] = "system_profile"
        return data
    except Exception as exc:
        return {
            "_source": "fallback",
            "_error": str(exc),
            "os": os.name,
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "mode": "unknown",
        }


def check_deps() -> list[dict]:
    out = []
    for module, spec in REQUIRED_MODULES:
        try:
            importlib.import_module(module)
            out.append({"module": module, "spec": spec, "ok": True})
        except Exception as exc:
            out.append({"module": module, "spec": spec, "ok": False, "error": str(exc)})
    return out


def check_comfy(root: Path | None = None) -> dict:
    main_py = (root or Path(config.COMFY_ROOT)) / "main.py"
    python = Path(config.COMFY_PYTHON)
    return {
        "root": str(root or Path(config.COMFY_ROOT)),
        "root_source": config.COMFY_ROOT_SOURCE,
        "main_exists": main_py.is_file(),
        "python": str(python),
        "python_source": config.COMFY_PYTHON_SOURCE,
        "python_exists": python.is_file(),
    }


def maybe_bootstrap(ask: bool, ref: str | None = None, dest: str | None = None) -> dict | None:
    """可选：拉取 ComfyUI 源码（只拉代码，不下载模型）。"""
    from setup import bootstrap

    target = Path(dest) if dest else bootstrap.default_dest()
    if (target / "main.py").is_file():
        return {"ok": True, "path": str(target), "skipped": True}
    if ask:
        try:
            answer = input(f"未找到 ComfyUI（{target}）。现在拉取源码？[y/N] ").strip().lower()
        except EOFError:
            answer = "n"
        if answer not in ("y", "yes"):
            return {"ok": False, "path": str(target), "skipped": True, "reason": "user-declined"}
    print(f"→ 拉取 ComfyUI 源码到 {target} ...")
    return bootstrap.download(target, ref or bootstrap.DEFAULT_REF)


def _next_steps(deps_ok: bool, comfy: dict, web_port: int = 8190) -> list[str]:
    steps: list[str] = []
    if not comfy.get("main_exists"):
        steps.append(
            "安装/指定 ComfyUI：设置环境变量 COMFYUI_ROOT 指向含 main.py 的目录，"
            "或运行 python setup/bootstrap.py"
        )
    if not comfy.get("python_exists"):
        steps.append("设置 COMFY_MCP_PYTHON 指向可运行 ComfyUI 的 Python（便携版 python_embeded\\python.exe）")
    if not deps_ok:
        steps.append(f'安装依赖：python -m pip install -r "{ROOT / "requirements.txt"}"')
    steps.append("（ComfyUI 侧依赖与 torch 由 ComfyUI 便携版自带，本项目不重复安装）")
    steps.append("模型文件请按 setup/models.json 自行放置（本脚本不下载大模型）")
    steps.append("启动：start_all.bat（ComfyUI 8188 + Web UI 8190）")
    steps.append(f"打开：http://127.0.0.1:{web_port}/ ，自检：python setup/doctor.py")
    return steps


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Aster Studio 安装检查（不下载模型、不启动 ComfyUI、不执行生成）")
    ap.add_argument("--skip-comfy", action="store_true", help="跳过 ComfyUI 拉取检查")
    ap.add_argument("--yes", action="store_true", help="非交互，自动确认所有提示")
    ap.add_argument("--dest", default=None, help="ComfyUI 目标目录（透传给 bootstrap）")
    ap.add_argument("--ref", default=None, help="ComfyUI git 分支/标签（默认 master）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON 结果")
    args = ap.parse_args(argv)

    host = detect_host()
    paths = config.path_report()
    deps = check_deps()
    deps_ok = all(d["ok"] for d in deps)
    comfy = check_comfy()
    bootstrap_result: dict | None = None

    if not args.skip_comfy and not comfy["main_exists"]:
        bootstrap_result = maybe_bootstrap(ask=not args.yes, ref=args.ref, dest=args.dest)
        comfy = check_comfy(Path(bootstrap_result.get("path", comfy["root"]))
                            if bootstrap_result else None)

    report = {
        "project": str(ROOT),
        "env_file": paths.get("env_file"),
        "host": host,
        "paths": paths,
        "comfy": comfy,
        "bootstrap": bootstrap_result,
        "deps": deps,
        "deps_ok": deps_ok,
        "next_steps": _next_steps(deps_ok, comfy),
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print("1. 主机环境")
        gpu = host.get("gpu") or {}
        print(f"  系统      : {host.get('os')} {host.get('os_version', '')}".rstrip())
        print(f"  Python    : {host.get('python')}")
        print(f"  CPU/RAM   : {host.get('cpu_count')} 线程 / {host.get('ram_gb')} GB")
        if gpu:
            print(f"  GPU       : {gpu.get('name')} ({gpu.get('vram_total_mb')} MB)")
        else:
            print("  GPU       : 未检测到 NVIDIA GPU（nvidia-smi 不可用）")
        print(f"  运行档位  : {host.get('mode')}（auto={host.get('detected_mode')}）")
        if host.get("_source") == "fallback":
            print(f"  ! 主机探测降级：{host.get('_error')}")

        _print("2. 路径解析（从项目位置与环境变量推导，无写死盘符）")
        print(f"  项目根    : {paths['project_dir']}")
        print(f"  .env      : {paths.get('env_file') or '（未使用）'}")
        print(f"  ComfyUI   : {paths['comfy_root']}")
        print(f"              来源 {paths['comfy_root_source']}")
        print(f"  main.py   : {'存在' if comfy['main_exists'] else '缺失'}")
        print(f"  Python    : {paths['comfy_python']}")
        print(f"              来源 {paths['comfy_python_source']}")
        print(f"  ffmpeg    : {paths['ffmpeg']} ({paths['ffmpeg_source']})")
        print(f"  ffprobe   : {paths['ffprobe']} ({paths['ffprobe_source']})")

        _print("3. Python 依赖（本项目 Web/Server 侧）")
        for d in deps:
            mark = "OK  " if d["ok"] else "MISS"
            print(f"  [{mark}] {d['spec']}")
        if not deps_ok:
            print(f'\n  安装命令: python -m pip install -r "{ROOT / "requirements.txt"}"')
        print("  注：torch / ComfyUI 依赖由 ComfyUI 便携版提供，这里不安装。")

        if bootstrap_result is not None:
            _print("4. ComfyUI 源码")
            print("  " + json.dumps(bootstrap_result, ensure_ascii=False))
        elif args.skip_comfy:
            _print("4. ComfyUI 源码")
            print("  已按 --skip-comfy 跳过")

        _print("5. 下一步")
        for i, step in enumerate(report["next_steps"], 1):
            print(f"  {i}. {step}")

        hard_ok = deps_ok and (comfy["main_exists"] or args.skip_comfy)
        print()
        print(f"结果: {'就绪，可以启动服务' if hard_ok else '存在未完成项（见上）'}")
        print("本次未下载模型、未启动 ComfyUI、未执行生成任务。")

    # 退出码：依赖缺失（或未跳过时缺 ComfyUI）视为未就绪
    hard_ok = deps_ok and (comfy["main_exists"] or args.skip_comfy)
    return 0 if hard_ok else 1


if __name__ == "__main__":
    sys.exit(main())
