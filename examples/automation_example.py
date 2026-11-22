"""端到端自动化示例：状态检查 → 干跑 → 提交 → 轮询 → 剪辑。

不依赖 MCP 客户端，直接用 comfy_mcp 的模块；可在 CI / 计划任务里跑。

用法：
    & 'K:\\CONFI\\mian\\ComfyUI_windows_portable\\python_embeded\\python.exe' examples\\automation_example.py
    ... --count 2 --prompt "..." --duration 2

注意：真正提交会加载 MiniMax-H3（约 40GB 权重），首次运行很慢；默认只做 --dry-run。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
for p in (PROJECT, os.path.dirname(PROJECT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import config  # noqa: E402
import client as comfy_client  # noqa: E402
import jobs as jobs_mod  # noqa: E402
import safety  # noqa: E402
import workflow  # noqa: E402


def build():
    settings = config.load_settings()
    cli = comfy_client.ComfyClient(settings["comfy"]["url"])
    store = jobs_mod.JobStore()
    guard = safety.Guard(cli, settings)
    return settings, cli, store, guard


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", default="minimax_h3_r2v")
    ap.add_argument("--prompt", default="<Picture 1> 夜晚河边，女孩缓缓走过，暖黄路灯，水面金色反光")
    ap.add_argument("--ref", action="append", default=None, help="参考图（本地绝对路径或 input 文件名）")
    ap.add_argument("--profile", default="draft")
    ap.add_argument("--duration", type=float, default=2.0)
    ap.add_argument("--count", type=int, default=1, help="连续生成几个（严格串行）")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--submit", dest="dry_run", action="store_false")
    ap.add_argument("--timeout-min", type=int, default=45)
    args = ap.parse_args()

    settings, cli, store, guard = build()
    if not cli.is_up():
        print("[!] ComfyUI 未运行：先执行 scripts/comfy.ps1 start 或 scripts/start_comfyui_safe.ps1")
        return 2

    hw = safety.hardware()
    print(f"[i] GPU={hw.get('gpu_name')} VRAM={hw.get('vram_free_mb')}/{hw.get('vram_total_mb')}MB "
          f"RAM_free={hw.get('ram_free_gb')}GB disk={hw.get('disk_free_gb')}GB")

    produced = []
    for i in range(max(1, args.count)):
        params = {"prompt": args.prompt, "profile": args.profile, "duration_s": args.duration}
        if args.ref:
            params["ref_images"] = args.ref
        pkg = workflow.resolve(args.template, params)
        validation = workflow.validate(pkg["api"], cli.object_info())
        if not validation["ok"]:
            print("[x] 校验失败:", json.dumps(validation["errors"], ensure_ascii=False, indent=2))
            return 3
        decision = guard.preflight(pkg["resolved"], active_jobs=len(store.list(status=["queued", "running"])))
        print(f"[i] 第 {i + 1}/{args.count} 个：{pkg['resolved']['width']}x{pkg['resolved']['height']} "
              f"{pkg['resolved']['length']}帧 steps={pkg['resolved']['steps']} risk={decision['risk']}")
        if not decision["ok"]:
            print("[x] 资源守卫拒绝:", decision["reasons"])
            return 4
        if args.dry_run:
            print("[dry-run] 不提交；加 --submit 真正执行")
            produced.append(pkg)
            continue

        # 上传本地参考图
        for nid, node in list(pkg["api"].items()):
            if node.get("class_type") == "LoadImage":
                img = node["inputs"]["image"]
                if os.path.isabs(img) and os.path.isfile(img):
                    node["inputs"]["image"] = cli.upload_image(img)["name"]

        import uuid

        pid = str(uuid.uuid4())
        cli.prompt(pkg["api"], prompt_id=pid)
        rec = store.add({"id": pid, "prompt_id": pid, "template": args.template, "params": params,
                         "resolved": pkg["resolved"], "status": "queued"})
        print(f"[+] 已提交 {pid}")
        guard.watch(pid, on_trip=lambda job, reason: print(f"[guard] {job}: {reason}"))
        try:
            deadline = time.time() + args.timeout_min * 60
            while time.time() < deadline:
                time.sleep(10)
                remote = cli.job(pid) or {}
                status = remote.get("status")
                if status in ("completed", "failed", "cancelled"):
                    print(f"[i] 任务结束: {status}")
                    break
                if safety.ram_free_gb() < settings["safety"]["trip_ram_free_gb"]:
                    cli.cancel(pid)
                    store.update(pid, status="aborted", error="low RAM")
                    print("[guard] RAM 触底，已中止")
                    break
            else:
                print("[!] 超时未完成，仍可在 ComfyUI 里查看")
        finally:
            guard.stop_watch()
        outs = cli.job_outputs(pid)
        for o in outs:
            path = os.path.join(config.OUTPUT_DIR, o.get("subfolder") or "", o["filename"])
            print("[=] 输出:", path)
            store.update(pid, status="completed", outputs=[{"path": path}])
            produced.append(path)

    if produced and not args.dry_run:
        print("[i] 剪辑示例（先 trim 第一段到 2s，再转 gif）：")
        import editing

        src = produced[0]
        print(editing.trim(src, os.path.join(config.ARTIFACTS_DIR, "edited", "auto_trim.mp4"), 0, 2).get("dst"))
    print("[done]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
