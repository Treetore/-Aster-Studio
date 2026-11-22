# Contributing to Aster Studio

Thank you for contributing. This document covers the development environment,
test expectations, code style, and the artefacts that must never enter the
repository.

## 1. Scope

Aster Studio targets a single Windows workstation with one CUDA GPU and a
separately installed ComfyUI. Contributions should keep that deployment model
intact: loopback binding, one generation at a time, and external models.

Before starting larger work, open an issue describing the change. For interface
changes (HTTP routes, MCP tool names or parameters, `data/*.json` keys), describe
the compatibility impact in the issue.

## 2. Repository layout

| Path | Contents |
|---|---|
| `server.py`, `mcp_protocol.py` | MCP stdio server and tool layer |
| `client.py` | ComfyUI HTTP client |
| `workflow.py`, `templates/` | Workflow templates, parameter resolution, validation |
| `safety.py` | Hardware probe, cost estimate, resource guard |
| `jobs.py`, `job_lock.py` | Job store and global single-task lock |
| `editing.py` | ffmpeg post-processing |
| `config.py` | Paths and settings (`data/settings.json` overrides) |
| `system_profile.py`, `model_policy.py`, `proc_cleaner.py` | Host/mode policy helpers |
| `webapp/` | FastAPI workbench and browser UI |
| `launcher/` | Desktop launcher (local FastAPI app + window) |
| `setup/` | ComfyUI bootstrap, model catalog, self-check |
| `tools/` | Operational scripts (segmentation, upscale, tunnel, diagnostics) |
| `tests/` | Offline unit tests and `tests/fixtures/` snapshots |
| `optional/` | Optional components, not installed by default |

## 3. Development setup

Requirements: Windows 10/11, Python 3.10 or newer (CI uses 3.12), and a ComfyUI
installation only for manual end-to-end runs.

```powershell
git clone https://github.com/Treetore/-Aster-Studio.git
cd -- -Aster-Studio
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install "pytest>=8"
```

Optional environment variables for a non-default layout (see `SETUP.md`):

```powershell
$env:COMFY_MCP_COMFY_ROOT = "D:\ComfyUI"
$env:COMFY_MCP_PYTHON = "D:\ComfyUI\python_embeded\python.exe"
$env:COMFY_MCP_URL = "http://127.0.0.1:8188"
$env:COMFYUI_ROOT = "D:\ComfyUI"           # used by setup/* and tests
$env:COMFY_BRIDGE_TOKEN = "<token>"        # optional; otherwise auto-minted
```

Unit tests do not need a GPU, ffmpeg, or a running ComfyUI.

## 4. Tests

Run the suite from the directory that contains the `comfy_mcp` package (the
parent of the checkout), so `import comfy_mcp` resolves:

```powershell
cd ..
python -m pytest comfy_mcp/tests -q
```

Equivalent discovery form:

```powershell
python -m unittest discover -s comfy_mcp\tests -v
```

Test properties you must preserve:

- **Offline by default.** `test_client_safety.py` uses a local fake HTTP server
  and mocks hardware probes; it never submits a prompt.
- **The live read-only test skips itself** unless ComfyUI answers on
  `127.0.0.1:8188`, and it only issues GET requests.
- **`test_editing.py` skips itself** when `artifacts/sample.mp4` and
  `artifacts/sample2.mp4` are absent. Those fixtures are git-ignored; generate
  them locally with ffmpeg if you want the editing tests to run.
- **`test_workflow.py` uses `tests/fixtures/object_info.json`** and must stay
  runnable without a live ComfyUI. Refresh that snapshot only when the node
  contract genuinely changed, and mention it in the pull request.

Known drift: seven offline assertions currently fail against the shipped
defaults (`GuardPreflightTest::test_below_min_ram_blocks`,
`test_big_job_blocks`, `test_low_vram_blocks`, `test_trip_ram_free_blocks`,
`TemplateFileTests::test_templates_validate_clean`,
`test_three_template_files_load`, `ResolveTests::test_profiles`). CI excludes
those test names (currently 174 tests pass, 7 are excluded), as recorded in
`CHANGELOG.md` and `docs/RELEASE_CHECKLIST.md`. Fixing the code or the
expectation is welcome; until then, do not add new failures and do not widen the
exclusion list without updating the changelog.

Local quick checks:

```powershell
python -m compileall -q .                        # syntax gate
python -m setup.doctor                           # environment self-check
python -m pytest comfy_mcp/tests -q --maxfail=1   # fail fast while iterating
```

