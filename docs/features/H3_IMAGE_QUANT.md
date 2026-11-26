# MiniMax H3 Image 量化版本配置

## 支持的版本

| 目录 | 文件 | 大小 | 适用 |
|---|---|---:|---|
| `models/diffusion_models/` | `MiniMax-H3-Image-Pruned-Q4_K_M.gguf` | 11.6 GB | 8GB 显存 / 32GB 内存，推荐默认 |
| `models/diffusion_models/` | `MiniMax-H3-Image-Pruned-Q2_K.gguf` | 6.9 GB | 更低显存，质量下降 |
| `models/diffusion_models/` | `minimax_h3_image_wavecut_style_pruned_reference_int8_convrot.safetensors` | 21.0 GB | 高显存/大内存，质量优先 |
| `models/vae/` | `minimax_h3_image_vae_fp16.safetensors` | 1.1 GB | H3 Image 必需 |
| `models/text_encoders/` | 现有 `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 14.6 GB | 默认复用 |
| `models/text_encoders/` | `qwen3vl_32b_minimax_h3_int8_convrot.safetensors` | 27.1 GB | 可选高精度编码器 |

模型权重不随项目分发；使用前请阅读 MiniMax H3 Image Community License。

## 工作流

| 模板 | 加载器 | 用途 |
|---|---|---|
| `minimax_h3_image` | `UNETLoader` | INT8 高质量 |
| `minimax_h3_image_gguf` | `UnetLoaderGGUF` | Q4/Q2 GGUF，低显存 |

采样参数：`euler` / `simple` / 6 steps / CFG 1 / denoise 1。
原生 H3 Image 推荐约 4–20MP，默认 2048×2048；显存不足时降低到 1024×1024。

## 下载

```powershell
# 默认：VAE + Q4 GGUF
.\scripts\download_h3_image.ps1 -Variant all -Proxy http://127.0.0.1:7897

# 只下载低配 Q2
.\scripts\download_h3_image.ps1 -Variant q2

# 只下载 INT8 高质量
.\scripts\download_h3_image.ps1 -Variant int8
```

也可以使用 Web「模型」页或 `setup/models.json` 的条目下载。

下载完成后刷新 ComfyUI，`/object_info` 中应出现对应文件；模板校验会通过。

## 配置入口

- 模型目录：`setup/models.json`
- 模板：`templates/minimax_h3_image.api.json`、`templates/minimax_h3_image_gguf.api.json`
- 模板元数据：`workflow.py` 的 `TEMPLATES`
- Web 模型市场：`/api/models/market`

## 注意

- 21GB INT8 + 27GB 文本编码器对 32GB 内存偏紧，建议高配主机使用；
- 8GB 显存优先 Q4/Q2 GGUF；
- H3 Image 当前模板为文生图，不包含参考图输入；参考图/编辑工作流后续接入；
- 上游下载可能需要登录或确认许可。
