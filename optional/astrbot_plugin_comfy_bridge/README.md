# astrbot_plugin_comfy_bridge · 本机生成桥插件

AstrBot 插件：把聊天中的提示词转发到本机 Aster Studio / ComfyUI，生成图像或视频后回传并发送给请求者。推理与算力均留在本机，插件只负责指令解析、请求提交与结果投递。

> 本目录为可选组件源码，默认不安装、不参与主流程。安装方式见 [../README.md](../README.md)。

## 一、指令总览

| 指令 | 作用 |
|---|---|
| `/模型` | 列出可用图像/视频模型与默认分辨率、步数、时长 |
| `/模型 <名称>` | 查看单个模型的默认值与可调参数 |
| `/画图 [参数] 提示词` | 用本机模型生成图像 |
| `/视频 [参数] 提示词` | 生成视频 |
| `/配置` | 查看当前插件设置（地址、令牌脱敏、默认值、超时） |
| `/诊断` | 检查桥接口连通性与令牌有效性 |
| `/生图帮助` | 完整帮助（图像 + 视频 + 其他指令） |
| `/画图帮助` / `/视频帮助` | 只看对应参数说明 |

同义/短指令：`/生图`、`/图` = `/画图`；`/生视频`、`/片` = `/视频`。

### 零参数用法

```
/画图 一只戴帽子的猫
/图 海边日落
/片 一只猫在草地上跑
```

无需记忆参数；需要调整时可在描述中加入口语词：

| 说法 | 效果 |
|---|---|
| `竖图` / `竖版` / `手机壁纸` | 768×1344 |
| `横图` / `横版` / `宽屏` | 1344×768 |
| `方图` | 1024×1024 |
| `高清` / `2K` | 未指定方向→1536×1536；已指定方向→提高到 30 步 |
| `快速` | 8 步（视频 4 步） |
| `精细` / `高质量` | 30 步（视频 8 步） |
| `用Qwen` / `用MOSS` / `用H3` | 指定模型 |
| 视频：`竖屏` / `横屏` / `2秒` / `5秒` / `10秒` | 视频尺寸与时长 |

示例：

```
/画图 一只猫竖图高清
/画图 雨夜街头横图快速用Qwen
/片 海边日落竖屏高清2秒
```

高级用法仍可使用 `--model/--size/--steps` 等参数，显式参数优先于口语词。

## 二、图像参数（`/画图`）

| 参数 | 中文别名 | 类型 | 默认 | 说明 |
|---|---|---|---|---|
| `--model` | `--模型` | 字符串 | `moss_image` | `moss_image`、`qwen_image`、`h3_image`、`moss_image_edit`、`qwen_image_edit` |
| `--size` | `--尺寸` | WxH | 模型默认 | 16 的倍数，如 `1024x1024` |
| `--steps` | `--步数` | 整数 | 模型默认 | MOSS/Qwen 25，编辑 30；越大越慢 |
| `--cfg` | — | 浮点 | 模型默认 | MOSS 2.5，MOSS 编辑 4.0；Qwen 通常 4.0 |
| `--seed` | `--种子` | 整数 | 随机 | 复现同一张图 |
| `--neg` | `--负面` | 文本 | 编辑有官方默认 | 负面提示词 |

示例：

```
/画图 --model qwen_image --size 1024x1024 --steps 30 雨夜赛博朋克街头
/画图 --model moss_image --size 1024x1024 极简海报，青绿色衬线字
```

编辑模式需要源图；聊天流程当前未接入图片上传，图生图请使用 Web 工作台或 `/api/edit`。

## 三、视频参数（`/视频`）

| 参数 | 中文别名 | 类型 | 默认 | 说明 |
|---|---|---|---|---|
| `--model` | `--模型` | 字符串 | `minimax_h3_t2v` | `minimax_h3_t2v`、`minimax_h3_t2v_720p15s`、`minimax_h3_fl2v`、`minimax_h3_r2v` |
| `--size` | `--尺寸` | WxH | `608x352` | 建议 32 的倍数；720p 用 `1280x736` |
| `--dur` | `--时长` | 秒 | 5 | 720p15s 用 `15` |
| `--length` | `--帧数` | 整数 | — | 优先于 `--dur`；124 帧 ≈ 5 秒 |
| `--steps` | `--步数` | 整数 | 模型默认 8 | turbo 可 4；画质优先 8 |
| `--cfg` | — | 浮点 | 模型默认 | 文本引导强度，通常 1 |
| `--seed` | `--种子` | 整数 | 随机 | 复现 |

示例：

```
/视频 --model minimax_h3_t2v --size 608x352 --dur 2 红苹果在木桌上，镜头缓慢推进
/视频 --model minimax_h3_t2v_720p15s --dur 15 电影感海岸线航拍
```

服务端为全局单任务：并发提交会返回"请等待上一条任务完成"。

## 四、插件配置（AstrBot WebUI 可改）

