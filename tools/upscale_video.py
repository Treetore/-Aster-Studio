"""逐帧视频超分（Real-ESRGAN / RRDB 架构，流式管道，内存恒定）。

用法:
  python tools/upscale_video.py --src in.mp4 --dst out.mp4 --model 4x-UltraSharp.pth [--scale-out 2]
"""
import argparse
import os
import subprocess
import sys
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

FFMPEG = r"C:\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe"
FFPROBE = r"C:\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffprobe.exe"
MODELS = r"K:\CONFI\mian\ComfyUI_windows_portable\ComfyUI\models\upscale_models"


class ResidualDenseBlock(nn.Module):
    def __init__(self, num_feat=64, num_grow_ch=32):
        super().__init__()
        self.conv1 = nn.Conv2d(num_feat, num_grow_ch, 3, 1, 1)
        self.conv2 = nn.Conv2d(num_feat + num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv3 = nn.Conv2d(num_feat + 2 * num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv4 = nn.Conv2d(num_feat + 3 * num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv5 = nn.Conv2d(num_feat + 4 * num_grow_ch, num_feat, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

    def forward(self, x):
        x1 = self.lrelu(self.conv1(x))
        x2 = self.lrelu(self.conv2(torch.cat((x, x1), 1)))
        x3 = self.lrelu(self.conv3(torch.cat((x, x1, x2), 1)))
        x4 = self.lrelu(self.conv4(torch.cat((x, x1, x2, x3), 1)))
        x5 = self.conv5(torch.cat((x, x1, x2, x3, x4), 1))
        return x5 * 0.2 + x


class RRDB(nn.Module):
    def __init__(self, num_feat, num_grow_ch=32):
        super().__init__()
        self.rdb1 = ResidualDenseBlock(num_feat, num_grow_ch)
        self.rdb2 = ResidualDenseBlock(num_feat, num_grow_ch)
        self.rdb3 = ResidualDenseBlock(num_feat, num_grow_ch)

    def forward(self, x):
        out = self.rdb1(x)
        out = self.rdb2(out)
        out = self.rdb3(out)
        return out * 0.2 + x


class RRDBNet(nn.Module):
    def __init__(self, num_in_ch=3, num_out_ch=3, scale=4, num_feat=64, num_block=6, num_grow_ch=32):
        super().__init__()
        self.scale = scale
        self.conv_first = nn.Conv2d(num_in_ch, num_feat, 3, 1, 1)
        self.body = nn.Sequential(*[RRDB(num_feat, num_grow_ch) for _ in range(num_block)])
        self.conv_body = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_up1 = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_up2 = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_hr = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_last = nn.Conv2d(num_feat, num_out_ch, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

    def forward(self, x):
        feat = self.conv_first(x)
        feat = feat + self.conv_body(self.body(feat))
        feat = self.lrelu(self.conv_up1(F.interpolate(feat, scale_factor=2, mode="nearest")))
        feat = self.lrelu(self.conv_up2(F.interpolate(feat, scale_factor=2, mode="nearest")))
        return self.conv_last(self.lrelu(self.conv_hr(feat)))


def load_model(name: str, device: torch.device) -> RRDBNet:
    path = name if os.path.isabs(name) else os.path.join(MODELS, name)
    sd = torch.load(path, map_location="cpu", weights_only=True)
    if isinstance(sd, dict) and "params_ema" in sd:
        sd = sd["params_ema"]
    num_block = 0
    while f"body.{num_block}.rdb1.conv1.weight" in sd:
        num_block += 1
    num_feat = sd["conv_first.weight"].shape[0]
    scale = 4 if "conv_up2.weight" in sd else 2
    model = RRDBNet(num_block=num_block, num_feat=num_feat, scale=scale)
    model.load_state_dict(sd, strict=True)
    model.eval().to(device)
    print(f"[model] {os.path.basename(path)} blocks={num_block} feat={num_feat} scale={scale}", flush=True)
    return model


def probe(path: str):
    out = subprocess.check_output(
        [FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate,codec_name", "-of", "csv=p=0", path],
        text=True,
    ).strip()
    parts = [p.strip() for p in out.split(",")]
    # ffprobe csv 输出顺序为 codec_name,width,height,r_frame_rate
    w, h = int(parts[1]), int(parts[2])
    num, den = parts[3].split("/") if "/" in parts[3] else (parts[3], "1")
    fps = float(num) / float(den)
    return w, h, fps


def gpu_temp() -> float | None:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).decode("utf-8", "replace").strip().splitlines()[0]
        return float(out)
    except Exception:
        return None


def free_commit_gb() -> float:
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return round(m.ullAvailPageFile / 1024 ** 3, 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--model", default="RealESRGAN_x4plus_anime_6B.pth")
    ap.add_argument("--scale-out", type=float, default=0.0, help="0=保持模型倍数；2=再缩到 2x")
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--pause-every", type=int, default=0, help="每 N 帧休息一次（散热/降内存压力）")
    ap.add_argument("--pause-s", type=float, default=20.0, help="休息秒数")
    ap.add_argument("--max-temp", type=float, default=88.0, help="超过该温度自动暂停降温；0=关闭")
    ap.add_argument("--temp-check-every", type=int, default=24, help="每 N 帧检查一次 GPU 温度")
    ap.add_argument("--min-commit-gb", type=float, default=0.0, help="空闲 commit 低于该值自动暂停/退出；0=关闭")
    args = ap.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = load_model(args.model, device)
    w, h, fps = probe(args.src)
    out_w, out_h = w * model.scale, h * model.scale
    if args.scale_out and args.scale_out != model.scale:
        out_w2 = int(round(w * args.scale_out)) // 2 * 2
        out_h2 = int(round(h * args.scale_out)) // 2 * 2
    else:
        out_w2, out_h2 = out_w, out_h
    print(f"[src] {w}x{h}@{fps:.3f} -> [out] {out_w2}x{out_h2}", flush=True)

    dec = subprocess.Popen(
        [FFMPEG, "-v", "error", "-i", args.src, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, bufsize=10 ** 8,
    )
    vf = []
    if out_w2 != out_w or out_h2 != out_h:
        vf = ["-vf", f"scale={out_w2}:{out_h2}:flags=lanczos"]
    enc = subprocess.Popen(
        [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{out_w}x{out_h}", "-r", str(fps), "-i", "-", "-i", args.src,
         "-map", "0:v:0", "-map", "1:a:0?", *vf,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", str(args.crf),
         "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", args.dst],
        stdin=subprocess.PIPE, bufsize=10 ** 8,
    )

    frame_bytes = w * h * 3
    n = 0
    t0 = time.time()
    while True:
        raw = dec.stdout.read(frame_bytes)
        if not raw or len(raw) < frame_bytes:
            break
        img = np.frombuffer(raw, np.uint8).reshape(h, w, 3)
        x = torch.from_numpy(img).to(device).float().permute(2, 0, 1).unsqueeze(0) / 255.0
        with torch.no_grad():
            y = model(x)
        y = y.clamp(0, 1).mul(255).round().byte().permute(0, 2, 3, 1).squeeze(0).cpu().numpy()
        enc.stdin.write(y.tobytes())
        n += 1
        if n % 24 == 0:
            print(f"[prog] {n} frames {n/(time.time()-t0):.1f} fps", flush=True)
        if args.max_temp > 0 and n % args.temp_check_every == 0:
            while True:
                t = gpu_temp()
                if t is None or t < args.max_temp:
                    break
                print(f"[thermal] GPU {t:.0f}C >= {args.max_temp:.0f}C, pause {args.pause_s:.0f}s", flush=True)
                time.sleep(args.pause_s)
        if args.min_commit_gb > 0 and n % args.temp_check_every == 0:
            low = 0
            while free_commit_gb() < args.min_commit_gb:
                low += 1
                if low > 60:
                    print(f"[mem] commit 持续低于 {args.min_commit_gb:.0f}GB，终止（防止掉驱动）", flush=True)
                    return 3
                print(f"[mem] commit {free_commit_gb():.1f}GB < {args.min_commit_gb:.0f}GB, sleep {args.pause_s:.0f}s", flush=True)
                time.sleep(args.pause_s)
        if args.pause_every > 0 and n % args.pause_every == 0:
            print(f"[cool] {n} frames, sleep {args.pause_s:.0f}s", flush=True)
            time.sleep(args.pause_s)
        if args.max_frames and n >= args.max_frames:
            break
    dec.stdout.close()
    enc.stdin.close()
    dec.wait()
    enc.wait()
    print(f"[done] {n} frames in {time.time()-t0:.0f}s -> {args.dst}", flush=True)
    return 0 if enc.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
