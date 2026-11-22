# comfy_mcp — 项目规范（Lead 与队友共享契约）

## 目标
为本地 ComfyUI（MiniMax-H3 视频生成）提供一个 **MCP stdio 服务**，让 Agent 能：
1. 编写/参数化视频工作流（API 格式），2. 自动化排队与监控，3. 用 ffmpeg 做视频剪辑，
4. 严格做显存/内存/磁盘资源守卫，避免 8GB VRAM + 32GB RAM 的本机崩溃。

## 环境事实（已实测，不要重复探测）
- ComfyUI 根：`K:\CONFI\mian\ComfyUI_windows_portable\ComfyUI`
- Python：`K:\CONFI\mian\ComfyUI_windows_portable\python_embeded\python.exe`（3.13，已装 requests，无 psutil）
- ComfyUI 版本：0.33.1，当前**已在本机 127.0.0.1:8188 运行**（Lead 启动，PID 16440）。禁止重启/停止它。
- GPU：RTX 4070 Laptop，8188 MiB VRAM；RAM 31.8GB；ffmpeg：`C:\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe`
- 工作流原件：`...\ComfyUI\user\default\workflows\video_minimax_h3_r2v.json`、`VIDOH3.json`
- 模型：`models\diffusion_models\minimax_h3_{fl2va,ref2va}_pruned_int8_convrot.safetensors`（各 19.5GB）、
  `text_encoders\qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`（14.6GB）、`vae\minimax_h3_video_vae_fp16`（4.85GB）、
  `vae\minimax_h3_audio_vae_fp32`（0.56GB）、`loras\minimax_h3\minimax_h3_fl2v_{lightx2v_turbo_4step_v0.1,turbo_8step_v1.0}_*.safetensors`（1.82GB each）
- 输出目录：`...\ComfyUI\output`；输入目录：`...\ComfyUI\input`
- recon 数据：`K:\CONFI\comfy_recon\object_info_subset.json`（节点 schema 实测快照）

## 关键 API 事实（已实测）
- `POST /prompt` body `{"prompt": {node_id: {class_type, inputs}}, "prompt_id": <uuid 可选>, "client_id": ...}` → `{"prompt_id": "...", "number": n, "node_errors": {}}`
- `GET /api/jobs?status=&limit=`、`GET /api/jobs/{id}`、`POST /api/jobs/{id}/cancel`（新式任务 API）
- 老式：`GET /queue`、`GET /history/{id}`、`POST /interrupt`（body 可带 prompt_id）、`POST /free`（unload_models/free_memory）、`POST /queue {"clear":true}`
- `POST /upload/image` multipart 字段 `image`，可选 `subfolder`、`type=input`、`overwrite=true` → `{name, subfolder, type}`
- `GET /system_stats` → `system.{ram_total, ram_free, comfyui_version, argv}`；`GET /object_info[/{class}]`
- SaveVideo 的 codec 是 `COMFY_DYNAMICCOMBO_V3`（API 里传字符串 "auto" 即可，h264+crf 时结构见 object_info）

## 目录与文件归属（禁止越界写）
```
K:\CONFI\comfy_mcp\
  SPEC.md                (Lead)
  README.md              (Lead)
  __init__.py            (Lead)
  config.py              (Lead)
  mcp_protocol.py        (Lead)
  server.py              (Lead)
  client.py              (T1)
  safety.py              (T1)
  jobs.py                (T1)
  tests\test_client_safety.py (T1)
  workflow.py            (T2)
  templates\*.api.json   (T2)
  tests\test_workflow.py (T2)
  editing.py             (T3)
  tests\test_editing.py  (T3)
  docs\WORKFLOWS.md      (T2)
  docs\EDITING.md        (T3)
  data\ logs\ artifacts\ (运行时生成，可写)
  bundle\                (Lead: package.json, cordis.patch.yml, locale\, icon.svg)
```

## 依赖与约束
- 只用 **标准库 + requests**（嵌入 Python 已装 requests 2.34）。不新增第三方依赖。
- 所有中文/emoji 输出必须 UTF-8 安全（Windows 控制台默认 GBK 会炸）；脚本内部读写统一 `encoding="utf-8"`。
- 测试用 `unittest`，命令：`& 'K:\CONFI\mian\ComfyUI_windows_portable\python_embeded\python.exe' -m unittest discover -s K:\CONFI\comfy_mcp\tests -v`
- **禁止**提交会加载 H3 大模型的 prompt（会 OOM/长时间占用）。模板正确性只用 `/object_info` 校验；
  轻量冒烟只允许 `LoadImage -> SaveImage` 这类 0.5 秒级图。
- 已完成模型加载后的真实生成由 Lead 端到端验证，队友不要触发。
- 并发：默认单飞（同一时刻最多 1 个生成任务）；所有写文件用原子写（tmp + os.replace）。

## 模块接口（必须精确实现）

