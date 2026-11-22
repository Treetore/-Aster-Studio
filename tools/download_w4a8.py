"""单线程 + Range 断点续传 + 429 退避，下载 W4A8 UNET。"""
import os
import sys
import time
import urllib.request

BASE = "https://hf-mirror.com/AX1Y2JP/MiniMax-H3-W4A8-ConvRot/resolve/main"
REL = "minimax_h3_fl2va_pruned_w4a8_mixed.safetensors"
DST = os.path.join(r"K:\CONFI\mian\ComfyUI_windows_portable\ComfyUI\models", "diffusion_models", REL)
PART = DST + ".part"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "*/*"}


def head_total() -> int:
    for attempt in range(1, 12):
        try:
            req = urllib.request.Request(f"{BASE}/{REL}", method="HEAD", headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return int(r.headers["Content-Length"])
        except Exception as exc:  # noqa: BLE001
            print(f"[head] {type(exc).__name__} attempt={attempt}", flush=True)
            time.sleep(min(120, 20 * attempt))
    raise RuntimeError("HEAD failed")


def main() -> int:
    if os.path.isfile(DST) and os.path.getsize(DST) > 1024 ** 3:
        print(f"[skip] exists {os.path.getsize(DST)/1024**3:.2f}GB", flush=True)
        return 0
    total = head_total()
    pos = os.path.getsize(PART) if os.path.isfile(PART) else 0
    print(f"[info] total={total/1024**3:.2f}GB resume={pos/1024**3:.3f}GB", flush=True)
    attempt = 0
    last_report = 0
    t0 = time.time()
    with open(PART, "ab") as f:
        while pos < total:
            try:
                req = urllib.request.Request(f"{BASE}/{REL}", headers={**UA, "Range": f"bytes={pos}-"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    while pos < total:
                        data = r.read(8 * 1024 * 1024)
                        if not data:
                            break
                        f.write(data)
                        f.flush()
                        pos += len(data)
                        if pos - last_report >= 512 * 1024 * 1024:
                            last_report = pos
                            sp = pos / 1024 ** 2 / max(1, time.time() - t0)
                            print(f"{pos*100/total:5.1f}% {pos/1024**3:5.2f}/{total/1024**3:.2f}GB {sp:5.1f}MB/s", flush=True)
                attempt = 0
            except Exception as exc:  # noqa: BLE001
                attempt += 1
                print(f"[retry] {type(exc).__name__}: {str(exc)[:100]} attempt={attempt} pos={pos/1024**3:.2f}GB", flush=True)
                if attempt > 15:
                    return 1
                time.sleep(min(120, 15 * attempt))
    os.replace(PART, DST)
    print(f"[done] {DST} {os.path.getsize(DST)/1024**3:.2f}GB {time.time()-t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
