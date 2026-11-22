# Qwen-Image 2.1 文生图工作流（qwen_image）

> 2026-11-15 新增。基于 ComfyUI **v0.45.3**（原 v0.33.1 无 Qwen-Image 2.1 节点，已升级）。
> 模板：`templates/qwen_image.api.json`；注册：`workflow.py` 的 `TEMPLATES["qwen_image"]`。

## 1. 模型文件（已下载到本地）

| 用途 | 文件 | 大小 |
|---|---|---|
| 扩散模型 | `models/diffusion_models/qwen_image_2.1_int8_convrot.safetensors` | 6.76GB |
| 文本编码器 | `models/text_encoders/qwen3vl_8b_int8_convrot.safetensors`（type=`qwen_image`） | 8.71GB |
| VAE | `models/vae/qwen_image_2.1_vae_bf16.safetensors` | 0.63GB |

来源：`Comfy-Org/Qwen-Image-2.1`（HuggingFace）。下载脚本：`tools/download_qwen21.py`（多线程分段、断点续传，~15MB/s）。可选：`--turbo` 追加 turbo 模型与 5 步 LoRA。

## 2. 工作流结构（12 节点）

```
UNETLoader(qwen_image_2.1_int8_convrot)
CLIPLoader(qwen3vl_8b_int8_convrot, type=qwen_image)
VAELoader(qwen_image_2.1_vae_bf16)
TextEncodeQwenImage21(prompt, negative_prompt, vae, resolution)
EmptyLatentImage(width, height)          # 宽高由 resolve 注入
RandomNoise(seed) / KSamplerSelect(euler) / BasicScheduler(simple, steps)
BasicGuider(model, positive)             # 等价 cfg=1
SamplerCustomAdvanced → VAEDecode → SaveImage(PNG)
```

## 3. 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `prompt` | 必填 | 提示词 |
| `width` / `height` | 1024×1024 | 未指定尺寸时用 1024 方形；32 对齐 |
| `steps` | **25** | 模板默认 25（可用 `params.steps` 覆盖） |
| `sampler` / `scheduler` | **euler / simple** | Qwen 官方配置 |
| `seed` | 随机 | |
| `negative_prompt` | "" | 写入 TextEncodeQwenImage21；当前 cfg=1 下不生效 |
| `lora_name` / `lora_strength` | null / 1.0 | 例：`p_q_image_2.1_5_steps_v0.1_comfyui_bf16.safetensors` 配 turbo 模型可 5–8 步 |
| `unet_name` / `clip_name` / `vae_name` | 模板默认 | 模型覆盖 |
| `output_prefix` | `image/Qwen_Image_2.1` | 输出到 `ComfyUI/output/image/` |

不支持：`two_pass`（非 H3 视频模板，传了直接报错）、`optimize`（H3 补丁链，传入会被忽略并 warning）、`first_frame`/`ref_images`（当前 t2i 模板未启用参考图；Qwen 2.1 的编辑能力可通过 `TextEncodeQwenImage21.images` 扩展）。

## 4. 实测

| 项 | 数据 |
|---|---|
| 配置 | 1024×1024 / 25 步 / euler / simple / int8 模型 |
| 耗时 | **51 秒**（含模型加载；采样约 1.15 it/s） |
| 输出 | `Qwen_Image_2.1_00001_.png`，1024×1024，约 1.7MB |
| 画质 | 写实静物（红苹果木桌窗光）细节/光影正常 |
| 显存 | 8GB 下可用（动态显存 offload） |

## 5. 用法

```json
{
  "template": "qwen_image",
  "params": {
    "prompt": "a red apple on a rustic wooden table by a window, soft morning light, photorealistic, film grain",
    "width": 1024, "height": 1024, "steps": 25, "seed": 2026111501
  }
}
```

- Web 工作台：创作页模板下拉选择「Qwen-Image 2.1 文生图」。
- CLI：`python tools/gen_image.py --template qwen_image --params-file params.json`
- REST：`POST /api/generate`，body 同上。

## 6. 升级说明（重要）

本机 ComfyUI 已从 **v0.33.1 升级到 v0.45.3**（Qwen-Image 2.1 节点只在新版提供）：

- 代码备份：`K:\CONFI\mian\_comfy_backup_v0331_code.zip`（升级前 953 个代码文件）
- 依赖快照：`logs/pip_freeze_before_v045.txt`
- 依赖更新：`comfyui-frontend-package 1.58.5`、`comfyui-workflow-templates 0.11.107`、`comfyui-embedded-docs 0.5.16`、`comfy-aimdo 0.6.0`、`comfy-kitchen 0.2.41`（torch 2.13/cu130 未变）
- 资产库迁移：新版重建了 `user/comfyui.db` 资产目录，旧库保留在 `user/comfyui.db.bkp`
- 回滚：解压上述 zip 覆盖代码 + `pip install -r` 旧版本（见 freeze 快照），必要时 `copy user\comfyui.db.bkp user\comfyui.db`