## 5. What must never be committed

The repository must stay free of models, media, runtime state and credentials.
`.gitignore` already excludes the paths below; do not force-add them.

- Model weights and checkpoints: `*.safetensors`, `*.ckpt`, `*.pt`, `*.pth`,
  `*.bin`, `*.gguf`.
- Generated or test media: `*.mp4`, `*.webm`, `artifacts/`, `output/`.
- Runtime state: `data/` (except `data/.gitkeep`), `logs/`, `backups/`,
  `dist/`, `bundle/`.
- Secrets: `data/bridge_token.txt`, `data/ip_allowlist.json`,
  `data/tunnel.json`, `data/tunnel_url.txt`, `.env*`, and any captured request
  log.
- Personal or deployment-specific values in code and docs: private host names,
  public tunnel URLs, IP addresses, tokens, account identifiers. Use
  placeholders such as `<BRIDGE_URL>`, `<TOKEN>`, `<HOST>`, `<IP>`.

If a secret is committed by accident, rotate it first (see `SECURITY.md`), then
rewrite history in a dedicated change.

## 6. Code style

Python:

- Target Python 3.10+ syntax (`from __future__ import annotations`, PEP 604
  unions, builtin generics).
- 4-space indentation, UTF-8 source files, one public API per module where
  practical. Keep modules focused; `server.py` is the tool layer, not a dumping
  ground.
- Type-hint public functions and return dictionaries precisely enough that the
  caller can rely on documented keys.
- Docstrings in Chinese or English are both accepted; keep the existing language
  of the file you are editing.
- Console/log output must survive a GBK console: prefer ASCII for machine-read
  messages and keep UTF-8 for file content. Never print raw emoji into a
  subprocess's inherited stdout on Windows.
- Persist state atomically (`tmp` file + `os.replace`) and serialise writes.
- Do not add a third-party dependency without discussion. The runtime baseline is
  `requirements.txt`; `client.py`/`safety.py`/`jobs.py` intentionally stick to the
  standard library plus `requests`/`psutil`.
- Handle external failures explicitly: ComfyUI down, ffmpeg missing, model file
  absent, disk full. Every such path should return a structured error, not a raw
  traceback.

PowerShell:

- `$ErrorActionPreference = 'Stop'`, validate input via `param(...)`, and prefer
  explicit `-Path`/`-LiteralPath` arguments.
- Do not bake absolute machine paths into new scripts; read them from
  environment variables, `data/*.json`, or a parameter.

Docs:

- Formal tone, no personal narratives, no test/diary entries.
- State explicitly whether a capability is implemented or planned. Do not
  describe a catalog, a stub endpoint, or a design note as a working feature.
- Every relative link must resolve inside the repository.
- Sanitize host names, tokens and addresses to placeholders.

## 7. Adding a workflow template or model

Workflow template:

1. Drop the API-format graph at `templates/<name>.api.json`.
2. Register metadata and the parameter surface in `workflow.py` (`TEMPLATES`).
3. If the graph uses node classes absent from `tests/fixtures/object_info.json`,
   refresh that snapshot and explain why.
4. Extend `tests/test_workflow.py` with resolution and validation coverage.
5. Document parameters in `docs/WORKFLOWS.md`.

Model catalog entry:

1. Append an object to `setup/models.json` with `id`, `name`, `kind`, `target`
   (the `ComfyUI/models/` subdirectory), `file`, `size_gb` and `homepage`.
2. Leave `url` empty unless you are certain about a stable direct download and
   its license implications. Users may fill it in locally.
3. If the entry introduces a new model family, add it to `NOTICE` with its
   upstream source, and reiterate that weights are not distributed.

## 8. Pull request process

1. Keep the change focused; unrelated refactors belong in a separate pull
   request.
2. Update documentation and `CHANGELOG.md` (`Unreleased` section) with any
   behaviour change, removal, or new limitation.
3. Confirm locally: `python -m compileall -q .` and
   `python -m pytest comfy_mcp/tests -q` (no new failures).
4. Confirm the diff contains no models, media, tokens, logs or personal paths.
5. CI (`.github/workflows/ci.yml`) must pass: byte-compilation, the tracked-binary
   guard, and the offline test subset on `windows-latest` with Python 3.12.
6. In the description, state: what changed, what you verified, and what remains
   unverified (for example "not exercised against a live ComfyUI").

Reviewers check: interface compatibility, error handling, atomic writes, absence
of secrets and weights, documentation accuracy (implemented vs planned), and
whether new behaviour is covered by an offline test.