| 配置项 | 默认 | 说明 |
|---|---|---|
| `base_url` | `http://127.0.0.1:8190` | 本机桥地址；同机用回环地址，跨机填 `<本机地址>:8190`，公网经隧道填 `<BRIDGE_URL>` |
| `token` | 空（读取服务端 `data/bridge_token.txt`） | 桥接令牌，secret；也可由服务端环境变量 `COMFY_BRIDGE_TOKEN` 覆盖 |
| `default_image_template` | `moss_image` | 默认图像模型 |
| `default_video_template` | `minimax_h3_t2v` | 默认视频模型 |
| `image_size` / `video_size` | 空 | 如 `1024x1024` / `608x352`，留空用模型默认 |
| `steps` / `cfg` | 0 | 0 = 用模型默认 |
| `duration_s` | 5 | 默认视频时长（0 = 模型默认） |
| `image_timeout_s` / `video_timeout_s` | 300 / 1800 | 请求超时（秒） |
| `send_progress` | 开 | 生成前先回一条"已提交"提示 |
| `max_prompt_chars` | 2000 | 提示词长度上限 |
| `cache_max_age_hours` | 24 | 本地缓存保留时长 |
| `cache_cleanup_interval_hours` | 6 | 缓存清理检查间隔 |

部署时请把 `<BRIDGE_URL>` 替换为自己的隧道域名，并确认该地址只能访问 `/api/bridge/*`；见项目根 [SECURITY.md](../../SECURITY.md)。

## 五、错误与排查

| 返回 | 原因 | 处理 |
|---|---|---|
| `ip not allowed: <IP>` | 服务端 IP 白名单拦截 | 将 AstrBot 出口 IP 加入服务端 `data/ip_allowlist.json` |
| `invalid token` | 令牌不匹配 | 核对插件 `token` 与服务端 `data/bridge_token.txt` 或 `COMFY_BRIDGE_TOKEN` |
| `请等待上一条任务完成…` | 全局单任务锁被占用 | 等待当前任务结束 |
| `生成超时` | 超过插件配置超时 | 调大 `video_timeout_s` 或缩短时长 |
| 隧道未运行 | cloudflared 未启动 | 在启动器「隧道」页启动 |
| `404` / 空响应 | 地址指向错误 hostname 或路径 | 使用部署者提供的 `<BRIDGE_HOST>`，不要使用顶级域 |

## 六、架构

```
用户 ──/画图 提示词──▶ AstrBot（适配器）──HTTPS──▶ <BRIDGE_HOST>
                                                   │
                                         ComfyUI :8188
                                                   │
用户 ◀──图像/视频──────── AstrBot ◀──媒体 URL──────┘
```

服务端桥接口：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/bridge/models` | 列出图像/视频模型与默认参数（需令牌） |
| POST | `/api/bridge/submit` | 异步提交，立即返回 `job_id`（推荐，规避隧道 100s 超时） |
| GET | `/api/bridge/result/{job_id}` | 轮询结果；`done` 时返回媒体（base64） |
| GET | `/api/bridge/media/{job_id}?token=...` | 令牌化媒体下载，供协议端直接拉取 |
| POST | `/api/bridge/generate` | 同步生成（仅本机/局域网，长任务易触发隧道超时） |
| GET | `/api/bridge/health` | 健康检查 |

## 七、结果投递与缓存

- `/画图` / `/视频` 只负责提交并立即回复"已提交"；
- 后台任务轮询 `/api/bridge/result/{job_id}`，完成后主动发送媒体 URL；
- 媒体通过 `/api/bridge/media/{job_id}?token=...` 由协议端（如 NapCat）直接下载，不传递本地文件路径，适配跨容器部署；
- 失败时主动发送错误文本，插件日志记录投递结果；
- 缓存目录位于系统临时目录下的 `astrbot_comfy_bridge/`，启动时清理一次，之后按 `cache_cleanup_interval_hours` 检查并删除超过 `cache_max_age_hours` 的文件；媒体既已通过 URL 投递，缓存仅用于排查。

## 八、文件

```
astrbot_plugin_comfy_bridge/
  metadata.yaml        # 插件元数据
  _conf_schema.json    # WebUI 配置项与提示
  main.py              # 指令与投递实现
  requirements.txt     # 仅依赖 AstrBot 自带 aiohttp
  README.md
```

## 九、许可与安全

- 插件源码随主项目以 Apache License 2.0 提供（见项目根 [LICENSE](../../LICENSE)）；AstrBot 及其依赖遵循其自身许可。
- 令牌为 bearer 凭据：不要提交到版本库、不要粘贴到公开渠道；轮换方式见项目根 [SECURITY.md](../../SECURITY.md)。
- 本目录中的地址、主机名与 IP 均为占位示例（`<BRIDGE_URL>`、`<BRIDGE_HOST>`、`203.0.113.10`），部署时需替换为实际值。