## 7. 已知限制

1. 8GB 显存下 2K（2048）输出会显著变慢/可能 OOM，建议 1024 起步。
2. `negative_prompt` 在 cfg=1 流程中不影响结果（官方同款配置）。
3. turbo LoRA 可把步数压到 5–8，但质量/细节会下降。
4. RGBA 透明输出：Qwen-Image 2.1 模型支持 alpha；当前 SaveImage 默认 PNG，若需透明需确认 VAE 解码输出为 RGBA（后续可加「SaveImageAdvanced bit_depth/format」参数验证）。


## 8. 图像编辑（qwen_image_edit）

同一套 Qwen-Image 2.1 权重，走 `TextEncodeQwenImage21` 的 **images autogrow**（`image_1..image_16`，最多用 10 张）：

```
UNETLoader / CLIPLoader(qwen_image) / VAELoader
TextEncodeQwenImage21(prompt=编辑指令, images.image_1..N ← LoadImage, vae, resolution)
SamplerCustomAdvanced(latent_image = 条件节点输出 [4,2]，即参考图 latent)
→ VAEDecode → SaveImage(image/Qwen_Image_2.1_edit_*.png)
```

- 参数：`images: list[string]`（input 目录文件名，1–10 张）、`prompt` 写编辑指令、`width/height` 决定 `resolution`（默认 1024）。
- autogrow 槽位键名必须是 **`images.image_1` …**（按 schema 的 names；`images.0` 之类不会真正进入参考图序列）。
- 实测：原图（苹果木桌）→ 水彩（42s）、铅笔素描；构图保留、风格化正确。
- 用法：

```json
{
  "template": "qwen_image_edit",
  "params": {
    "prompt": "turn the apple into a soft watercolor painting, keep composition",
    "images": ["qwen21_apple_1024.png"],
    "width": 1024, "height": 1024, "steps": 25
  }
}
```

## 9. Web UI 变更（2026-11-15）

- 模式条新增：**文生图 · Qwen**、**图像编辑 · Qwen**、**H3 静帧**；Qwen 模式自动 `steps=25`、`1024×1024`，并隐藏时长/二采，禁用 H3 的 optimize 与 LoRA 下拉。
- 分辨率预设新增 `768×768`、`1024×1024`。
- 图像编辑模式复用参考图上传（最多 10 张），提交时写入 `images` 字段。
- 结果面板新增 `<img>` 展示：输出是 PNG/JPG/WEBP 时显示图片，视频仍用 `<video>`。
- 作品页合并图片：新增 **`/api/images`**（output + artifacts 下的 png/jpg/jpeg/webp），卡片按扩展名用 `<img>` 渲染；点击图片在新标签打开（视频仍走播放器）。


## 10. 安全守卫与分辨率上限（2026-11-15 修正）

- 旧守卫对所有模板用视频口径 `MAX_PIXELS=1920×1088≈2.09MP`，Qwen 的 2K（2048²=4.19MP）会被判 `high` 并返回
  `resource_guard · estimated VRAM risk is high`（网页显示「不可用」）。
- 现按模板 `kind` 区分：**单帧图像模板上限 2048×2048（4.19MP）**，视频模板仍为 1920×1088。
- 实测（8GB，int8 模型）：
  - 1024² / 25 步：51s，PNG 1.7MB
  - 1536² / 25 步：78s，PNG 2.9MB，峰值 VRAM ~6.4GB
  - 2048² / 8 步：66s，PNG 4.7MB，峰值 VRAM ~6.3GB
- Web UI：分辨率预设新增 `1536×1536`、`2048×2048`；守卫拒绝时会显示像素数与上限，并提供「用 1024×1024 重试」链接。
- 仍会拦截：视频 >1920×1088、图片 >2048²、显存/RAM 低于阈值、已有并发任务。


## 11. 专用「生图」页面（2026-11-15）

视频创作页与图像生成已分离：左侧导航新增 **生图**（`/studio?tab=image`），不再复用视频表单。

- 形式：模式（文生图 / 图像编辑 / H3 静帧）→ 提示词或编辑指令 → 尺寸预设（1024/1536/2K/768/横竖/自定义）+ 宽高 + 步数 + 种子；编辑模式出现参考图上传（≤10 张）。
- 高级参数：负面提示词（cfg=1 不生效）、采样器/调度器、LoRA+强度、UNet/CLIP/VAE 覆盖（从 `/api/models` 填充）。
- 实时进度（生图专用）：阶段、`data-live` 采样 `x/y`、百分比进度条、用时、剩余 ETA、任务号、事件详情；完成后在右侧直接显示 PNG（视频元素隐藏），可「打开原图」。
- 校验按钮：`/api/plan` 显示 validation / guard risk / eta / 尺寸。

