# MiniMax H3 图像生成工作流（minimax_h3_image）

> 2026-11-15 新增。用现有 H3 omni-modal 模型（fl2va int8）生成**静帧 PNG**，无需额外图像模型。
> 实现在 `templates/minimax_h3_image.api.json` + `workflow.py` 的 `TEMPLATES["minimax_h3_image"]`。

## 1. 原理

H3 是视频扩散模型（17k+5 帧网格，24fps）。图像生成走"最小帧数"路径：

```
MiniMaxH3ImageToVideo（length=5，即 17×0+5 的最小合法帧数）
  → SamplerCustomAdvanced（8 步）
  → VAEDecode（视频 VAE 解码 5 帧）
  → ImageFromBatch（取第 0 帧）
  → SaveImage（PNG）
```

不接 AudioVAE / CreateVideo，因此**不产出音频与视频**，只输出单张 PNG。

## 2. 节点链路（15 节点）

| id | 节点 | 说明 |
|---|---|---|
| 1 | UNETLoader | `minimax_h3_fl2va_pruned_int8_convrot.safetensors`（可覆盖） |
| 2 | CLIPLoader | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`，type=minimax |
| 3 | VAELoader | `minimax_h3_video_vae_fp16.safetensors` |
| 5/6/7/8/9 | RandomNoise / KSamplerSelect / BasicScheduler / BasicGuider / SamplerCustomAdvanced | 采样链 |
| 10 | **MiniMaxH3ImageToVideo** | 提示词 + 宽高 + length（默认 5）；支持可选 first_frame |
| 11 | VAEDecode | 视频 VAE 解码 |
| 13 | **ImageFromBatch** | batch_index=0、length=1，只取首帧 |
| 12 | **SaveImage** | 输出 PNG，前缀 `image/MiniMax_H3` |
| 200/201/202 | optimize 补丁 | 仅 optimize≠off 时由 resolve 自动插入 |

## 3. 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `prompt` | — | 必填 |
| `width` / `height` | **768×768** | 未显式给尺寸且未给 megapixels/aspect 时用方形 768 |
| `duration_s` / `length` | 0.2s → **5 帧** | 模板上限 39 帧（0.83s），超过会截断；给大帧数只会生成更多帧，最终仍只保存第 1 帧 |
| `steps` | profile（draft=4） | 推荐 **8** |
| `optimize` | off | 推荐 **max**（Sage+LowVRAM+ChunkFF，8GB 显存友好） |
| `first_frame` | null | 可选：input 目录图片名，作为构图/起点（图生图风格） |
| `lora_name` / `lora_strength` | null / 1.0 | 可选 turbo LoRA |
| `unet_name` / `clip_name` / `vae_name` | 模板默认 | 模型覆盖 |
| `seed` | 随机 | 固定可复现 |
| `output_prefix` | `image/MiniMax_H3` | 输出路径前缀（output/image/） |

**禁止**：`two_pass`（图像流程无音频/视频后处理链，传了会直接报错）。

## 4. 用法

### MCP 工具

```json
{
  "template": "minimax_h3_image",
  "params": {
    "prompt": "cinematic still, a red apple on a rustic wooden table by a window, soft morning light, photorealistic, film grain",
    "width": 768, "height": 768,
    "steps": 8, "optimize": "max",
    "duration_s": 0.2,
    "seed": 2026111501
  }
}
```

### Web UI

重启 uvicorn 后，创作页模板下拉会出现 **「MiniMax H3 图像生成 (still)」**；选它、填提示词即可。输出在 `ComfyUI/output/image/`，工作台「我的作品」中按图片展示。

### 命令行（REST）

```powershell
$body = @{ template='minimax_h3_image'; params=@{ prompt='a lighthouse at dusk, cinematic'; width=768; height=768; steps=8; optimize='max'; duration_s=0.2 } } | ConvertTo-Json -Depth 6
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8190/api/generate -ContentType application/json -Body $body
```

## 5. 实测（2026-11-15，RTX 4070 Laptop 8GB）

| 配置 | 结果 |
|---|---|
| 768×768 / 5 帧 / 8 步 / optimize=max | 采样 20–44s；含模型加载总 **180–190s**；输出 1 张 PNG（约 600–800KB） |
| 质量抽检 | "红苹果木桌"、"黄昏灯塔" 两张写实度良好，构图/光影符合提示词 |
| 显存 | 峰值约 6.2GB（优化链下），未触发 OOM |
| 批量 | 每次任务输出 **1 张** PNG（ImageFromBatch 已裁掉其余帧） |

> 说明：H3 训练区间是 124–362 帧，5 帧属于"最小可行静帧"，模型会给出单帧画面但细节丰富度低于视频首帧；如果追求更高画质，可把 `length` 提到 39 或 124（耗时增加），模板仍只保存第 1 帧。

## 6. 限制与注意事项

1. **不是独立图像模型**：目前模型库里没有 H3 图像专用 checkpoint，本工作流是"用视频模型出静帧"；后续若有图像专用模型，只需替换模板里的 UNET/VAE 节点。
2. 5 帧为 H3 合法最小值（17k+5 网格），低于 5 帧会被 snap 回 5。
3. 高分辨率上限：H3 原生短边 768、最大约 768×1344；再高画质收益有限且显存风险上升。
4. 无音频输出（不需要 AudioVAE）。
5. `optimize=max` 在本机 8GB 上最稳；`off` 更省内存但更慢/更吃显存。

## 7. 相关文件

- 模板：`templates/minimax_h3_image.api.json`
- 注册与解析：`workflow.py`（`TEMPLATES["minimax_h3_image"]`、`_TEMPLATE_PARAMETERS["minimax_h3_image"]`、`resolve()` 的 SaveImage 兼容与 two_pass 拦截）
- 策略：`docs/GENERATION_POLICY_20261111.md`（分辨率/步数/显存规则同样适用）
