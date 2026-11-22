# AstrBot 本机生图桥（2026-11-15）

目标：AstrBot 服务器作为请求端，把聊天里的提示词发到本机，本机用 ComfyUI 生成图像/视频，
结果回传并由适配器发送给请求人。

## 1. 本机桥 API（webapp :8190）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/bridge/models` | 列出允许的图像/视频模型及默认参数 |
| POST | `/api/bridge/generate` | 同步生成，返回 base64 媒体 |
| GET | `/api/bridge/health` | ComfyUI 是否在线 |

鉴权：`X-Bridge-Token`，token 在 `data/bridge_token.txt`（首次自动生成），可用 `COMFY_BRIDGE_TOKEN` 覆盖。

允许的模板：
- 图像：`moss_image`、`qwen_image`、`h3_image`、`moss_image_edit`、`qwen_image_edit`
- 视频：`minimax_h3_t2v`、`minimax_h3_fl2v`、`minimax_h3_r2v`、`minimax_h3_t2v_720p15s`

`generate` 参数：prompt、template、width、height、steps、cfg、seed、negative_prompt、
duration_s、length、optimize、lora_name、timeout_s（30–3600）。

## 2. AstrBot 插件

目录：`K:\CONFI\astrbot_plugin_comfy_bridge`
- `metadata.yaml`、`_conf_schema.json`、`main.py`、`README.md`
- 指令：`/模型 [名称]`、`/画图`、`/视频`、`/生图帮助`
- 参数：`--model --size --steps --cfg --seed --neg --dur --length`
- 发送：图像 `Comp.Image.fromFileSystem`，视频 `Comp.Video.fromFileSystem`

部署：复制到 AstrBot `data/plugins/`，配置 `base_url` 与 `token`，重载插件。

## 3. 实测

| 场景 | 结果 |
|---|---|
| `GET /api/bridge/models` | 4 图像 + 4 视频模型，含默认分辨率/步数/时长 |
| 图像 `moss_image` 512×512 8步 | 26.2s，返回 base64 PNG |
| 视频 `minimax_h3_t2v` 608×352 22帧 2步 | 149.5s，返回 base64 MP4 |
| 插件 `main.py` | `py_compile` 通过；参数解析/超时/错误回传已实现 |

## 4. 注意

1. 桥是同步等待：一次请求占用一个生成位，长视频不要并发。
2. 对外暴露必须保留 token，并建议 frp/tailscale + 反代限流。
3. 聊天侧暂不支持“带参考图编辑”（ref_images 需要先上传文件）；本机 Web/API 已支持。
4. 720p 15s 直出约 40–60 分钟；聊天场景建议先用 2–5 秒短视频。


## 5. 安全加固与自动映射（2026-11-15 晚）

### Token 轮换
- 新 token 在 `data/bridge_token.txt`（已轮换，旧 token 作废）。
- 插件配置里的 `token` 同步更新。

### IP 白名单（只允许 AstrBot 服务器）
- 配置：`data/ip_allowlist.json`
  ```json
  {"enabled": true, "ips": ["203.0.113.10"]}
  ```
- 中间件读取 `CF-Connecting-IP`（cloudflared）或 `X-Forwarded-For`，非白名单返回 403；
  本机直连（无转发头）始终放行，便于本地调试。
- 若 AstrBot 出口 IP 变化，改这个文件即可（热生效，无需重启）。

### 端口映射自动启停
- 脚本：`tools/tunnel.ps1 start|stop|status`
- cloudflared 路径：`C:\Tools\cloudflared\cloudflared.exe`（已解除锁定并加入用户 PATH）
- 启动项目（`start_all.ps1` 或启动器「启动」）时自动执行 `start`，URL 写入 `data/tunnel_url.txt`；
- 启动器「停止（全部）」会调用 `stop`；`data/tunnel.pid` 记录进程。

当前一次启动的 Quick Tunnel：
- URL：见 `data/tunnel_url.txt`（每次启动会变）
- 实测：本地 200、伪造非白名单 IP 403、白名单 IP 200；经隧道从非白名单公网访问 403。

> 需要**固定域名**时改用命名隧道：`cloudflared tunnel login` → `create` → `route dns`，
> 然后 `cloudflared tunnel run --token <TOKEN>`（或 service install），插件 base_url 填该域名。


## 6. Web 可视化：固定域名隧道（2026-11-15 晚）

启动器新增「☁ 隧道」页（`http://127.0.0.1:8191` → 隧道）：

- 状态/地址/进程 PID 实时显示
- 模式：Quick（免费，URL 每次变）/ 命名隧道（固定域名）
- 固定域名一键流程：
  1. 点「① 登录 Cloudflare」→ 浏览器授权，`~/.cloudflared/cert.pem` 生成；
  2. 填域名（如 `bridge.example.com`，需托管在 Cloudflare）与隧道名；
  3. 点「② 创建固定域名隧道」→ 自动 `tunnel create` → `route dns` → `tunnel token` → 写配置并重启映射；
  4. 状态显示固定 https 地址。
