# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 1.0.x | Yes |
| < 1.0 | No |

Security fixes are applied to the latest release line only.

## Deployment model (read this first)

Aster Studio is designed as a **single-workstation tool**. The web app binds to
loopback and the generation backend is a local ComfyUI process.

- Web workbench default: `127.0.0.1:8190` (`uvicorn ... --host 127.0.0.1`)
- Launcher: `127.0.0.1:8191`
- ComfyUI: `127.0.0.1:8188` (launcher start arguments include `--listen 127.0.0.1`)
- MCP transport: stdio (no network listener)

Do not change these bind addresses to `0.0.0.0` unless you understand the
consequences. The web UI has no login, no CSRF token and no per-user
authorization: anyone who can reach the port can submit jobs, read job history,
read logs, browse generated media and change settings. Treat loopback binding as
the primary access control.

## Exposure through a tunnel

If you expose the service to a chat platform or the internet, all of the
following apply at once:

1. **Only the bridge surface is meant to be reachable.** Tunnel the bridge
   endpoints (`/api/bridge/*`) rather than the whole application. Exposing
   `/studio`, `/api/settings`, `/api/logs` or the generation endpoints gives a
   caller without credentials control of the workstation.
2. **Keep the bridge token on.** Every `/api/bridge/*` handler compares the
   `X-Bridge-Token` header (or `?token=` on media downloads) with the minted
   token. A missing or wrong token is rejected: JSON endpoints answer
   `{"ok": false, "error": "invalid token"}` and the media endpoint answers
   HTTP 403. Never ship a configuration with an empty token.
3. **Understand the IP allowlist's exact behaviour.** `data/ip_allowlist.json`
   is enforced by a global middleware for *every* HTTP path:
   - with `{"enabled": true, "ips": [...]}` only loopback, `::1` and the listed
     addresses pass; everything else gets HTTP 403 with `ip not allowed`;
   - if the file is missing or unparsable, the middleware falls back to a single
     placeholder entry (`203.0.113.10`, from the TEST-NET-3 documentation range),
     which in practice still blocks non-loopback callers until you configure it;
   - `/api/bridge/media/<job_id>` is deliberately exempt because chat adapters
     may egress from arbitrary container addresses — that path is protected by
     the token only.
4. **The allowlist trusts proxy headers.** Client identity is taken from
   `CF-Connecting-IP`, then `X-Forwarded-For`. Those headers are spoofable unless
   the service is reachable *only* through your tunnel, so do not expose the
   plain HTTP port to a network where a client could send them directly.
5. **Terminate TLS at the tunnel.** The app itself serves plain HTTP on loopback.
   A quick tunnel URL and a token are both bearer secrets: anyone holding the URL
   plus the token can spend your GPU time and download generated media.
6. **Watch `logs/bridge_access.log`.** It records method, path, status, source IP
   and latency for `/api/bridge` traffic; token mismatches are logged with the
   compared token lengths. Unexpected 403 bursts mean someone is probing the
   tunnel.
7. **Stop the tunnel when unused.** The launcher exposes tunnel start/stop; a
   stopped tunnel is the cheapest mitigation.

`tools/tunnel.ps1` contains an inline example path for the `cloudflared` binary
and local data/log directories. Review and adjust it before use on another
machine.

## Token handling

| Secret | Where it lives | Notes |
|---|---|---|
| Bridge token | `data/bridge_token.txt`, or env `COMFY_BRIDGE_TOKEN` | Auto-generated with `secrets.token_urlsafe(24)` when absent; env value wins. |
| Tunnel token | `data/tunnel.json` (named tunnel mode) | Written by the launcher; treat the file as a credential. |
| IP allowlist | `data/ip_allowlist.json` | Not a secret, but reveals your network layout. |

Rules:

- `data/`, `logs/`, `.env*` are git-ignored. Never commit token files, tunnel
  configuration, or captured request logs.
- Rotate the bridge token by deleting `data/bridge_token.txt` (or changing
  `COMFY_BRIDGE_TOKEN`) and updating the client plugin configuration. Rotating
  invalidates every client that still uses the old value.
- The optional chat plugin stores the token in AstrBot configuration and offers a
  masked `/配置` view; the mask reveals the first and last four characters only.
  Do not paste unmasked values into issues, screenshots or chat logs.
- Compare tokens over TLS only. On loopback, plain HTTP is acceptable; across a
  network it is not.
- Generated media URLs contain the token as a query parameter
  (`/api/bridge/media/{job_id}?token=...`). Treat such URLs as secrets and avoid
  archiving them in shared logs.

## Data at rest

- `data/` holds settings, job records, selected models, host mode, allowlist and
  tokens. Back it up deliberately; delete it when decommissioning a machine.
- `logs/` holds launcher, bridge-access and generation logs. Log rotation and
  retention are operator responsibilities.
- Generated media lives under the ComfyUI output directory and is not managed by
  this project's access control.

## Dependencies and models

- Python dependencies are pinned by minimum version in `requirements.txt`.
  Review upstream advisories for FastAPI, Uvicorn, Requests, psutil, pydantic,
  python-multipart and aiohttp before release.
- ComfyUI, models, LoRA files and ffmpeg binaries are external. A malicious
  model file or custom node is outside this project's control; install only from
  sources you trust and prefer isolated environments.
- This repository never ships model weights (see `NOTICE`).

## Reporting a vulnerability

Please use **GitHub Security Advisories** for this repository:

1. Open the repository's **Security** tab.
2. Choose **Report a vulnerability** (private advisory).
3. Include: affected version or commit, deployment mode (loopback only, tunnel,
   plugin), reproduction steps, expected vs observed behaviour, and impact.
4. If the report involves the optional chat plugin, state the plugin version from
   `optional/astrbot_plugin_comfy_bridge/metadata.yaml`.

Please do not open a public issue for a suspected vulnerability, and do not
include real tokens, tunnel URLs, private host names or personal data in any
report or screenshot. Use placeholders such as `<TOKEN>`, `<TUNNEL_URL>`,
`<HOST>`.

Advisory handling: acknowledgement on a best-effort basis, a fix or mitigation
plan for confirmed issues in the supported version line, and coordinated
disclosure through the advisory once a fix is available.

## Out of scope

- Misconfiguration that deliberately widens exposure (binding to `0.0.0.0`,
  publishing the tunnel with an empty token, disabling the allowlist).
- Vulnerabilities in ComfyUI, model providers, ffmpeg, AstrBot or chat platforms.
- GPU/host damage caused by overriding the resource guard thresholds.
- Content policy questions about generated media; see `NOTICE` for model license
  obligations.

## 远程 Web 访问

远程访问时设置 `ASTER_WEB_TOKEN`。除本机回环地址与 `/login`、`/api/login`、`/api/health` 外，所有 Web 请求需要 `aster_token` Cookie 或 `X-Web-Token` 请求头；桥接接口继续使用独立 `COMFY_BRIDGE_TOKEN`。