### config.py（Lead 已写）
常量：`PROJECT_DIR, DATA_DIR, TEMPLATES_DIR, LOGS_DIR, ARTIFACTS_DIR, COMFY_ROOT, COMFY_PYTHON, COMFY_URL, COMFY_HOST, COMFY_PORT, OUTPUT_DIR, INPUT_DIR, FFPROBE`
函数：`load_settings() -> dict`，返回合并后的设置（默认 + `data/settings.json`）。
默认设置结构（键名固定）：
```python
{
 "comfy": {"url": COMFY_URL, "start_args": ["--windows-standalone-build","--listen","127.0.0.1","--port","8188","--fast","--cuda-malloc","--disable-auto-launch"], "start_timeout_s": 300},
 "safety": {"min_ram_free_gb": 6.0, "trip_ram_free_gb": 2.5, "watch_interval_s": 5.0, "single_flight": True,
            "orchestrate": {"enabled": True, "allow_concurrent": False, "max_queue": 2, "vram_budget_mb": 7600}},
 "generation": {"default_profile": "draft",
   "profiles": {"draft": {"aspect":"16:9","megapixels":0.2,"duration_s":2,"steps":8,"sampler":"res_multistep","scheduler":"simple"},
                "standard": {"aspect":"16:9","megapixels":0.4,"duration_s":5,"steps":20,"sampler":"res_multistep","scheduler":"simple"},
                "high": {"aspect":"16:9","megapixels":0.8,"duration_s":5,"steps":24,"sampler":"res_multistep","scheduler":"simple"}},
   "max_pixels": 1920*1088, "max_length": 362},
 "ffmpeg": {"path": None, "threads": 4, "timeout_s": 1800},
}
```

### client.py（T1）
```python
class ComfyError(RuntimeError): ...
class ComfyUnavailable(ComfyError): ...

class ComfyClient:
    def __init__(self, url: str | None = None, timeout: float = 30.0): ...
    def is_up(self) -> bool
    def system_stats(self) -> dict
    def object_info(self, node_class: str | None = None) -> dict
    def queue(self) -> dict
    def prompt(self, api_workflow: dict, prompt_id: str | None = None, extra_data: dict | None = None) -> dict
        # 400 时抛 ComfyError，消息内含 node_errors 的 JSON（截断 2000 字符）
    def jobs(self, status: list[str] | None = None, limit: int = 50) -> list[dict]
    def job(self, job_id: str) -> dict | None      # 404 -> None
    def cancel(self, job_id: str) -> dict
    def interrupt(self, prompt_id: str | None = None) -> bool
    def free(self, unload_models: bool = True, free_memory: bool = True) -> bool
    def history(self, prompt_id: str | None = None) -> dict
    def upload_image(self, path: str, subfolder: str = "") -> dict   # -> {name, subfolder, type}
    def download(self, filename: str, subfolder: str = "", folder_type: str = "output") -> bytes
    def save_output(self, filename: str, subfolder: str, folder_type: str, dest: str) -> str  # 原子写, 返回 dest
    def job_outputs(self, job_id: str) -> list[dict]   # 从 history 提取 [{filename, subfolder, type, filename_abs?}]
```

### safety.py（T1）
```python
def hardware() -> dict   # {gpu_name, vram_total_mb, vram_used_mb, vram_free_mb, ram_total_gb, ram_free_gb, disk_free_gb, checks:{nvidia_smi_ok, ...}}
def ram_free_gb() -> float
def vram_usage() -> dict | None   # {total_mb, used_mb, free_mb}
def estimate_cost(resolved: dict) -> dict  # {pixels, frames, duration_s, vram_risk:"low|medium|high", ram_risk, eta_minutes, notes:[]}
class Guard:
    def __init__(self, client, settings: dict): ...
    def preflight(self, resolved: dict, active_jobs: int = 0) -> dict
        # {ok: bool, risk, reasons:[], suggestions:[], snapshot:{}}
    def snapshot(self) -> dict          # hardware + 当前进程/队列
    def watch(self, job_id: str, on_trip) -> None   # 后台线程, 每 watch_interval_s 检查
    def stop_watch(self) -> None
    def trip_reason(self) -> str | None
```
触发条件（trip → 调 client.interrupt(job_id) 并记录）：ram_free < trip_ram_free_gb，或 system_stats 连续失败，或 vram free < 300MB 持续 3 次（仅告警，不自动 kill 除非 ram 触发）。

### jobs.py（T1）
```python
class JobStore:
    def __init__(self, path: str | None = None): ...   # 默认 DATA_DIR/jobs.json；线程安全（threading.Lock）；原子写
    def add(self, record: dict) -> dict
    def update(self, job_id: str, **fields) -> dict
    def get(self, job_id: str) -> dict | None
    def list(self, limit: int = 50, status: list[str] | None = None) -> list[dict]
    def delete(self, job_id: str) -> bool
```
记录字段：`id,prompt_id,template,params,resolved,status(queued|running|completed|failed|cancelled|aborted),created_at,updated_at,started_at,finished_at,error,outputs:list[str],client_id,mode,pprompt`(仅截断 500 字)。