- 也支持手动粘贴 Tunnel Token + 域名保存。
- 同页可编辑 IP 白名单（热生效）。

接口：`/api/tunnel`、`/api/tunnel/action/{start|stop|restart}`、`/api/tunnel/test`、
`/api/tunnel/login`、`/api/tunnel/login/status`、`/api/tunnel/setup`、`/api/security/allowlist`。

cloudflared 连接已强制 `--protocol http2`（本机 QUIC 偶发超时，http2 稳定）。

当前 Quick Tunnel（本次）：见 `data/tunnel_url.txt`。


## 7. 全局单任务锁（2026-11-15）

- 文件锁：`data/global_job.lock`（PID + 来源 + 模板 + 开始时间），跨 MCP / Web / 桥 / 长片工具生效。
- 行为：已有任务占用时，新请求直接返回
  `{"ok": false, "busy": true, "error": "请等待上一条任务完成（当前：moss_image · bridge · 开始于 ...）"}`，
  不再排进 ComfyUI 队列。
- 释放：任务完成/失败/取消自动释放；后台 `lock-watch` 线程兜底；进程退出后按 PID 判活清理；超过 6 小时视为陈旧。
- 覆盖入口：MCP `tool_generate`、Web `/api/generate`、`/api/bridge/generate`、Web 长片工具 `segment_film.py`、`h3_720p15s.py`。
- 实测：bridge 任务进行中再请求 → busy；完成后释放；跨入口（bridge → /api/generate）同样返回 busy；隧道连接不受影响。


## 8. 固定域名 example.com 排查（2026-11-15）

已创建并连通命名隧道：
- 隧道：`comfy-bridge`，ID `20b50811-d591-444b-afe4-d2d76b633ab2`
- 本机 ingress：`C:\Users\<user>\.cloudflared\config.yml`，`bridge.example.com → http://127.0.0.1:8190`
- 运行方式：`mode=named`，`cloudflared tunnel run`（pid 见 `data/tunnel.pid`）

阻塞点：**域名 DNS 不在 Cloudflare**
- `example.com` 的 NS 是 `dns3/dns4.hichina.com`（阿里云），公网 A 记录指向 `203.0.113.10`（nginx）。
- Cloudflare 里虽然已添加 `bridge.example.com` 的 CNAME，但公网解析不经过 Cloudflare，`bridge.example.com` 目前 NXDOMAIN。
- 顶级域 `example.com` 在 Cloudflare 已存在 A 记录，`route dns` 报 1003（同名记录冲突），因此固定域名请用子域 `bridge.example.com`。

解决：在阿里云 DNS 把 NS 改为 Cloudflare 分配的两个 NS（Cloudflare 控制台 Zone Overview 可见），等待生效后：
- `https://bridge.example.com` → Cloudflare → 隧道 → 本机 8190；
- 插件 `base_url` 填该地址；IP 白名单仍需放行 AstrBot 服务器出口 IP。


## 9. 临时切回 Quick（2026-11-15 晚）

- 域名 NS 已改 Cloudflare，等待生效（12–24h）；期间使用 Quick Tunnel。
- 命名隧道 `comfy-bridge` 保留但停止；`config.yml` 只保留 `bridge.example.com`，**不再占用顶级域 `example.com`**。
- Quick 模式使用独立空配置 `data/cloudflared_quick.yml`，避免默认 `config.yml` 的 ingress 把 Quick 请求 404。
- 当前 Quick 地址见 `data/tunnel_url.txt`；重启会变，生效前插件 `base_url` 用该地址。


## 10. Cloudflare 固定域名验证（2026-11-15 23:10）

- 公网 DNS 已切换到 Cloudflare：`cruz.ns.cloudflare.com` / `drake.ns.cloudflare.com`；
  `bridge.example.com` 解析到 Cloudflare 任播 IP（104.21.42.142 / 172.67.162.129）。
- 顶级域 `example.com` 未被隧道占用；HTTP 访问返回 nginx 的 301（Cloudflare 回源）。
- 但 Cloudflare 边缘 **HTTPS 握手失败**（`SSLV3_ALERT_HANDSHAKE_FAILURE`，apex 与 bridge 均如此），
  说明 Cloudflare 侧 SSL/TLS 尚未就绪（常见：SSL/TLS 模式为 Off，或 Universal SSL 证书仍在签发）。
- 因此暂不切命名隧道，继续 Quick；待 Cloudflare SSL/TLS 设置为 Flexible/Full 且证书生效后再切。


## 11. 固定域名已启用（2026-11-15 23:22）

- Cloudflare SSL 已生效：`example.com` 证书 SAN 含 apex 与 `*.example.com`，TLS1.3。
- 命名隧道 `comfy-bridge` 已切为 `mode=named`，pid 27380，URL `https://bridge.example.com`。
- 验证：
  - `https://bridge.example.com/api/bridge/health` → 403 JSON（本机公网 IP 不在白名单，说明隧道已到达本机桥）；
  - `https://example.com/` → 200 nginx（顶级域未被隧道占用）。
