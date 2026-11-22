# MOSS-Image 接入记录（2026-11-15）

来源：OpenMOSS 的 **MOSS-Image**（5B 单流 DiT，Apache-2.0），ComfyUI 原生支持。
文档：[ComfyUI MOSS-Image 教程](https://docs.comfy.org/zh/tutorials/image/moss-image/moss-image) ·
模型：[Comfy-Org/MOSS-Image](https://huggingface.co/Comfy-Org/MOSS-Image)

## 1. 模型文件（已下载到本地）

| 用途 | 文件 | 大小 |
|---|---|---|
| 扩散模型（SFT, int8） | `models/diffusion_models/moss_image_sft_int8_convrot.safetensors` | 5.04GB |
| 文本编码器 | `models/text_encoders/qwen3.5_9b_w4a8_gptq.safetensors`（CLIPLoader type=`moss_image`） | 7.34GB |
| VAE | `models/vae/flux2-vae_small.safetensors`（FLUX.2 small） | 0.25GB |

可选：`moss_image_edit_lora_rank_64_bf16.safetensors`（编辑）、`moss_image_color_depth_control.safetensors` + MoGe（深度/颜色控制）。下载脚本：`tools/download_moss.py`。

## 2. 工作流（`templates/moss_image.api.json`）

```
UNETLoader(moss_image_sft_int8)
  → SkipLayerGuidanceDiTSimple(7,8,9)
CLIPLoader(qwen3.5_9b_w4a8_gptq, type=moss_image)
  → CLIPTextEncode Positive / Negative
EmptyFlux2LatentImage(width,height)
  → KSampler(er_sde, simple, steps=25, cfg=2.5, denoise=1)
  → VAEDecode(flux2-vae_small) → SaveImage(image/MOSS_Image)
```

- 官方 t2i 模板还带提示词增强器（同一 Qwen3.5 编码器 + TextGenerate），本模板默认关闭，直接使用用户提示词，避免改写。
- MOSS 擅长中英文字体与排版、长提示词（约 4K token）；分辨率为 16 对齐。

## 3. 已做的适配

- `workflow.py`：注册 `moss_image` 模板（kind=`image_moss`），新增 `_find_moss_node`（按 Positive/Negative 标题定位）、KSampler 兼容分支（seed/steps/cfg/sampler/scheduler）、`EmptyFlux2LatentImage` 宽高注入；`supports_optimize=false`、`two_pass` 拒绝。
- Web「生图」页：模式新增 **MOSS 文生图**，默认 1024×1024 / 25 步，新增 **CFG** 输入（默认 2.5，仅 MOSS 可用），负面提示词可用。
- 守卫：图像模板上限 2048×2048，与 Qwen 一致；不触发 H3 的 unlimited 规则。

## 4. 用法

```json
{ "template": "moss_image",
  "params": { "prompt": "poster with the word MOSS in bold letters, teal and cream",
              "width": 1024, "height": 1024, "steps": 25, "cfg": 2.5 } }
```

Web：生图页 → 模式「MOSS 文生图」→ 填提示词 → 生成；输出 `ComfyUI/output/image/MOSS_Image_*.png`。

## 5. 实测（2026-11-15）

- 首张 1024×1024 / 25 步 / cfg 2.5：**50 秒完成**（含模型+TE 首次加载），显存峰值 6.8GB。
- 提示词中的英文大字与副标题渲染准确（见 `ComfyUI/output/image/MOSS_Image_00001_.png`）。
- Web `/api/generate` 冒烟（512×512 / 8 步）：`guard.ok=true risk=low`，任务 completed。
- 下载说明：huggingface.co 在本机不可直连，脚本已改用 `hf-mirror.com` 并行分段；`tools/download_moss.py` 可直接复用。

## 6. 注意

1. 文本编码器 7.34GB + 模型 5.04GB：首次加载较慢，8GB 显存靠动态卸载，建议不要与视频任务并发。
2. `er_sde` 采样器 + cfg 2.5 为官方配置，改动会影响排版质量。
3. 需要图像编辑/布局时，再接入官方 edit LoRA 与 `MOSSImageLayoutPrompt` 节点（本机已具备节点）。


## 7. MOSS 图像编辑（2026-11-15）

| 项 | 内容 |
|---|---|
| 编辑 LoRA | `models/loras/moss_image_edit_lora_rank_64_bf16.safetensors`（283MB） |
| 模板 | `templates/moss_image_edit.api.json` / 模板名 `moss_image_edit` |
| 工作流 | LoadImage → ImageScaleToTotalPixels(1.0MP,16) → GetImageSize → EmptyFlux2LatentImage；TextEncodeMOSSImageEdit(正/负, edit_format=Moss-Image-Edit) → KSampler(30 步, cfg 4.0, euler, normal) → VAEDecode |
| 默认 | 30 步 / cfg 4.0 / euler / normal；参考图缩放到 1.0MP（16 对齐） |
| Web | 生图页新增「MOSS 编辑」模式：上传 1 张源图 + 填写“只改什么”的指令 |

实测：
- 源图 `MOSS_Image_00001_.png`（MOSS 海报）→ “背景改为深海军蓝，保留文字与构图”：**70 秒**，背景正确替换，主标题保留。
- 同图 → “背景改为日落橙”：背景替换成功，但小号副标题出现字形扭曲（编辑模型对细小文字会重绘）。
- Web `/api/generate`（moss_image_edit）冒烟通过，输出 `MOSS_Edit_00001/00002_.png`。

注意：
1. 指令只描述要改的部分，并明确“保持人物/构图/文字不变”；官方推荐句式：`Change only the X to Y. Keep ... unchanged.`
2. cfg 保持 4.0；过低容易凭空出现饰品/文字。
3. 参考图按 1.0MP 进入模型；降到 0.26MP 会更省但容易重构图。
4. 小字号文字可能被重绘，重要排版建议后期叠加或减少编辑强度。