### workflow.py（T2）
```python
TEMPLATES = {"minimax_h3_r2v": {...}, "minimax_h3_fl2v": {...}, "minimax_h3_t2v": {...}}  # 元数据
def list_templates() -> list[dict]        # [{name,title,description,model,parameters:{name:{type,default,min,max,options,help}}}]
def resolve(name: str, params: dict) -> dict
    # -> {"name","api": {<API prompt>}, "resolved": {"prompt","width","height","length","duration_s","seed","steps","sampler","scheduler","ref_image_size","output_prefix","profile"}, "warnings":[]}
def validate(api: dict, object_info: dict | None = None) -> dict   # {"ok","errors":[],"warnings":[]}
def duration_to_length(seconds: float) -> int   # max(5, round(s*24)) 后 snap 到 17k+5
def align_frame_count(n: int) -> int
def resolve_size(aspect: str, megapixels: float, multiple: int = 32) -> tuple[int,int]
def ui_to_api(ui: dict) -> dict           # 展开 subgraph，去掉 mode=2/4 节点（4 透传），用于导入未来工作流
```
参数面（三模板统一）：`prompt`(必填), `profile`(draft/standard/high 或 None), `width`,`height`,`duration_s` 或 `length`,
`megapixels`,`aspect`,`seed`(None=随机), `steps`, `sampler`, `scheduler`, `ref_image_size`(match|max),
`ref_images`(list[str] 输入文件名), `first_frame`(fl2v), `last_frame`(fl2v), `lora_name`,`lora_strength`(fl2v 可选),
`output_prefix`, `negative_prompt`(H3 无 CFG，忽略并 warn)。解析优先级：显式 width/height > megapixels+aspect > profile。

### editing.py（T3）
用 subprocess 调 ffmpeg/ffprobe（`config.load_settings()["ffmpeg"]`，`-threads` 限流），超时杀进程，输出原子写完再 replace。
```python
class EditError(RuntimeError): ...
def toolchain() -> dict                      # {ffmpeg, ffprobe, version, ok}
def probe(path: str) -> dict                 # {path, width,height,fps,duration_s,video_codec,audio_codec,has_audio,bitrate,size_mb,rotation}
def trim(src, dst, start_s=0.0, duration_s=None, reencode=True) -> dict
def concat(inputs: list[str], dst, crossfade_s=0.0, reencode=True) -> dict
def transform(src, dst, width=None, height=None, fps=None, pad=False, reencode=True) -> dict
def mux_audio(video, dst, audio=None, volume=1.0, offset_s=0.0, replace=True, normalize=False) -> dict
def burn_subtitles(video, dst, srt_path, font_size=24, margin_v=40) -> dict
def overlay(video, dst, image_path, position="bottom-right", margin=20, opacity=1.0, scale=0.2) -> dict
def speed(src, dst, factor=1.0, keep_audio=True) -> dict
def extract_frames(video, out_dir, fps=None, start_s=None, end_s=None, prefix="frame") -> dict
def extract_audio(video, dst) -> dict
def thumbnail(video, dst, at_s=0.0, width=1280) -> dict
def to_gif(src, dst, fps=12, width=640, start_s=0.0, duration_s=None) -> dict
def run_ffmpeg(args: list[str], dst: str, timeout_s: int | None = None) -> dict  # {ok, cmd, stderr_tail, dst, duration_s}
```
所有函数返回 `{"ok":True,"dst":...,"cmd":[...],"probe":{...}}` 或抛 `EditError`。

### server.py 工具层（Lead）
工具名（MCP 里显示为 `mcp__comfy__<name>`）：`status, hardware, start, stop, free, templates, plan, generate, job, jobs, cancel, wait, upload, edit, probe_media, settings`。
`edit` 工具参数：`{"action": "trim|concat|transform|mux_audio|burn_subtitles|overlay|speed|extract_frames|extract_audio|thumbnail|to_gif", ...该函数参数}`。

## 交付与验证
- T1：`python -m unittest ...test_client_safety -v` 全绿；对运行中的 8188 实测 `status/system_stats/jobs/queue`（只读，不提交）。
- T2：`test_workflow` 全绿；用 object_info 实测三模板 `validate` 无 error；`resolve` 覆盖 draft/standard/high、fl2v 首尾帧、r2v 参考图、duration→length 网格、size 对齐。
- T3：`test_editing` 全绿（用 Lead 提供的 `K:\CONFI\comfy_mcp\artifacts\sample.mp4` 或自己用 ffmpeg 生成 1s 测试片）。
- 完成后在任务里报告：改动文件、测试输出摘要、未完成/风险点。不要改 SPEC.md 或其他人的文件。