- 插件配置应使用：
  ```json
  {"base_url": "https://bridge.example.com", "token": "<data/bridge_token.txt>"}
  ```
- 启动项目时 `tunnel.ps1 start` 会按 `data/tunnel.json` 的 named 模式自动启动。


## 12. 修复长任务 JSON 解析错误（2026-11-15 23:50）

现象：客户端报 `Expecting value: line 1 column 1 (char 0)`，本机实际已生成成功。

原因：Cloudflare 对源站响应有 **100 秒**超时；同步 `/api/bridge/generate` 在长任务（视频/首次加载模型）时被 Cloudflare 返回 524 HTML/空响应，
插件 `r.json()` 解析失败，于是提示生成失败，但本机任务仍在跑完。

修复：
1. 本机新增异步接口：
   - `POST /api/bridge/submit` → 立即返回 `job_id`（后台线程等 ComfyUI 完成）；
   - `GET /api/bridge/result/{job_id}` → `running/done/failed`，done 时返回 base64 媒体。
2. 插件改为 **submit + 2 秒轮询**，每次请求都很短，不会触发 100 秒超时；
3. 非 JSON 响应（524/HTML/空）会转成可读错误：`HTTP 524 非 JSON 响应：...`，不再抛 JSONDecodeError；
4. 旧同步接口保留给本机/局域网调试。

实测：视频 `minimax_h3_t2v` 22 帧 / 2 步，提交后轮询 **147 秒**拿到 mp4（180KB base64），全局锁自动释放；
图像 512×512/8 步 38 秒完成。


## 13. 任务完成不返回结果修复（2026-11-16）

原因：结果在消息处理器内等待并发送，长任务（尤其视频/首次加载）期间处理器可能被平台或 AstrBot 超时取消，
本地任务已完成但聊天收不到结果。

修复（插件 v1.5.0）：
- 处理器只做 submit，立即返回“已提交”；
- 后台 asyncio 任务轮询 result，完成后用 `context.send_message(umo, MessageChain)` 主动推送；
- 图片用 `MessageChain().message(caption).file_image(path)`，视频用组件链；
- 失败主动发错误文本，日志记录 `bridge delivered` / `deliver failed`；
- 本机 result 接口补充 width/height/duration，标题不再出现 None。


## 14. 跨容器媒体发送修复（2026-11-16）

现象：AstrBot 日志 `ENOENT ... /tmp/astrbot_comfy_bridge/xxx.mp4`，NapCat 打不开 AstrBot 容器里的文件。

原因：插件把媒体存到 AstrBot 容器 `/tmp`，再用 `Image/Video.fromFileSystem` 把**本地路径**交给 NapCat；
NapCat 在另一个容器/进程，路径不可见。

修复（插件 v1.6.0）：
- 本机新增 `GET /api/bridge/media/{job_id}?token=...`，直接流式返回图片/视频；
- 插件完成后用 `Comp.Image.fromURL(url)` / `Comp.Video.fromURL(url)` 发送**公网 URL**，由 NapCat 自行下载；
- 媒体路径不参与 IP 白名单（靠 token 校验），避免 NapCat 容器出口 IP 不同被 403；
- 实测：本地媒体 200（276KB png），经 `https://bridge.example.com` 同样 200。


## 15. 发送失败 / 详情页乱码 / 缓存清理（2026-11-16）

- **发送失败**：`MessageChain([...])` 构造在部分 AstrBot 版本不可靠；改为
  `chain = MessageChain()` 后 `chain.chain.append(comp)` + `append(Plain)`。
  同时保留 URL 发送（跨容器）。
- **详情页乱码**：`metadata.yaml` 曾被 PowerShell 以错误编码写回，内容变成
  “鏈満鐢熷浘妗?” 并带 BOM；已重写为 UTF-8 无 BOM 的正常中文，版本 v1.7.0。
- **缓存清理**：插件启动清理一次，后台每 6 小时检查，删除超过 24 小时的媒体；
  可用 `cache_max_age_hours` / `cache_cleanup_interval_hours` 调整。
- 自测：48h 前文件被删除、24h 内保留；交付链为 `[Image(url), Plain(caption)]`。


## 16. 媒体 403 / 下载器丢 query 修复（2026-11-16）

现象：AstrBot 下载 `.../api/bridge/media/<job_id>?token=...` 返回 403，提示 host=bridge... file='<job_id>'。

原因：部分下载器把 URL 最后一段当文件名并**丢弃 query**，token 丢失 → 403。

修复（插件 v1.7.1）：
- 提交任务时生成随机 `media_key`；
- 新增路径式地址：`/api/bridge/media/{job_id}/{media_key}`（无 query）；
- result 接口返回 `media_key`，插件用它拼 URL；
- 旧 `?token=` 方式保留兼容；
- 媒体路径仍免 IP 白名单（靠 key 校验）。

实测：本机与 `https://bridge.example.com` 路径式地址均 200（image/png 276KB）。
