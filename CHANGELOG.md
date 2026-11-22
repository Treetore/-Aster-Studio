# Changelog

All notable changes to Aster Studio are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to semantic versioning for its public interface (HTTP API,
MCP tool surface and CLI entry points).

## [1.0.0] - 2026-11-22

First public source release of the local generation workbench. The project is a
Windows-first, single-workstation deployment: a FastAPI workbench, an optional
desktop launcher, an MCP stdio server, and an optional chat bridge plugin, all
fronting a separately installed ComfyUI instance.

### Added

- **Web workbench** (`webapp/`) served at `http://127.0.0.1:8190/studio`: prompt
  entry, template selection, reference/image upload, job list, gallery, media
  browsing, log view and settings.
- **Desktop launcher** (`launcher/`) on `http://127.0.0.1:8191`: start/stop/
  restart for ComfyUI and the web app, status monitoring, model browsing and
  deletion, diagnostics, console/log view, recent jobs, theme settings.
- **MCP stdio server** (`server.py`) exposing `status`, `hardware`, `start`,
  `stop`, `free`, `templates`, `plan`, `generate`, `job`, `jobs`, `cancel`,
  `wait`, `upload`, `edit`, `probe_media` and `settings`.
- **Workflow templates** (`workflow.py`, `templates/`): `minimax_h3_t2v`,
  `minimax_h3_fl2v`, `minimax_h3_r2v`, `minimax_h3_t2v_720p15s`,
  `minimax_h3_image`, `qwen_image`, `qwen_image_edit`, `moss_image`,
  `moss_image_edit`, with unified parameter resolution (`profile`, `width`,
  `height`, `megapixels`, `aspect`, `duration_s`, `length`, `seed`, `steps`,
  `sampler`, `scheduler`, reference images, first/last frame).
- **Host capability detection** (`system_profile.py`): CPU/RAM/VRAM probe,
  `low_vram` / `normal` / `high` recommendation, per-mode limits (direct 720p
  ceiling, segment threshold, recommended optimizer and step count) and a stored
  manual override.
- **First-run setup flow** in the workbench: error-message style selection
  (`easy` / `pro`), host mode confirmation, model selection, and a download
  action for the selected catalog entries.
- **Model catalog** (`setup/models.json`, `setup/models.py`): identifiers,
  expected target directory, file name, size estimate and homepage per model;
  CLI `python -m setup.models --list|--download`.
- **Self-check** (`setup/doctor.py`): Python version, ComfyUI presence, page
  assets, ffmpeg, selected model files, `data/` writability, rendered in `easy`
  or `pro` wording; exposed as `GET /api/doctor` and run before generation.
- **Resource guard** (`safety.py`): hardware snapshot, cost estimate, preflight
  rejection (RAM/VRAM/disk/queue/concurrency) and runtime watch that interrupts
  the running job when free RAM drops below the trip threshold.
- **Job infrastructure** (`jobs.py`, `job_lock.py`): persistent job records with
  atomic `tmp + os.replace` writes, status query, cancellation and a global
  single-task lock.
- **ffmpeg post-processing** (`editing.py`): `trim`, `concat`, `transform`,
  `mux_audio`, `burn_subtitles`, `overlay`, `speed`, `extract_frames`,
  `extract_audio`, `thumbnail`, `to_gif`, `probe`.
- **Long-form pipeline**: segmented video generation with first-frame carry-over
  and concatenation (`tools/segment_film.py`, `tools/auto_film.py`,
  `POST /api/film/start`), plus upscaling endpoints (`POST /api/upscale`).
- **Chat bridge HTTP API** (`/api/bridge/*`): catalog, submit, result polling,
  token-protected media download, health, IP allowlist and access logging.
- **Optional chat plugin** source in `optional/astrbot_plugin_comfy_bridge/`
  (AstrBot commands, config schema, metadata). Not installed by default.
- **Cloudflare tunnel control** in the launcher and `tools/tunnel.ps1`
  (start/stop/status, quick or token mode).
- **Release/legal documents**: `LICENSE` (Apache-2.0), `NOTICE`,
  `SECURITY.md`, `CONTRIBUTING.md`, this changelog, `docs/release/RELEASE_CHECKLIST.md`
  and a GitHub Actions CI workflow.

### Changed

- Documentation now separates **implemented** from **planned** capabilities.
  Claims that did not match the code were removed or rewritten:
  - model handling is described per real entry point — browsing and deletion in
    the launcher, selection and URL-based download in the setup flow — instead of
    a general "upload, delete and download" model manager;
  - the model download path is documented as requiring a configured URL in
    `setup/models.json`, with manual placement as the supported fallback;
  - ComfyUI installation is documented as the CLI step
    `python -m setup.bootstrap`; the first-run wizard does not download ComfyUI;
  - hosting/exposure guidance is documented as tunnel + allowlist + token rather
    than an implied public hosting mode.
- `SETUP.md` documents the environment variables that relocate ComfyUI and the
  embedded interpreter (`COMFY_MCP_COMFY_ROOT`, `COMFY_MCP_PYTHON`,
  `COMFY_MCP_URL`, `COMFYUI_ROOT`) and states which paths are local examples.
- `README.md` API list aligned with the routes that actually exist, including
  `/api/setup/models`, `/api/setup/hostmode`, `/api/setup/download`,
  `/api/film/*`, `/api/upscale`, `/api/bridge/*` and `/api/tunnel` (launcher).
- Plugin documentation in `optional/` is sanitized: host names, tokens and
  deployment-specific values are replaced with placeholders.

### Fixed

- Job store writes are atomic (`tmp` file + `os.replace`), so an interrupted
  process no longer leaves a truncated `data/jobs.json`.
- ffmpeg outputs are written to `<dst>.part` and atomically renamed on success;
  failed runs clean up the partial file.
- Model downloads stream to a `.part` file, validate the response, and remove
  the partial file on error.
- Resource guard now interrupts the running job (not only logs) when free RAM
  crosses the trip threshold or ComfyUI stops responding.
- Bridge media delivery uses a tokenized URL and no longer relies on a local file
  path being visible across containers.

### Known limitations

- **Model download needs a URL.** `setup/models.json` ships with empty `url`
  fields; the wizard and CLI report an error until a URL is provided, and the downloader attempts a HuggingFace filename lookup when `url` is empty.
- **No model upload.** Models must be placed under `ComfyUI/models/` manually or
  downloaded with a configured URL; the UI only browses and deletes them.
- **ComfyUI is not installed by the wizard.** Use `python -m setup.bootstrap` or
  point `COMFY_MCP_COMFY_ROOT` / `COMFYUI_ROOT` at an existing installation.
  The first-run wizard does not download ComfyUI; use `setup/install.py`.
- **Offline test suite.** The test suite is green on Python 3.11/3.12; tests use
  fixtures/mocks and do not require GPU or model weights.

- **Windows only.** Paths, PowerShell scripts and launcher behaviour assume
  Windows; several scripts under `tools/` and the defaults in `config.py`
  contain local absolute example paths that must be overridden or edited for a
  different layout.
- **Single workstation, single task.** One GPU, one generation at a time. The
  local web UI has no user authentication; exposure requires a tunnel plus the
  IP allowlist and bridge token described in `SECURITY.md`.
- **Chat plugin scope.** Commands cover text-to-image and text-to-video;
  image-to-image editing needs a source image, which the chat flow does not
  upload yet.
- **No installer or packaging.** Distribution is a source checkout.