## 12. 作品页去堆积（2026-11-15）

- 新增 **`/api/gallery?limit&offset`**：只扫 `output/`，按 mtime 倒序，按 `(文件名, 字节数)` 去重，返回 `{items,total}`。
- 前端分页：首屏 24 张，「加载更多」按需追加；卡片图片用 `<img>`（1:1），视频仍用 `<video>`（16:9）。
- 最近作品取 gallery 前 4；点击图片新标签打开，视频走播放器。


## 13. 作品页分类（2026-11-15）

- 顶部分类 chips：**全部 / 图片 / 视频 / Qwen 文生图 / Qwen 编辑 / H3 静帧 / H3 视频**，每个 chip 带数量。
- 后端 `/api/gallery?category=all|image|video|qwen_t2i|qwen_edit|h3_still|h3_video&limit&offset`，返回 `{items,total,counts}`。
- 分类依据文件名前缀：`Qwen_Image_2.1_edit_*`→编辑、`Qwen_Image_2.1_*`→文生图、`MiniMax_H3_*.png`→H3 静帧、`MiniMax_H3_*.mp4`→H3 视频；其余按扩展名归入图片/视频。
- 分页与筛选组合生效（切换分类重置到第 1 页），`counts` 与 `total` 一致（all = image + video）。


## 14. 图片预览（2026-11-15）

- 作品页/生图页点击图片不再 `window.open` 触发下载，而是打开站内弹窗预览（`#modal` + `#modalImg`，最大 92vw/80vh）。
- 弹窗按钮：**新标签**（`/api/media?...&inline=1`，服务端 `content-disposition: inline`）、**下载**（默认 attachment）、超分/剪辑/关闭。
- 视频仍用 `<video>` 播放；关闭弹窗会清空 img/video 的 src。


## 15. 预览导航与缩放（2026-11-15）

- 弹窗内 **← / →** 在当前作品列表（分类+分页已加载项）中循环切换，顶部显示 `3 / 24 · ← → 切换`。
- **滚轮缩放** 0.5×–4×（以图片中心为原点）；点击图片在 1× / 1.5× / 2.25×… 间循环。
- **Esc** 关闭；关闭时重置缩放与列表状态。
- 切换目标为视频时自动切换 `<video>` 播放，仅图片显示缩放提示。


## 16. 图像编辑卡死复盘（2026-11-15 13:36）

**现象**：编辑任务 `6499abe3`（qwen_image_edit）长时间 `running`，ComfyUI `/queue`、`/system_stats` 全部超时，
MCP 守卫记录 `guard_trip: ComfyUI system_stats failed 3 times in a row`。

**日志证据**（`logs/comfy_stderr.log`）：

```
[INFO] got prompt
[INFO] Model WanVAE prepared ... 644MB Staged
[INFO] Model QwenImage21TEModel_ prepared ... 8916MB Staged   （两次）
[INFO] Model QwenImage21 prepared ... 6920MB Staged
[INFO] Requested to load KVCacheModel
[INFO] Model KVCacheModel prepared ... 7737MB Staged
0%| | 0/25 [00:00<?, ?it/s, Model Initializing ...]        ← 之后无任何输出
```

对照：
- 成功的 1024×1024 编辑：`KVCacheModel ... 9–41MB`，42–65s 完成；
- t2i 2048²：`KVCacheModel ... 74MB`；
- 卡死这次：参考图 1984×1088 RGBA，`KVCacheModel ... 7737MB`。

**根因**：图像编辑时 `QwenImage21Cache` 的 KV cache 随参考图分辨率膨胀；7.7GB cache + 6.9GB 模型 + 8.9GB TE
远超 8GB 显存，动态显存加载在初始化阶段反复换入换出，采样停在 0/25，HTTP 线程被阻塞（表现为服务假死）。
当时模板没有 cache 参数，走的是官方默认 `auto/default(bf16)`。

**修复**（已实施）：

1. `qwen_image_edit` 模板加入 `QwenImage21Cache` 节点，默认 `device=cpu`、`dtype=int8`；
   可用参数 `cache_device`（cpu/auto/gpu/off）、`cache_dtype`（int8/default/int4）覆盖。
2. `resolve()`：编辑模板 `pixels > 1_600_000` 时把参考 `resolution` 钳制到 1536（保留宽高比）并 warning。
3. Web 生图页高级参数新增两项 cache 选择；提交后把该 warning 回显。

**修复后实测**：
- 1984×1088（钳到 1536，输出 2080×1152）：184s 完成，水彩效果正确，VRAM 峰值约 5.7GB；
- 1024×1024 回归：45s 完成，正常；
- 卡死任务已被 MCP 以 `stale: ComfyUI 中不存在该任务` 自动取消。

**经验**：8GB 上编辑建议 ≤1536（面积约 2.3MP），cache 固定 `cpu + int8`；若再遇假死，直接重启 ComfyUI。
