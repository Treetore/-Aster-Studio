"""MiniMax-H3 视频工作流模板层。

职责：
- 三个 API 格式模板（可直接 POST /prompt）：r2v / fl2v / t2v；
- 统一参数面：profile、width/height、megapixels+aspect、duration_s/length、
  seed、steps、sampler、scheduler、ref_images、first_frame/last_frame、LoRA、输出前缀；
- 纯函数规则：duration_to_length / align_frame_count / resolve_size；
- validate(api, object_info)：结构 + 节点 schema 校验（未知类、缺必需输入、
  悬空连线、静默节点）；
- ui_to_api(ui_workflow)：把 ComfyUI 前端 UI 工作流（含 subgraph、mode=4 透传、
  mode=2 静音、ResolutionSelector/ComfyMathExpression/Primitive* 辅助节点）
  转换成 /prompt 可用的 API 格式。

模板文件是「最小公共链路」：LoadImage/LoRA 等可选节点由 resolve() 按参数
动态插入，这样未使用的可选节点不会变成静默节点。

注意（autogrow 键名）：MiniMaxH3ReferenceToVideo 的 ref_images 在 API prompt 里
必须写成扁平点号键 "ref_images.ref_image_0"（不是嵌套字典），依据见
文档 docs/WORKFLOWS.md 与 validate() 中的 COMFY_AUTOGROW_V3 处理。
"""

from __future__ import annotations

import ast
import copy
import json
import math
import os
import random
import re

try:  # 作为包导入
    from . import config as _config
except ImportError:  # 直接以脚本/测试方式导入
    import config as _config  # type: ignore[no-redef]

FPS = 24
CANVAS_MULTIPLE = 32
MIN_FRAMES = 5
MAX_FRAMES = 3600
MAX_LENGTH_TRAINED = 362
MAX_REF_IMAGES = 9
SEED_MAX = (1 << 64) - 1

TEMPLATES_DIR = getattr(_config, "TEMPLATES_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"))
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHED_OBJECT_INFO = os.path.join(PROJECT_DIR, "tests", "fixtures", "object_info.json")

CLIP_NAME = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VAE_VIDEO = "minimax_h3_video_vae_fp16.safetensors"
VAE_AUDIO = "minimax_h3_audio_vae_fp32.safetensors"
UNET_FL2V = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
UNET_REF2V = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
TURBO_LORA_4STEP = "minimax_h3\\minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors"
TURBO_LORA_8STEP = "minimax_h3\\minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"

ASPECT_OPTIONS = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "3:2", "2:3"]
SCHEDULER_OPTIONS = [
    "simple", "sgm_uniform", "karras", "exponential", "ddim_uniform",
    "beta", "normal", "linear_quadratic",
]

# ---------------------------------------------------------------------------
# 模板元数据
# ---------------------------------------------------------------------------

TEMPLATES = {
    "minimax_h3_r2v": {
        "name": "minimax_h3_r2v",
        "title": "MiniMax H3 参考图生视频 (r2v)",
        "description": (
            "reference-to-video：用 <Picture i> 参考图（0-9 张，可选参考视频/音频）"
            "驱动带原生立体声的视频生成，使用 ref2va 模型。适合角色/风格/场景一致性。"
        ),
        "model": UNET_REF2V,
        "node_class": "MiniMaxH3ReferenceToVideo",
        "template_file": "minimax_h3_r2v.api.json",
        "default_prefix": "video/MiniMax_H3_r2v",
        "supports_refs": True,
        "supports_frames": False,
        "supports_lora": False,
        "default_lora": None,
    },
    "minimax_h3_fl2v": {
        "name": "minimax_h3_fl2v",
        "title": "MiniMax H3 首尾帧生视频 (fl2v)",
        "description": (
            "first/last-frame-to-video：可选首帧/尾帧图 + 提示词生成带音频视频，"
            "使用 fl2va 模型；可挂 lightx2v turbo LoRA 把 steps 降到 4-8。"
        ),
        "model": UNET_FL2V,
        "node_class": "MiniMaxH3ImageToVideo",
        "template_file": "minimax_h3_fl2v.api.json",
        "default_prefix": "video/MiniMax_H3_fl2v",
        "supports_refs": False,
        "supports_frames": True,
        "supports_lora": True,
        "default_lora": TURBO_LORA_4STEP,
    },
    "minimax_h3_t2v": {
        "name": "minimax_h3_t2v",
        "title": "MiniMax H3 文生视频 (t2v)",
        "description": (
            "text-to-video：纯文字驱动（同一 fl2va 模型，不接任何帧），"
            "输出 24fps 视频 + 原生立体声；可挂 turbo LoRA 加速。"
        ),
        "model": UNET_FL2V,
        "node_class": "MiniMaxH3ImageToVideo",
        "template_file": "minimax_h3_t2v.api.json",
        "default_prefix": "video/MiniMax_H3_t2v",
        "supports_refs": False,
        "supports_frames": False,
        "supports_lora": True,
        "default_lora": TURBO_LORA_4STEP,
    },
    "minimax_h3_image": {
        "name": "minimax_h3_image",
        "title": "MiniMax H3 图像生成 (still)",
        "description": (
            "text-to-image：用 H3 omni-modal 模型生成静帧（最小 5 帧 latent，VAEDecode 后 SaveImage 输出 PNG），"
            "同一 fl2va 模型与文本编码器；支持 optimize / LoRA / 模型覆盖，不产出音频与视频。"
        ),
        "model": UNET_FL2V,
        "node_class": "MiniMaxH3ImageToVideo",
        "template_file": "minimax_h3_image.api.json",
        "default_prefix": "image/MiniMax_H3",
        "supports_refs": False,
        "supports_frames": True,
        "supports_lora": True,
        "default_lora": TURBO_LORA_4STEP,
        "kind": "image",
        "default_size": (768, 768),
        "max_length": 39,
    },
    "qwen_image": {
        "name": "qwen_image",
        "title": "Qwen-Image 2.1 文生图",
        "description": (
            "Qwen-Image 2.1（7B MMDiT，原生 2K、RGBA）：qwen_image_2.1_int8_convrot UNET + "
            "qwen3vl_8b int8 文本编码器（type=qwen_image）+ qwen_image_2.1 VAE，SaveImage 输出 PNG。"
            "官方采样配置 steps=25 / cfg=1 / euler / simple（本模板用 BasicGuider，等价 cfg=1）。"
        ),
        "model": "qwen_image_2.1_int8_convrot.safetensors",
        "node_class": "TextEncodeQwenImage21",
        "template_file": "qwen_image.api.json",
        "default_prefix": "image/Qwen_Image_2.1",
        "supports_refs": False,
        "supports_frames": False,
        "supports_lora": True,
        "default_lora": None,
        "kind": "image_qwen",
        "default_size": (1024, 1024),
        "default_steps": 25,
        "default_sampler": "euler",
        "supports_optimize": False,
    },
    "qwen_image_edit": {
        "name": "qwen_image_edit",
        "title": "Qwen-Image 2.1 图像编辑",
        "description": (
            "Qwen-Image 2.1 编辑：TextEncodeQwenImage21 支持最多 10 张参考图（autogrow images.image_1..N），"
            "参考图经 VAE 编码拼入序列，输出为编辑后的图像（PNG）。"
            "官方采样 steps=25 / cfg=1 / euler / simple。"
        ),
        "model": "qwen_image_2.1_int8_convrot.safetensors",
        "node_class": "TextEncodeQwenImage21",
        "template_file": "qwen_image_edit.api.json",
        "default_prefix": "image/Qwen_Image_2.1_edit",
        "supports_refs": True,
        "supports_frames": False,
        "supports_lora": True,
        "default_lora": None,
        "kind": "image_qwen_edit",
        "default_size": (1024, 1024),
        "default_steps": 25,
        "default_sampler": "euler",
        "supports_optimize": False,
        "supports_edit_images": True,
    },
    "moss_image": {
        "name": "moss_image",
        "title": "MOSS-Image 文生图",
        "description": (
            "MOSS-Image（5B 单流 DiT，OpenMOSS，Apache-2.0）：moss_image_sft int8 + "
            "Qwen3.5 9B 文本编码器（type=moss_image）+ FLUX.2 small VAE；25 步 / cfg 2.5 / er_sde。"
            "擅长中英文字体与排版，支持约 4K token 长提示词。"
        ),
        "model": "moss_image_sft_int8_convrot.safetensors",
        "node_class": "CLIPTextEncode",
        "template_file": "moss_image.api.json",
        "default_prefix": "image/MOSS_Image",
        "supports_refs": False,
        "supports_frames": False,
        "supports_lora": True,
        "default_lora": None,
        "kind": "image_moss",
        "default_size": (1024, 1024),
        "default_steps": 25,
        "default_cfg": 2.5,
        "default_sampler": "er_sde",
        "supports_optimize": False,
    },
    "moss_image_edit": {
        "name": "moss_image_edit",
        "title": "MOSS-Image 图像编辑",
        "description": (
            "MOSS-Image 编辑：SFT int8 + edit LoRA（rank64）+ 源图作为参考，30 步 / cfg 4.0 / euler；"
            "按指令只改指定内容，保持人物、构图与背景不变。"
        ),
        "model": "moss_image_sft_int8_convrot.safetensors",
        "node_class": "TextEncodeMOSSImageEdit",
        "template_file": "moss_image_edit.api.json",
        "default_prefix": "image/MOSS_Edit",
        "supports_refs": True,
        "supports_frames": False,
        "supports_lora": False,
        "default_lora": None,
        "kind": "image_moss_edit",
        "default_size": (1024, 1024),
        "default_steps": 30,
        "default_cfg": 4.0,
        "default_sampler": "euler",
        "default_scheduler": "normal",
        "supports_optimize": False,
    },
    "minimax_h3_t2v_720p15s": {
        "name": "minimax_h3_t2v_720p15s",
        "title": "MiniMax H3 720p 15s 长片",
        "description": (
            "720p（1280x736）15 秒单次成片：MiniMaxH3ContextWindows 分段注意力（124/39）"
            " + turbo 4-step LoRA + Sage 优化 + 普通 VAE 解码；8GB 显存实测可用。"
        ),
        "model": UNET_FL2V,
        "node_class": "MiniMaxH3ImageToVideo",
        "template_file": "minimax_h3_t2v.api.json",
        "default_prefix": "video/MiniMax_H3_720p15s",
        "supports_refs": False,
        "supports_frames": True,
        "supports_lora": True,
        "default_lora": TURBO_LORA_4STEP,
        "default_steps": 4,
        "default_sampler": "res_multistep",
        "default_size": (1280, 736),
        "default_length": 362,
        "context_length": 124,
        "context_overlap": 39,
        "default_optimize": "sage",
        "auto_lora": True,
        "kind": "video_720p",
    },
}

_COMMON_PARAMETERS = {
    "prompt": {
        "type": "string", "default": "", "required": True,
        "help": "提示词；r2v 用 <Picture 1>/<Video 1>/<Audio 1> 引用参考素材（序号从 1 开始）。",
    },
    "profile": {
        "type": "string", "default": None, "options": ["draft", "standard", "high"],
        "help": "档位预设（config.generation.profiles），draft/standard/high 分别对应低/中/高显存与质量。",
    },
    "width": {
        "type": "int", "default": None, "min": 32, "max": 16384, "step": 32,
        "help": "显式宽度；与 height 同时给出时优先于 megapixels/profile。会向上对齐到 32。",
    },
    "height": {
        "type": "int", "default": None, "min": 32, "max": 16384, "step": 32,
        "help": "显式高度；与 width 同时给出时优先于 megapixels/profile。会向上对齐到 32。",
    },
    "megapixels": {
        "type": "float", "default": None, "min": 0.1, "max": 2.0,
        "help": "目标像素量（1.0 MP 按 1024x1024 计）；配合 aspect 计算宽高（32 对齐）。",
    },
    "aspect": {
        "type": "string", "default": None, "options": ASPECT_OPTIONS,
        "help": "宽高比；未给 width/height 时与 megapixels 一起决定分辨率。",
    },
    "duration_s": {
        "type": "float", "default": None, "min": 0.2, "max": 15.0,
        "help": "目标时长（秒），按 24fps 换算后 snap 到 17k+5 帧网格；与 length 二选一。",
    },
    "length": {
        "type": "int", "default": None, "min": 5, "max": 3600, "step": 17,
        "help": "帧数（24fps），会 snap 到 17k+5 网格；5 秒=124 帧，训练区间 124-362 帧。",
    },
    "seed": {
        "type": "int", "default": None, "min": 0, "max": SEED_MAX,
        "help": "随机种子；None 时随机生成并由 resolved.seed 回传。",
    },
    "steps": {
        "type": "int", "default": None, "min": 1, "max": 100,
        "help": "采样步数；默认取 profile。挂 turbo LoRA 时可用 4-8。",
    },
    "sampler": {
        "type": "string", "default": "res_multistep",
        "help": "KSamplerSelect 采样器名（如 res_multistep/euler/dpmpp_2m/uni_pc）。",
    },
    "scheduler": {
        "type": "string", "default": "simple", "options": SCHEDULER_OPTIONS,
        "help": "BasicScheduler 调度器名。",
    },
    "output_prefix": {
        "type": "string", "default": None,
        "help": "SaveVideo 的 filename_prefix，如 video/MiniMax_H3；默认按模板给出。",
    },
    "negative_prompt": {
        "type": "string", "default": None,
        "help": "H3 无 CFG/负面提示词路径，传入会被忽略并产生 warning。",
    },
    "optimize": {
        "type": "string", "default": "off", "options": ["off", "sage", "lowmem", "max"],
        "help": (
            "可选加速/降显存节点链（本机已装的 KJNodes / TE-Speed 节点）："
            "off=原样；sage=H3 SageAttention 补丁；lowmem=+LowVRAMAttention+ChunkFeedForward；"
            "max=lowmem + Sage 加速（TE-Speed 因与当前 ComfyUI 版本不兼容已停用）。"
        ),
    },
    "attn_head_chunks": {
        "type": "int", "default": 4, "min": 1, "max": 56,
        "help": "optimize=lowmem/max 时 MiniMaxLowVRAMAttention 的 head 分块数（越大越省显存、略慢）。",
    },
    "ff_chunks": {
        "type": "int", "default": 2, "min": 1, "max": 64,
        "help": "optimize=lowmem/max 时 MiniMaxChunkFeedForward 的分块数。",
    },
    "ff_seq_threshold": {
        "type": "int", "default": 4096, "min": 256, "max": 262144, "step": 256,
        "help": "optimize=lowmem/max 时 FF 分块的 token 阈值。",
    },
    "te_speed_device": {
        "type": "string", "default": "auto", "options": ["auto", "gpu", "cpu"],
        "help": "optimize=max 时 TE-Speed 的 device 策略。",
    },
    "unet_name": {
        "type": "string", "default": None,
        "help": "覆盖模板的 UNET 文件名（如 W4A8 量化版 minimax_h3_fl2va_pruned_w4a8_mixed.safetensors）。",
    },
    "clip_name": {
        "type": "string", "default": None,
        "help": "覆盖模板的文本编码器文件名（如 W4A8 版）。",
    },
    "vae_name": {
        "type": "string", "default": None,
        "help": "覆盖模板的视频 VAE 文件名。",
    },
    "audio_vae_name": {
        "type": "string", "default": None,
        "help": "覆盖模板的音频 VAE 文件名。",
    },
    "two_pass": {
        "type": "bool", "default": False,
        "help": (
            "H3 latent 放大二采：首遍低分辨率生成（含原生音频）→ H3 latent upscaler 放大 → "
            "低噪声二次采样重建细节，保留原音轨。需已安装 LBH latent upscaler 节点与 3D 模型。"
        ),
    },
    "two_pass_scale": {
        "type": "number", "default": 1.9, "min": 1.1, "max": 4.0,
        "help": "二采放大倍率；目标像素自动限制在 0.82MP（8GB 显存实测上限）并 32 对齐。",
    },
    "two_pass_sigmas": {
        "type": "string", "default": "0.9035,0.6316,0.3158,0",
        "help": "二采精修的 sigma 序列（低噪声区间，3 步较稳）；改动过大可能改变人物/音频。",
    },
    "two_pass_model": {
        "type": "string", "default": None,
        "help": "二采 latent upscaler 模型名（默认 minimax_h3_latent_upscaler_3d_conv_v1\\model_fp16.safetensors）。",
    },
}

_TEMPLATE_PARAMETERS = {
    "moss_image_edit": {
        "cfg": {"type": "float", "default": 4.0, "min": 1.0, "max": 10.0, "step": 0.1,
                "help": "KSampler CFG（官方 4.0，过低容易凭空出现饰品）。"},
        "negative_prompt": {"type": "string",
                            "default": "nsfw, worst quality, bad anatomy, watermark, jpeg artifact"},
        "megapixels": {"type": "float", "default": 1.0, "min": 0.2, "max": 2.0, "step": 0.05,
                       "help": "参考图缩放到的总像素（官方 1.0MP，16 对齐）。"},
        "image_strength": {"type": "float", "default": 1.0, "min": 0.0, "max": 1.0},
    },
    "moss_image": {
        "cfg": {"type": "float", "default": 2.5, "min": 1.0, "max": 10.0, "step": 0.1,
                "help": "KSampler CFG（MOSS 官方 2.5）。"},
        "negative_prompt": {"type": "string", "default": "",
                            "help": "负面提示词；官方模板默认留空。"},
        "lora_name": {"type": "string", "default": None, "help": "可选 LoRA（如编辑 LoRA）。"},
        "lora_strength": {"type": "float", "default": 1.0, "min": -10.0, "max": 10.0},
    },
    "minimax_h3_r2v": {
        "ref_image_size": {
            "type": "string", "default": "match", "options": ["match", "max"],
            "help": "参考图尺寸策略：match=缩放到生成像素面积；max=参考管线 2048 短边（更还原但更慢）。",
        },
        "ref_images": {
            "type": "list[string]", "default": [], "max": MAX_REF_IMAGES,
            "help": "输入目录下的参考图文件名列表（0-9 张）；第 i 张对应提示词里的 <Picture i+1>。",
        },
    },
    "minimax_h3_fl2v": {
        "first_frame": {
            "type": "string", "default": None,
            "help": "首帧图片文件名（input 目录）；留空则纯文本/尾帧。",
        },
        "last_frame": {
            "type": "string", "default": None,
            "help": "尾帧图片文件名（input 目录）；留空则只固定首帧。",
        },
        "lora_name": {
            "type": "string", "default": None,
            "help": "可选 LoRA 文件名（相对 loras 目录），如 minimax_h3\\\\minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors。",
        },
        "lora_strength": {
            "type": "float", "default": 1.0, "min": -10.0, "max": 10.0,
            "help": "LoRA 强度（strength_model）。",
        },
    },
    "minimax_h3_t2v": {
        "lora_name": {
            "type": "string", "default": None,
            "help": "可选 LoRA 文件名（相对 loras 目录）。",
        },
        "lora_strength": {
            "type": "float", "default": 1.0, "min": -10.0, "max": 10.0,
            "help": "LoRA 强度（strength_model）。",
        },
    },
    "minimax_h3_image": {
        "first_frame": {
            "type": "string", "default": None,
            "help": "可选首帧图（input 目录）：给定后生成以该图为起点/构图的静帧（图生图风格）。",
        },
        "lora_name": {
            "type": "string", "default": None,
            "help": "可选 LoRA 文件名（相对 loras 目录）。",
        },
        "lora_strength": {
            "type": "float", "default": 1.0, "min": -10.0, "max": 10.0,
            "help": "LoRA 强度（strength_model）。",
        },
    },
    "minimax_h3_t2v_720p15s": {
        "context_length": {"type": "int", "default": 124, "min": 0, "max": 362, "step": 17,
                           "help": "上下文窗口长度（17n+5，0=关闭）；124≈5s，越小越省显存。"},
        "context_overlap": {"type": "int", "default": 39, "min": 0, "max": 200, "step": 17,
                            "help": "窗口重叠帧（17n+5），越大衔接越平滑但更慢。"},
        "freenoise": {"type": "bool", "default": True, "help": "FreeNoise 噪声打乱，改善窗口衔接。"},
        "decode_tiled": {"type": "bool", "default": False, "help": "VAE 分块解码（省显存但更慢）。"},
        "tile_size": {"type": "int", "default": 512, "min": 128, "max": 2048, "step": 64},
        "tile_overlap": {"type": "int", "default": 64, "min": 0, "max": 512, "step": 16},
        "temporal_size": {"type": "int", "default": 64, "min": 8, "max": 256, "step": 8},
        "temporal_overlap": {"type": "int", "default": 8, "min": 0, "max": 64, "step": 4},
        "lora_name": {"type": "string", "default": None,
                      "help": "默认挂 4step turbo LoRA；传空字符串可关闭。"},
        "lora_strength": {"type": "float", "default": 1.0, "min": -10.0, "max": 10.0},
    },
    "qwen_image": {
        "negative_prompt": {
            "type": "string", "default": "",
            "help": "负面提示词；官方式 cfg=1（BasicGuider）下不生效，仅记录/兼容。",
        },
        "lora_name": {
            "type": "string", "default": None,
            "help": "可选 LoRA（如 p_q_image_2.1_5_steps_v0.1_comfyui_bf16.safetensors，配 turbo 模型可 5-8 步）。",
        },
        "lora_strength": {
            "type": "float", "default": 1.0, "min": -10.0, "max": 10.0,
            "help": "LoRA 强度（strength_model）。",
        },
    },
    "qwen_image_edit": {
        "images": {
            "type": "list[string]", "default": [], "max": 10,
            "help": "输入目录下的参考图文件名（1-10 张），按顺序写入 autogrow 槽位 images.image_1..N；编辑指令写在 prompt 里。",
        },
        "cache_device": {
            "type": "string", "default": "cpu", "options": ["cpu", "auto", "gpu", "off"],
            "help": "QwenImage21Cache 的 KV cache 设备：cpu=放内存（默认，省显存）；off=不缓存（最省显存但更慢）。",
        },
        "cache_dtype": {
            "type": "string", "default": "int8", "options": ["int8", "default", "int4"],
            "help": "KV cache 精度：int8 约为 bf16 一半显存，int4 更省但有精度损失。",
        },
        "negative_prompt": {
            "type": "string", "default": "",
            "help": "负面提示词；cfg=1 下不生效，仅记录/兼容。",
        },
        "lora_name": {
            "type": "string", "default": None,
            "help": "可选 LoRA 文件名（相对 loras 目录）。",
        },
        "lora_strength": {
            "type": "float", "default": 1.0, "min": -10.0, "max": 10.0,
            "help": "LoRA 强度（strength_model）。",
        },
    },
}


def _parameters_for(name: str) -> dict:
    params = copy.deepcopy(_COMMON_PARAMETERS)
    params.update(copy.deepcopy(_TEMPLATE_PARAMETERS.get(name, {})))
    return params


def list_templates() -> list[dict]:
    """返回 [{\"name\",\"title\",\"description\",\"model\",\"parameters\":{...}}]。"""
    out = []
    for name, meta in TEMPLATES.items():
        out.append({
            "name": name,
            "title": meta["title"],
            "description": meta["description"],
            "model": meta["model"],
            "node_class": meta["node_class"],
            "supports_refs": meta["supports_refs"],
            "supports_frames": meta["supports_frames"],
            "supports_lora": meta["supports_lora"],
            "parameters": _parameters_for(name),
        })
    return out


# ---------------------------------------------------------------------------
# 纯规则函数
# ---------------------------------------------------------------------------

def align_frame_count(n: int) -> int:
    """把帧数向上对齐到模型要求的 17k+5 网格（124≈5s、362≈15s）。"""
    n = int(n)
    if n < MIN_FRAMES:
        n = MIN_FRAMES
    while n % 17 != 5:
        n += 1
    return n


def duration_to_length(seconds: float) -> int:
    """秒 -> 帧数：max(5, round(s*24)) 再 snap 到 17k+5 网格。"""
    frames = max(MIN_FRAMES, int(round(float(seconds) * FPS)))
    return align_frame_count(frames)


def _parse_aspect(aspect: str) -> tuple[int, int]:
    """把 \"16:9\" / \"16:9 (Widescreen)\" / AspectRatio 枚举值解析为 (w,h)。"""
    if isinstance(aspect, (list, tuple)) and len(aspect) == 2:
        return int(aspect[0]), int(aspect[1])
    if not isinstance(aspect, str):
        raise ValueError(f"无效的 aspect: {aspect!r}")
    match = re.search(r"(\d+)\s*:\s*(\d+)", aspect)
    if not match:
        raise ValueError(f"无法解析 aspect: {aspect!r}（示例：16:9 / 9:16 / 1:1）")
    w, h = int(match.group(1)), int(match.group(2))
    if w <= 0 or h <= 0:
        raise ValueError(f"无效的 aspect: {aspect!r}")
    return w, h


def resolve_size(aspect: str, megapixels: float, multiple: int = CANVAS_MULTIPLE) -> tuple[int, int]:
    """按 aspect + megapixels 计算 32 对齐的宽高（与 ResolutionSelector 同算法）。

    公式与 comfy_extras/nodes_resolution.py::ResolutionSelector 一致：
    total = megapixels * 1024 * 1024；scale = sqrt(total / (w_ratio*h_ratio))；
    width = round(w_ratio*scale/multiple)*multiple，height 同理。
    """
    w_ratio, h_ratio = _parse_aspect(aspect)
    total = float(megapixels) * 1024 * 1024
    if total <= 0:
        raise ValueError("megapixels 必须大于 0")
    scale = math.sqrt(total / (w_ratio * h_ratio))
    m = int(multiple) if multiple else CANVAS_MULTIPLE
    if m <= 0:
        raise ValueError("multiple 必须大于 0")
    width = max(m, int(round(w_ratio * scale / m)) * m)
    height = max(m, int(round(h_ratio * scale / m)) * m)
    return width, height


def align_dim(value: int, multiple: int = CANVAS_MULTIPLE) -> int:
    """把单个边长对齐到 multiple（四舍五入，最小 multiple）。"""
    value = int(value)
    multiple = int(multiple)
    return max(multiple, int(round(value / multiple)) * multiple)


# ---------------------------------------------------------------------------
# 设置 / 模板读写
# ---------------------------------------------------------------------------

def _settings() -> dict:
    try:
        return _config.load_settings()
    except Exception:
        return {}


def template_path(name: str) -> str:
    if name not in TEMPLATES:
        raise ValueError(f"未知工作流模板: {name!r}；可选：{', '.join(TEMPLATES)}")
    return os.path.join(TEMPLATES_DIR, TEMPLATES[name]["template_file"])


def load_template(name: str) -> dict:
    """读取模板 JSON（返回深拷贝，调用方可自由修改）。"""
    path = template_path(name)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not data:
        raise ValueError(f"模板文件为空或格式错误: {path}")
    return copy.deepcopy(data)


def _find_node_id(api: dict, class_type: str) -> str:
    for node_id, node in api.items():
        if node.get("class_type") == class_type:
            return node_id
    raise ValueError(f"模板中缺少节点 {class_type}")


def _find_moss_node(api: dict, which: str, required: bool = True) -> str | None:
    """Find MOSS CLIPTextEncode by node title (Positive/Negative)."""
    for node_id, node in api.items():
        if node.get("class_type") != "CLIPTextEncode":
            continue
        title = str((node.get("_meta") or {}).get("title") or "").lower()
        if which in title:
            return node_id
    if required:
        raise ValueError(f"模板中缺少 MOSS {which} 文本节点")
    return None


def _find_moss_edit_node(api: dict, which: str, required: bool = True) -> str | None:
    for node_id, node in api.items():
        if node.get("class_type") != "TextEncodeMOSSImageEdit":
            continue
        title = str((node.get("_meta") or {}).get("title") or "").lower()
        if which in title:
            return node_id
    if required:
        raise ValueError(f"模板中缺少 MOSS 编辑 {which} 文本节点")
    return None


def _safe_find(api: dict, class_type: str) -> str | None:
    for node_id, node in api.items():
        if node.get("class_type") == class_type:
            return node_id
    return None


def _find_cond_id(api: dict) -> str:
    for node_id, node in api.items():
        if node.get("class_type") in ("MiniMaxH3ReferenceToVideo", "MiniMaxH3ImageToVideo",
                                      "TextEncodeQwenImage21"):
            return node_id
    raise ValueError("模板中缺少条件节点（MiniMaxH3* 或 TextEncodeQwenImage21）")


def _add_two_pass(api: dict, params: dict, width: int, height: int, warnings: list[str]) -> dict | None:
    """H3 latent 放大二采：首遍低分辨率 → latent 3D 放大 → 低噪声精修（保留原生音频）。

    依赖 LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler 与 KJNodes 的 AV latent 拆分/合并节点。
    """
    import math

    try:
        scale = float(params.get("two_pass_scale", 1.9) or 1.9)
    except (TypeError, ValueError):
        scale = 1.9
    scale = max(1.1, min(4.0, scale))
    tw = max(64, int(round(width * scale)))
    th = max(64, int(round(height * scale)))
    max_pixels = 0.82 * 1024 * 1024  # 8GB 显存实测上限（社区共识）
    if tw * th > max_pixels:
        k = math.sqrt(max_pixels / float(tw * th))
        tw, th = int(round(tw * k)), int(round(th * k))
    tw = max(32, (tw // 32) * 32)
    th = max(32, (th // 32) * 32)
    model = str(params.get("two_pass_model") or "minimax_h3_latent_upscaler_3d_conv_v1\\model_fp16.safetensors")
    sigmas = str(params.get("two_pass_sigmas") or "0.9035,0.6316,0.3158,0")

    try:
        sampler_id = _find_node_id(api, "SamplerCustomAdvanced")
        cond_id = _find_cond_id(api)
        save_id = _find_node_id(api, "SaveVideo")
    except ValueError as exc:
        warnings.append(f"two_pass: {exc}，已跳过二采")
        return None

    sampler_inputs = api[sampler_id].get("inputs") or {}
    cond = dict(api[cond_id].get("inputs") or {})
    cond["width"] = tw
    cond["height"] = th
    vae_ref = cond.get("vae") or ["3", 0]

    audio_vae_ref = ["4", 0]
    for node in api.values():
        if node.get("class_type") == "VAEDecodeAudio":
            vae = (node.get("inputs") or {}).get("vae")
            if isinstance(vae, list) and len(vae) == 2:
                audio_vae_ref = vae
            break

    model_ref = ["1", 0]
    for node in api.values():
        if node.get("class_type") == "BasicGuider":
            m = (node.get("inputs") or {}).get("model")
            if isinstance(m, list) and len(m) == 2:
                model_ref = m
            break

    save_inputs = api[save_id].get("inputs") or {}
    old_video_ref = save_inputs.get("video")
    fps, bit_depth = 24.0, 8
    if isinstance(old_video_ref, list) and old_video_ref and str(old_video_ref[0]) in api:
        create_inputs = api[str(old_video_ref[0])].get("inputs") or {}
        fps = create_inputs.get("fps", fps)
        bit_depth = create_inputs.get("bit_depth", bit_depth)

    base = 300
    nodes = {
        str(base): {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": [sampler_id, 1]}},
        str(base + 1): {"class_type": "MinimaxH3LatentUpscaler3D", "inputs": {
            "latent": [str(base), 0], "model_name": model, "mode": "target dimensions",
            "mode.width": tw, "mode.height": th, "align": 32,
            "enable_temporal_chunking": True, "force_unload": True,
            "device": "cuda", "precision": "fp16"}},
        str(base + 2): {"class_type": "LTXVConcatAVLatent", "inputs": {
            "video_latent": [str(base + 1), 0], "audio_latent": [str(base), 1]}},
        str(base + 3): {"class_type": api[cond_id].get("class_type"), "inputs": cond},
        str(base + 4): {"class_type": "BasicGuider", "inputs": {
            "model": model_ref, "conditioning": [str(base + 3), 0]}},
        str(base + 5): {"class_type": "ManualSigmas", "inputs": {"sigmas": sigmas}},
        str(base + 6): {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": sampler_inputs.get("noise"), "guider": [str(base + 4), 0],
            "sampler": sampler_inputs.get("sampler"), "sigmas": [str(base + 5), 0],
            "latent_image": [str(base + 2), 0]}},
        str(base + 7): {"class_type": "VAEDecode", "inputs": {
            "samples": [str(base + 6), 0], "vae": vae_ref}},
        str(base + 8): {"class_type": "VAEDecodeAudio", "inputs": {
            "samples": [str(base + 6), 0], "vae": audio_vae_ref}},
        str(base + 9): {"class_type": "CreateVideo", "inputs": {
            "images": [str(base + 7), 0], "audio": [str(base + 8), 0],
            "fps": fps, "bit_depth": bit_depth}},
    }
    api.update(nodes)
    if isinstance(old_video_ref, list):
        api[save_id]["inputs"]["video"] = [str(base + 9), 0]
    return {"scale": scale, "width": tw, "height": th, "model": model, "sigmas": sigmas,
            "node_ids": list(nodes.keys())}


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------

def resolve(name: str, params: dict | None = None) -> dict:
    """把参数解析进模板，返回 {name, api, resolved, warnings}。

    参数优先级：显式 width/height > megapixels+aspect > profile；
    显式 length > duration_s > profile.duration_s；
    显式 steps/sampler/scheduler > profile。
    """
    params = dict(params or {})
    if name not in TEMPLATES:
        raise ValueError(f"未知工作流模板: {name!r}；可选：{', '.join(TEMPLATES)}")
    meta = TEMPLATES[name]
    settings = _settings()
    generation = settings.get("generation") or {}
    profiles = generation.get("profiles") or {}
    warnings: list[str] = []

    if params.get("negative_prompt"):
        warnings.append("H3 没有 CFG/负面提示词通道，negative_prompt 已忽略。")

    profile_name = params.get("profile") or generation.get("default_profile") or "draft"
    if profile_name not in profiles:
        raise ValueError(f"未知 profile: {profile_name!r}；可选：{', '.join(profiles) or 'draft/standard/high'}")
    profile = profiles[profile_name]

    prompt = params.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt 不能为空")

    # --- 尺寸 -------------------------------------------------------------
    width_in = params.get("width")
    height_in = params.get("height")
    if (width_in is None) != (height_in is None):
        raise ValueError("width 和 height 必须同时给出")
    if width_in is not None:
        width = int(width_in)
        height = int(height_in)
        aligned_w, aligned_h = align_dim(width), align_dim(height)
        if (aligned_w, aligned_h) != (width, height):
            warnings.append(f"宽高 ({width}x{height}) 已对齐到 32 的倍数：{aligned_w}x{aligned_h}")
        width, height = aligned_w, aligned_h
    else:
        aspect = params.get("aspect") or profile.get("aspect") or "16:9"
        megapixels = params.get("megapixels")
        if megapixels is None:
            megapixels = profile.get("megapixels", 0.4)
        width, height = resolve_size(aspect, float(megapixels))
    # 图像模板默认方形画布（H3 原生短边 768），仅在完全未指定尺寸时生效
    if meta.get("default_size") and width_in is None and height_in is None \
            and params.get("megapixels") is None and params.get("aspect") is None:
        width, height = align_dim(int(meta["default_size"][0])), align_dim(int(meta["default_size"][1]))
    max_pixels = int(generation.get("max_pixels") or (1920 * 1088))
    if str(meta.get("kind") or "").startswith("image"):
        max_pixels = max(max_pixels, 2048 * 2048)
    if width * height > max_pixels:
        warnings.append(
            f"分辨率 {width}x{height}={width * height} 超过设置上限 max_pixels={max_pixels}，安全守卫可能拒绝该任务。"
        )

    # --- 帧数 -------------------------------------------------------------
    length_in = params.get("length")
    if length_in is not None:
        length = int(length_in)
    elif params.get("duration_s") is None and meta.get("default_length"):
        length = int(meta["default_length"])
    else:
        duration = params.get("duration_s")
        if duration is None:
            duration = profile.get("duration_s", 5)
        length = duration_to_length(float(duration))
    if str(meta.get("kind") or "").startswith("image") or meta.get("node_class") == "CLIPTextEncode":
        length = 1  # 图像模板：无帧数概念，占位以复用 resolved 结构
    else:
        snapped = align_frame_count(length)
        if snapped != length:
            warnings.append(f"帧数 {length} 已 snap 到 17k+5 网格：{snapped}")
        length = snapped
        max_length = int(generation.get("max_length") or MAX_LENGTH_TRAINED)
        if meta.get("max_length"):
            max_length = min(max_length, int(meta["max_length"]))
        if length > max_length:
            warnings.append(
                f"帧数 {length} 超过 max_length={max_length}（模型训练区间 124-362），已截断到 {max_length}。"
            )
            length = align_frame_count(max_length)
        if length < 124:
            warnings.append(f"帧数 {length}（{length / FPS:.2f}s）低于训练区间下限 124，画质可能不稳定。")

        # --- 720p 单次直出护栏（2026-11-21 实测：10s 可行，12s 需低显存，15s 必崩）---
        is_h3_video = ("h3" in str(meta.get("model") or "").lower()
                       and not str(meta.get("kind") or "").startswith("image"))
        hi_res = width * height >= 900_000
        if is_h3_video and hi_res:
            if length > 294 and not bool(params.get("allow_unsafe")):
                raise ValueError(
                    "720p 单次直出上限 12s：12s 以上请改用 5s 分段（segment_film / 长片页），"
                    "或显式传 allow_unsafe=true 仅做实验（实测 15s 会 aimdo 崩溃并挂死 ComfyUI）。"
                )
            if length > 252:
                if params.get("context_length") is None:
                    params["context_length"] = 124
                    params["context_overlap"] = 39
                    warnings.append("720p >10s：已自动启用 H3 上下文窗口（实测 12s 可输出）。")
                if params.get("decode_tiled") is None:
                    params["decode_tiled"] = True
                    warnings.append("720p >10s：已自动启用分块 VAE 解码。")
                if str(params.get("optimize") or meta.get("default_optimize") or "off").lower() in ("off", "sage"):
                    params["optimize"] = "max"
                    warnings.append("720p >10s：已自动切换到 optimize=max。")

    # --- 采样参数 ---------------------------------------------------------
    steps = int(params.get("steps") if params.get("steps") is not None else profile.get("steps", 20))
    if params.get("steps") is None and meta.get("default_steps"):
        steps = int(meta["default_steps"])
    sampler = str(params.get("sampler") or profile.get("sampler") or "res_multistep")
    if params.get("sampler") is None and meta.get("default_sampler"):
        sampler = str(meta["default_sampler"])
    scheduler = str(params.get("scheduler") or meta.get("default_scheduler") or profile.get("scheduler") or "simple")
    seed = params.get("seed")
    if seed is None:
        seed = random.SystemRandom().randrange(0, 1 << 63)
    else:
        seed = int(seed)
    seed &= SEED_MAX

    ref_image_size = str(params.get("ref_image_size") or "match")
    if ref_image_size not in ("match", "max"):
        raise ValueError(f"ref_image_size 只能是 match/max，收到 {ref_image_size!r}")

    output_prefix = params.get("output_prefix") or meta["default_prefix"]

    ref_images: list[str] = []
    if params.get("ref_images"):
        raw_refs = params["ref_images"]
        if isinstance(raw_refs, str):
            raw_refs = [raw_refs]
        for item in raw_refs:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("ref_images 必须是输入目录下的文件名（字符串）列表")
            ref_images.append(item)
        if len(ref_images) > MAX_REF_IMAGES:
            raise ValueError(f"ref_images 最多 {MAX_REF_IMAGES} 张，收到 {len(ref_images)} 张")

    first_frame = params.get("first_frame") or None
    last_frame = params.get("last_frame") or None
    _lora_param = str(params.get("lora_name") or "").strip()
    if _lora_param.lower() in ("none", "off", "__none__"):
        lora_name = None
    else:
        lora_name = _lora_param or (meta.get("default_lora") if meta.get("auto_lora") else None) or None
    lora_strength = float(params.get("lora_strength", 1.0) if params.get("lora_strength") is not None else 1.0)

    if ref_images and not meta["supports_refs"]:
        warnings.append(f"模板 {name} 不支持 ref_images，已忽略。")
        ref_images = []
    if (first_frame or last_frame) and not meta["supports_frames"]:
        warnings.append(f"模板 {name} 不支持 first_frame/last_frame，已忽略。")
        first_frame = last_frame = None
    if lora_name and not meta["supports_lora"]:
        warnings.append(f"模板 {name} 使用的是 ref2va 模型，未挂载 LoRA：{lora_name} 已忽略。")
        lora_name = None
    if not lora_name:
        lora_strength = 1.0

    # --- 写入模板 ---------------------------------------------------------
    api = load_template(name)
    if meta.get("kind") == "image_moss_edit":
        _refs = params.get("ref_images") or []
        _src = (_refs[0] if _refs else params.get("first_frame"))
        if not _src:
            raise ValueError("MOSS 图像编辑需要参考图：请传 ref_images 或 first_frame")
        for _nid, _node in api.items():
            if _node.get("class_type") == "LoadImage":
                _node.setdefault("inputs", {})["image"] = str(_src)
            if _node.get("class_type") == "ImageScaleToTotalPixels":
                _node.setdefault("inputs", {})["megapixels"] = float(params.get("megapixels") or 1.0)
    # 可选模型覆盖（例如 W4A8 / GGUF 量化版）
    unet_name = params.get("unet_name")
    clip_name = params.get("clip_name")
    vae_name = params.get("vae_name")
    audio_vae_name = params.get("audio_vae_name")
    if unet_name:
        api[_find_node_id(api, "UNETLoader")]["inputs"]["unet_name"] = str(unet_name)
    if clip_name:
        api[_find_node_id(api, "CLIPLoader")]["inputs"]["clip_name"] = str(clip_name)
    if vae_name or audio_vae_name:
        for _node in api.values():
            if _node.get("class_type") != "VAELoader":
                continue
            _title = str((_node.get("_meta") or {}).get("title") or "").lower()
            if "audio" in _title:
                if audio_vae_name:
                    _node["inputs"]["vae_name"] = str(audio_vae_name)
            elif vae_name:
                _node["inputs"]["vae_name"] = str(vae_name)
    if meta.get("kind") == "image_moss":
        cond_id = _find_moss_node(api, "positive")
    elif meta.get("kind") == "image_moss_edit":
        cond_id = _find_moss_edit_node(api, "positive")
    else:
        cond_id = _find_cond_id(api)
    cond_inputs = api[cond_id]["inputs"]
    cond_inputs["prompt"] = prompt
    if meta.get("kind") == "image_moss_edit":
        cond_inputs["prompt"] = prompt
        _neg = _find_moss_edit_node(api, "negative", required=False)
        if _neg:
            _neg_prompt = params.get("negative_prompt")
            if _neg_prompt is not None:
                api[_neg]["inputs"]["prompt"] = str(_neg_prompt)
    elif meta.get("kind") == "image_moss":
        cond_inputs["text"] = prompt
        neg_id = _find_moss_node(api, "negative", required=False)
        if neg_id:
            api[neg_id]["inputs"]["text"] = str(params.get("negative_prompt") or "")
        for _nid, _node in api.items():
            if _node.get("class_type") == "EmptyFlux2LatentImage":
                _node.setdefault("inputs", {})["width"] = width
                _node["inputs"]["height"] = height
    elif meta.get("node_class") == "TextEncodeQwenImage21":
        # Qwen-Image 2.1：条件节点只有 prompt/negative_prompt/resolution，宽高交给 EmptyLatentImage
        neg = params.get("negative_prompt")
        if neg is not None:
            cond_inputs["negative_prompt"] = str(neg)
        _res = int(max(width, height))
        if str(meta.get("kind") or "") == "image_qwen_edit" and width * height > 1_600_000:
            _res = min(_res, 1536)
            warnings.append(
                f"图像编辑分辨率 {width}x{height} 过大（>{1_600_000} px），已将参考图 resolution 限制到 {_res}，"
                "避免 KV cache 撑爆 8GB 显存。"
            )
        cond_inputs["resolution"] = _res
        for _nid, _node in api.items():
            if _node.get("class_type") == "QwenImage21Cache":
                _node.setdefault("inputs", {})["device"] = str(params.get("cache_device") or "cpu")
                _node["inputs"]["dtype"] = str(params.get("cache_dtype") or "int8")
            if _node.get("class_type") == "EmptyLatentImage":
                _node.setdefault("inputs", {})["width"] = width
                _node["inputs"]["height"] = height
    else:
        cond_inputs["width"] = width
        cond_inputs["height"] = height
        cond_inputs["length"] = length
    if meta["node_class"] == "MiniMaxH3ReferenceToVideo":
        cond_inputs["ref_image_size"] = ref_image_size

    _noise = _safe_find(api, "RandomNoise")
    if _noise:
        api[_noise]["inputs"]["noise_seed"] = seed
    _sched = _safe_find(api, "BasicScheduler")
    if _sched:
        scheduler_inputs = api[_sched]["inputs"]
        scheduler_inputs["steps"] = steps
        scheduler_inputs["scheduler"] = scheduler
    _ksel = _safe_find(api, "KSamplerSelect")
    if _ksel:
        api[_ksel]["inputs"]["sampler_name"] = sampler
    _ks = _safe_find(api, "KSampler")
    if _ks:
        _ki = api[_ks]["inputs"]
        _ki["seed"] = seed
        _ki["steps"] = steps
        _ki["cfg"] = float(params.get("cfg") or meta.get("default_cfg") or 2.5)
        _ki["sampler_name"] = sampler
        _ki["scheduler"] = scheduler
        _ki["denoise"] = 1.0
    save_id = None
    for _save_cls in ("SaveVideo", "SaveImage", "SaveAudio", "SaveWEBM", "SaveAnimatedWEBP"):
        try:
            save_id = _find_node_id(api, _save_cls)
            break
        except ValueError:
            continue
    if save_id:
        api[save_id]["inputs"]["filename_prefix"] = output_prefix
    else:
        warnings.append("模板中没有输出节点（SaveVideo/SaveImage 等），不会产生文件。")

    next_id = 100
    _h3_node = str(meta.get("node_class") or "").startswith("MiniMaxH3")
    if ref_images and _h3_node:
        for index, filename in enumerate(ref_images):
            node_id = str(next_id)
            next_id += 1
            api[node_id] = {
                "class_type": "LoadImage",
                "inputs": {"image": filename},
                "_meta": {"title": f"LoadImage (ref {index})"},
            }
            cond_inputs[f"ref_images.ref_image_{index}"] = [node_id, 0]
    if first_frame and _h3_node:
        node_id = str(next_id)
        next_id += 1
        api[node_id] = {
            "class_type": "LoadImage",
            "inputs": {"image": first_frame},
            "_meta": {"title": "LoadImage (first_frame)"},
        }
        cond_inputs["first_frame"] = [node_id, 0]
    if last_frame and _h3_node:
        node_id = str(next_id)
        next_id += 1
        api[node_id] = {
            "class_type": "LoadImage",
            "inputs": {"image": last_frame},
            "_meta": {"title": "LoadImage (last_frame)"},
        }
        cond_inputs["last_frame"] = [node_id, 0]

    if meta.get("supports_edit_images"):
        edit_images = params.get("images") or []
        if isinstance(edit_images, str):
            edit_images = [edit_images]
        edit_images = [str(x) for x in edit_images if x]
        if len(edit_images) > 10:
            warnings.append(f"参考图 {len(edit_images)} 张超过 10 张上限，已截断。")
            edit_images = edit_images[:10]
        if not edit_images:
            warnings.append("qwen_image_edit 未提供参考图（images），将退化为纯文生图。")
        for index, filename in enumerate(edit_images):
            node_id = str(next_id)
            next_id += 1
            api[node_id] = {
                "class_type": "LoadImage",
                "inputs": {"image": filename},
                "_meta": {"title": f"LoadImage (edit {index + 1})"},
            }
            cond_inputs[f"images.image_{index + 1}"] = [node_id, 0]

    if lora_name:
        unet_id = _find_node_id(api, "UNETLoader")
        lora_id = "90"
        api[lora_id] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": [unet_id, 0],
                "lora_name": lora_name,
                "strength_model": lora_strength,
            },
            "_meta": {"title": "LoraLoaderModelOnly"},
        }
        for node_id, node in api.items():
            if node_id == lora_id:
                continue
            inputs = node.get("inputs") or {}
            for key, value in inputs.items():
                if isinstance(value, list) and len(value) == 2 and value[0] == unet_id and value[1] == 0:
                    inputs[key] = [lora_id, 0]

    # --- 可选优化节点链（Sage / 低显存 Attention / 分块 FF / TE-Speed） -------
    optimize = str(params.get("optimize") or meta.get("default_optimize") or "off").lower()
    if meta.get("supports_optimize") is False and optimize != "off":
        warnings.append("该模板不支持 H3 optimize 补丁链（KJNodes/TE-Speed），已忽略 optimize。")
        optimize = "off"
    if optimize not in ("off", "sage", "lowmem", "max"):
        warnings.append(f"optimize={optimize} 非法，已按 off 处理。")
        optimize = "off"
    unet_id = _find_node_id(api, "UNETLoader")
    model_src = lora_id if lora_name else unet_id
    opt_specs: list[tuple[str, dict]] = []
    if optimize in ("sage", "lowmem", "max"):
        opt_specs.append(("MiniMaxH3MemoryEfficientSageAttentionPatch", {}))
    if optimize in ("lowmem", "max"):
        opt_specs.append(("MiniMaxLowVRAMAttention", {"head_chunks": int(params.get("attn_head_chunks", 4))}))
        opt_specs.append((
            "MiniMaxChunkFeedForward",
            {
                "chunks": int(params.get("ff_chunks", 2)),
                "seq_threshold": int(params.get("ff_seq_threshold", 4096)),
            },
        ))
    # 注意：TE-Speed-MiniMaxH3 节点与当前 ComfyUI 0.33.1 的 MiniMaxH3Model._forward 不兼容，
    # 运行时会抛 "TE-Speed-MiniMaxH3: unsupported MiniMaxH3Model._forward version"，
    # 因此 max 档只保留 Sage + LowVRAMAttention + ChunkFeedForward，不再插入 TESpeed。
    # （2026-11-10 失败日志：job 1d857506 即由该节点导致）
    opt_ids: list[str] = []
    base = model_src
    for i, (cls, extra) in enumerate(opt_specs):
        nid = str(200 + i)
        api[nid] = {
            "class_type": cls,
            "inputs": {"model": [base, 0], **extra},
            "_meta": {"title": cls},
        }
        opt_ids.append(nid)
        base = nid
    if opt_ids:
        for node_id, node in api.items():
            if node_id in opt_ids:
                continue
            inputs = node.get("inputs") or {}
            for key, value in inputs.items():
                if isinstance(value, list) and len(value) == 2 and value[0] == model_src and value[1] == 0:
                    inputs[key] = [base, 0]

    # --- 上下文窗口（长视频分段注意力） -------------------------------------
    ctx_len = int(params.get("context_length") or meta.get("context_length") or 0)
    if ctx_len and not str(meta.get("kind") or "").startswith("image") and "h3" in str(meta.get("model") or "").lower():
        ctx_id = "250"
        ctx_overlap = int(params.get("context_overlap") or meta.get("context_overlap") or 39)
        api[ctx_id] = {
            "class_type": "MiniMaxH3ContextWindows",
            "inputs": {
                "model": [base, 0],
                "context_length": ctx_len,
                "context_overlap": ctx_overlap,
                "fuse_method": str(params.get("fuse_method") or "pyramid"),
                "freenoise": bool(params.get("freenoise", True)),
                "split_conds_to_windows": False,
            },
            "_meta": {"title": "MiniMaxH3ContextWindows"},
        }
        for _nid, _node in api.items():
            if _nid == ctx_id:
                continue
            for _k, _v in (_node.get("inputs") or {}).items():
                if isinstance(_v, list) and _v == [base, 0]:
                    _node["inputs"][_k] = [ctx_id, 0]
        warnings.append(f"已启用 H3 上下文窗口 {ctx_len}（重叠 {ctx_overlap}）。")

    if bool(params.get("decode_tiled")) and "h3" in str(meta.get("model") or "").lower():
        for _nid, _node in api.items():
            if _node.get("class_type") == "VAEDecode":
                _node["class_type"] = "VAEDecodeTiled"
                _node["inputs"].update({
                    "tile_size": int(params.get("tile_size") or 512),
                    "overlap": int(params.get("tile_overlap") or 64),
                    "temporal_size": int(params.get("temporal_size") or 64),
                    "temporal_overlap": int(params.get("temporal_overlap") or 8),
                })
                break

    # --- 生成策略护栏（数据来自 2026-11-11 压力测试，见 docs/GENERATION_POLICY_20261111.md）---
    policy = generation.get("policy") or {}
    two_pass_max_length = int(policy.get("two_pass_max_length") or 124)
    if params.get("two_pass") and length > two_pass_max_length:
        raise ValueError(
            f"two_pass 仅支持 length<={two_pass_max_length}（约 {two_pass_max_length / FPS:.1f}s），"
            f"当前 length={length}（{length / FPS:.2f}s）。实测 15s 段二采第二阶段单步约 20 分钟，"
            "不可行；长段请改用 steps=8~10、optimize=max 单采，或缩短到 5s 再开二采。"
        )
    max_steps = int(policy.get("max_recommended_steps") or 12)
    long_len = int(policy.get("long_length_threshold") or 260)
    if length >= long_len and steps > max_steps:
        warnings.append(
            f"长段 {length / FPS:.1f}s 配 {steps} 步：实测 15s 段建议 <= {max_steps} 步，"
            "更多步数会成倍拉长耗时（26s/步/15s 段）。"
        )

    two_pass = bool(params.get("two_pass") or False)
    if two_pass and str(meta.get("kind") or "").startswith("image"):
        raise ValueError("minimax_h3_image 不支持 two_pass：图像单帧流程没有音频/视频后处理链。")
    two_pass_info = None
    if two_pass:
        two_pass_info = _add_two_pass(api, params, width, height, warnings)

    resolved = {
        "prompt": prompt,
        "width": width,
        "height": height,
        "length": length,
        "duration_s": round(length / FPS, 3),
        "fps": FPS,
        "seed": seed,
        "steps": steps,
        "sampler": sampler,
        "scheduler": scheduler,
        "ref_image_size": ref_image_size,
        "output_prefix": output_prefix,
        "profile": profile_name,
        "pixels": width * height,
        "ref_images": ref_images,
        "first_frame": first_frame,
        "last_frame": last_frame,
        "lora_name": lora_name,
        "lora_strength": lora_strength,
        "optimize": optimize,
        "opt_nodes": opt_ids,
        "two_pass": two_pass,
        "two_pass_info": two_pass_info,
        "kind": str(meta.get("kind") or "video"),
    }
    return {"name": name, "api": api, "resolved": resolved, "warnings": warnings}


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

SOCKET_TYPES = {
    "IMAGE", "MASK", "LATENT", "MODEL", "CLIP", "VAE", "CONDITIONING", "AUDIO",
    "VIDEO", "NOISE", "GUIDER", "SAMPLER", "SIGMAS", "CONTROL_NET", "UPSCALE_MODEL",
    "CLIP_VISION", "STYLE_MODEL", "CLIP_VISION_OUTPUT", "VOXEL", "MESH", "SPLAT",
    "POINTCLOUD", "CURVE", "COMFY_MASK", "TIMESTEP", "WEBCAM", "IMAGEUPLOAD",
    "AUDIOUPLOAD", "VIDEOUPLOAD", "MODEL_PATCH", "WAV",
}

FALLBACK_OUTPUT_NODES = {
    "SaveVideo", "SaveImage", "SaveAudio", "SaveWEBM", "SaveAnimatedWEBP",
    "SaveAnimatedPNG", "PreviewImage", "PreviewVideo", "PreviewAudio",
    "VHS_VideoCombine", "SaveAudioMP3", "SaveGLB", "SaveSplat",
}

_REFERENCES_UNKNOWN = "（ComfyUI 会静默忽略该键，不会报错）"


def load_cached_object_info() -> dict | None:
    """读取 tests/fixtures/object_info.json（只读缓存快照）。"""
    try:
        with open(CACHED_OBJECT_INFO, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data:
            return data
    except (OSError, ValueError):
        pass
    return None


def _is_link(value) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], (str, int))
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
    )


def _schema_parts(info: dict) -> tuple[dict, dict]:
    raw = (info or {}).get("input") or {}
    return raw.get("required") or {}, raw.get("optional") or {}


def _dynamic_input_specs(required: dict, optional: dict) -> tuple[dict, dict]:
    """收集 autogrow / dynamic combo 输入定义。"""
    autogrows: dict[str, dict] = {}
    combos: dict[str, dict] = {}
    for category, mapping in (("required", required), ("optional", optional)):
        for name, spec in mapping.items():
            if not isinstance(spec, (list, tuple)) or not spec:
                continue
            io_type = spec[0]
            extra = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if io_type == "COMFY_AUTOGROW_V3":
                template = extra.get("template") or {}
                template_input = template.get("input") or {}
                slot_type = None
                for per_category in template_input.values():
                    for slot_spec in (per_category or {}).values():
                        if isinstance(slot_spec, (list, tuple)) and slot_spec:
                            slot_type = slot_spec[0]
                            break
                    if slot_type:
                        break
                autogrows[name] = {
                    "prefix": template.get("prefix"),
                    "names": list(template.get("names") or []),
                    "min": template.get("min", 0),
                    "max": template.get("max", 0),
                    "slot_type": slot_type,
                    "category": category,
                }
            elif io_type == "COMFY_DYNAMICCOMBO_V3":
                options = extra.get("options") or []
                combos[name] = {
                    "keys": [opt.get("key") for opt in options if isinstance(opt, dict)],
                    "category": category,
                }
    return autogrows, combos


def _input_type_options(io_type, extra: dict) -> list | None:
    if isinstance(io_type, list):
        return io_type
    if io_type == "COMBO":
        options = extra.get("options")
        if isinstance(options, list):
            return options
    return None


def validate(api: dict, object_info: dict | None = None) -> dict:
    """校验 API prompt：未知 class_type、必需输入缺失、悬空连线/槽位、静默节点。

    返回 {"ok": bool, "errors": [...], "warnings": [...]}。
    object_info 为 None 时尝试读取 tests/fixtures/object_info.json；读不到就只做
    图结构校验并给出 warning。
    """
    errors: list[str] = []
    warnings: list[str] = []
    if object_info is None:
        object_info = load_cached_object_info()
    if not isinstance(api, dict) or not api:
        return {"ok": False, "errors": ["API prompt 为空或不是 dict"], "warnings": warnings}
    if not object_info:
        warnings.append("没有 object_info 缓存，已跳过节点/输入 schema 校验（只做图结构检查）。")
        object_info = {}

    class_types: dict[str, str] = {}
    links: list[tuple[str, str, int, str]] = []  # (node_id, input_name, src_slot, src_node)
    referenced: set[str] = set()

    for node_id, node in api.items():
        if not isinstance(node_id, str):
            errors.append(f"节点 id 必须是字符串：{node_id!r}")
        if not isinstance(node, dict):
            errors.append(f"节点 {node_id}: 必须是 dict")
            continue
        class_type = node.get("class_type")
        if not isinstance(class_type, str) or not class_type:
            errors.append(f"节点 {node_id}: 缺少 class_type")
            continue
        if class_type in SOCKET_TYPES:
            errors.append(f"节点 {node_id}: {class_type} 看起来是数据类型而不是节点类")
        class_types[node_id] = class_type
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            errors.append(f"节点 {node_id} ({class_type}): inputs 必须是 dict")
            continue
        for input_name, value in inputs.items():
            if _is_link(value):
                links.append((node_id, input_name, int(value[1]), str(value[0])))
                referenced.add(str(value[0]))
            elif isinstance(value, list) and len(value) == 2:
                errors.append(
                    f"节点 {node_id} ({class_type}): 输入 {input_name} 的连线格式错误，应为 [\"节点id\", 槽位整数]"
                )

    # 连线目标必须存在
    for node_id, input_name, slot, src_node in links:
        if src_node not in api:
            errors.append(f"节点 {node_id} ({class_types.get(node_id)}): 输入 {input_name} 指向不存在的节点 {src_node}")
            class_types.pop(src_node, None)

    # 输出节点
    output_nodes: set[str] = set()
    for node_id, class_type in class_types.items():
        info = object_info.get(class_type)
        is_output = False
        if isinstance(info, dict):
            is_output = bool(info.get("output_node"))
        elif class_type in FALLBACK_OUTPUT_NODES:
            is_output = True
        if is_output:
            output_nodes.add(node_id)
    if not output_nodes:
        errors.append("没有任何输出节点（如 SaveVideo），ComfyUI 不会执行该 prompt")

    # 可达性 / 静默节点
    consumers: dict[str, set[str]] = {}
    for node_id, _input_name, _slot, src_node in links:
        consumers.setdefault(src_node, set()).add(node_id)
    reachable: set[str] = set()
    stack = list(output_nodes)
    while stack:
        current = stack.pop()
        if current in reachable or current not in api:
            continue
        reachable.add(current)
        node = api[current]
        for value in (node.get("inputs") or {}).values():
            if _is_link(value):
                stack.append(str(value[0]))
    for node_id, class_type in class_types.items():
        if node_id not in reachable:
            warnings.append(
                f"静默节点：{node_id} ({class_type}) 不在任何输出节点的依赖链上，不会被执行"
            )
        elif class_type not in FALLBACK_OUTPUT_NODES and node_id not in consumers and node_id not in output_nodes:
            warnings.append(f"节点 {node_id} ({class_type}) 的输出没有被任何节点使用")

    if not object_info:
        return {"ok": not errors, "errors": errors, "warnings": warnings}

    # 逐节点 schema 校验
    for node_id, class_type in class_types.items():
        node = api.get(node_id) or {}
        inputs = node.get("inputs") or {}
        info = object_info.get(class_type)
        if not isinstance(info, dict):
            errors.append(f"未知节点类型 (unknown class_type): {node_id} -> {class_type}")
            continue
        required, optional = _schema_parts(info)
        autogrows, dynamic_combos = _dynamic_input_specs(required, optional)
        output_types = info.get("output") or []
        known_plain = set(required) | set(optional)

        # 必需输入缺失
        for name in required:
            if name in autogrows or name in dynamic_combos:
                entry = autogrows.get(name) or dynamic_combos.get(name) or {}
                min_count = entry.get("min", 0)
                if entry.get("category") == "required" and min_count:
                    warnings.append(
                        f"节点 {node_id} ({class_type}): 动态输入 {name} 至少需要 {min_count} 项，请确认已提供"
                    )
                continue
            if name not in inputs:
                errors.append(
                    f"节点 {node_id} ({class_type}): 缺少必需输入 {name}"
                )

        for input_name, value in inputs.items():
            expected_type = None
            extra: dict = {}
            required_input = False
            if "." in input_name:
                container, suffix = input_name.split(".", 1)
                if container in autogrows:
                    meta = autogrows[container]
                    named = meta.get("names") or []
                    if named:
                        valid_names = list(named)
                    else:
                        prefix = meta.get("prefix") or ""
                        max_count = int(meta.get("max") or 0)
                        valid_names = [f"{prefix}{i}" for i in range(max_count)]
                    if suffix not in valid_names:
                        shown = ", ".join(valid_names[:4]) + (" ..." if len(valid_names) > 4 else "")
                        errors.append(
                            f"节点 {node_id} ({class_type}): autogrow 槽位 {input_name!r} 无效，"
                            f"合法键为 {container}.<{shown}>"
                        )
                        continue
                    expected_type = meta.get("slot_type")
                elif container in dynamic_combos:
                    warnings.append(
                        f"节点 {node_id} ({class_type}): 未声明的动态组合子输入 {input_name}，将按 ComfyUI 行为忽略"
                    )
                    continue
                else:
                    warnings.append(
                        f"节点 {node_id} ({class_type}): 未知输入 {input_name}{_REFERENCES_UNKNOWN}"
                    )
                    continue
            elif input_name in autogrows:
                errors.append(
                    f"节点 {node_id} ({class_type}): autogrow 输入 {input_name} 必须使用点号键，"
                    f"例如 \"{input_name}.{autogrows[input_name].get('prefix')}0\"；"
                    f"写成嵌套 dict/容器名会被 ComfyUI 静默忽略"
                )
                continue
            elif input_name in dynamic_combos:
                expected_type = "COMFY_DYNAMICCOMBO_V3"
                extra = dynamic_combos[input_name]
            elif input_name in known_plain:
                spec = required.get(input_name) or optional.get(input_name) or []
                if not spec:
                    continue
                expected_type = spec[0]
                extra = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
                required_input = input_name in required
            elif input_name in ("hidden",):
                continue
            else:
                warnings.append(
                    f"节点 {node_id} ({class_type}): 未知输入 {input_name}{_REFERENCES_UNKNOWN}"
                )
                continue

            if _is_link(value):
                src_node, slot = str(value[0]), int(value[1])
                if src_node not in api:
                    continue  # 上面已报
                src_class = class_types.get(src_node)
                src_info = object_info.get(src_class) if src_class else None
                if isinstance(src_info, dict):
                    src_outputs = src_info.get("output") or []
                    if slot < 0 or slot >= len(src_outputs):
                        errors.append(
                            f"节点 {node_id} ({class_type}): 输入 {input_name} 指向 {src_node} ({src_class}) "
                            f"的槽位 {slot}，但该节点只有 {len(src_outputs)} 个输出"
                        )
                        continue
                    src_type = src_outputs[slot]
                    if isinstance(expected_type, str) and expected_type in SOCKET_TYPES:
                        if src_type not in (expected_type, "*", "ANY"):
                            errors.append(
                                f"节点 {node_id} ({class_type}): 输入 {input_name} 需要 {expected_type}，"
                                f"但 {src_node} ({src_class}) 槽位 {slot} 输出 {src_type}"
                            )
                continue

            # 字面量
            if value is None:
                if required_input:
                    errors.append(f"节点 {node_id} ({class_type}): 必需输入 {input_name} 为 null")
                continue
            if expected_type == "COMFY_DYNAMICCOMBO_V3":
                keys = extra.get("keys") or []
                if keys and value not in keys:
                    errors.append(
                        f"节点 {node_id} ({class_type}): {input_name} 的值 {value!r} 不在动态组合选项 {keys} 中"
                    )
                continue
            if expected_type is None or expected_type == "COMFY_AUTOGROW_V3":
                continue
            if isinstance(expected_type, str) and expected_type in SOCKET_TYPES:
                errors.append(
                    f"节点 {node_id} ({class_type}): {input_name} 需要连线（{expected_type}），不能是字面量 {value!r}"
                )
                continue
            options = _input_type_options(expected_type, extra)
            if options is not None:
                # generate 工具支持传本地绝对路径，提交前会自动上传成 input 目录文件名；
                # 这种值此刻不在 object_info 的选项表里，不能算错误。
                if class_type in ("LoadImage", "LoadVideo", "LoadAudio") and isinstance(value, str):
                    candidate = value
                    if os.path.isabs(candidate):
                        if os.path.isfile(candidate):
                            continue
                        warnings.append(
                            f"节点 {node_id} ({class_type}): 本地素材 {candidate} 不存在，提交前上传会失败"
                        )
                        continue
                    # 页面先上传到 input 目录、再传文件名；object_info 可能是旧缓存，
                    # 只要文件确实在 input 目录里就放行（提交时 ComfyUI 会正常读取）。
                    _input_dir = getattr(_config, "INPUT_DIR", "") or ""
                    if _input_dir and os.path.isfile(os.path.join(_input_dir, candidate)):
                        continue
                if value not in options:
                    shown = options if len(options) <= 12 else f"{options[:12]}..."
                    errors.append(
                        f"节点 {node_id} ({class_type}): {input_name} 的值 {value!r} 不在可选列表 {shown} 中"
                    )
                continue
            if expected_type == "INT":
                if isinstance(value, bool) or not isinstance(value, int):
                    if isinstance(value, float) and value.is_integer():
                        continue
                    errors.append(f"节点 {node_id} ({class_type}): {input_name} 需要 INT，收到 {value!r}")
                    continue
            elif expected_type == "FLOAT":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    errors.append(f"节点 {node_id} ({class_type}): {input_name} 需要 FLOAT，收到 {value!r}")
                    continue
            elif expected_type == "STRING":
                if not isinstance(value, str):
                    errors.append(f"节点 {node_id} ({class_type}): {input_name} 需要 STRING，收到 {value!r}")
                    continue
            elif expected_type == "BOOLEAN":
                if not isinstance(value, bool):
                    errors.append(f"节点 {node_id} ({class_type}): {input_name} 需要 BOOLEAN，收到 {value!r}")
                    continue

            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if "min" in extra and value < extra["min"]:
                    errors.append(
                        f"节点 {node_id} ({class_type}): {input_name}={value} 小于最小值 {extra['min']}"
                    )
                if "max" in extra and value > extra["max"]:
                    errors.append(
                        f"节点 {node_id} ({class_type}): {input_name}={value} 大于最大值 {extra['max']}"
                    )

    return {"ok": not errors, "errors": errors, "warnings": warnings}


# ---------------------------------------------------------------------------
# UI 工作流 -> API
# ---------------------------------------------------------------------------

_DROP_NODE_TYPES = {"MarkdownNote", "Note"}
_SIMPLE_PRIMITIVE_TYPES = {
    "PrimitiveFloat", "PrimitiveInt", "PrimitiveString", "PrimitiveStringMultiline",
    "PrimitiveBoolean",
}
_UPLOAD_TYPES = {"IMAGEUPLOAD", "AUDIOUPLOAD", "VIDEOUPLOAD", "UPLOAD"}


class _NotFoldable(Exception):
    """辅助节点含运行期依赖，不能静态折叠。"""


def _widget_entries(node: dict) -> list[tuple[str, object, dict]]:
    """按 UI inputs 顺序把 widgets_values 映射成 (widget 名, 值, input 定义)。"""
    values = node.get("widgets_values") or []
    out: list[tuple[str, object, dict]] = []
    index = 0
    for inp in node.get("inputs") or []:
        widget = inp.get("widget")
        if not widget:
            continue
        if isinstance(widget, dict):
            name = widget.get("name") or inp.get("name")
        else:
            name = inp.get("name")
        value = values[index] if index < len(values) else None
        out.append((str(name), value, inp))
        index += 1
    return out


def _widget_lookup(node_entry: dict, name: str):
    for widget_name, value, _inp in node_entry.get("widgets") or []:
        if widget_name == name:
            return value
    return None


def _api_input_name(inp: dict) -> str | None:
    if inp.get("type") in _UPLOAD_TYPES:
        return None
    widget = inp.get("widget")
    if isinstance(widget, dict) and widget.get("name"):
        return str(widget["name"])
    if inp.get("name"):
        return str(inp["name"])
    return None


_MATH_FUNCTIONS = {
    "sum": lambda *args: sum(args[0]) if len(args) == 1 and isinstance(args[0], (list, tuple)) else sum(args),
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
    "pow": pow,
    "sqrt": math.sqrt,
    "ceil": math.ceil,
    "floor": math.floor,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "int": int,
    "float": float,
}

_ALLOWED_AST = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Load,
    ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.Pow, ast.UAdd, ast.USub, ast.Compare, ast.IfExp, ast.BoolOp, ast.And,
    ast.Or, ast.Not, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Tuple,
)


def _eval_math_expression(expression: str, values: dict) -> float | int:
    """安全求值 ComfyMathExpression 的表达式子集。"""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"无法解析数学表达式: {expression!r}: {exc}") from None
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_AST):
            raise ValueError(f"表达式 {expression!r} 含不允许的语法: {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _MATH_FUNCTIONS:
                raise ValueError(f"表达式 {expression!r} 调用了不允许的函数")
            if node.keywords:
                raise ValueError(f"表达式 {expression!r} 不允许关键字参数")
        elif isinstance(node, ast.Name) and node.id not in _MATH_FUNCTIONS and node.id not in values:
            raise ValueError(f"表达式 {expression!r} 使用了未知变量 {node.id!r}")
    namespace = dict(values)
    namespace.update(_MATH_FUNCTIONS)
    return eval(compile(tree, "<comfy-math-expression>", "eval"), {"__builtins__": {}}, namespace)


class _UIFlattener:
    """把 UI 工作流展开成扁平节点/边（展开 subgraph，保留 mode）。"""

    def __init__(self, ui: dict):
        self.subgraphs = {
            sg.get("id"): sg
            for sg in ((ui.get("definitions") or {}).get("subgraphs") or [])
            if isinstance(sg, dict)
        }
        self.nodes: dict[str, dict] = {}
        self.aliases: dict[tuple[str, int], tuple] = {}
        self.instance_ctx: dict[str, tuple] = {}
        self.edges: list[dict] = []
        self.expanded: set[str] = set()
        self.expanding: set[str] = set()

    def run(self, ui: dict) -> tuple[dict, list[dict]]:
        self._level(
            {"nodes": ui.get("nodes") or [], "links": ui.get("links") or []},
            prefix="",
            in_specs=[],
            out_sinks=[],
        )
        return self.nodes, self.edges

    # -- 内部 --------------------------------------------------------------
    @staticmethod
    def _normalize_links(raw_links) -> dict:
        links = {}
        for link in raw_links or []:
            if isinstance(link, (list, tuple)):
                if len(link) < 5:
                    continue
                link_id, origin_id, origin_slot, target_id, target_slot = link[:5]
            elif isinstance(link, dict):
                link_id = link.get("id")
                origin_id = link.get("origin_id")
                origin_slot = link.get("origin_slot")
                target_id = link.get("target_id")
                target_slot = link.get("target_slot")
            else:
                continue
            links[link_id] = (origin_id, origin_slot, target_id, target_slot)
        return links

    def _level(self, graph: dict, prefix: str, in_specs: list, out_sinks: list,
               alias_out: dict | None = None) -> None:
        nodes = graph.get("nodes") or []
        links = self._normalize_links(graph.get("links"))

        for node in nodes:
            eid = prefix + str(node.get("id"))
            self.nodes[eid] = {
                "type": node.get("type"),
                "mode": int(node.get("mode") or 0),
                "inputs": node.get("inputs") or [],
                "outputs": node.get("outputs") or [],
                "widgets": _widget_entries(node),
                "raw": node,
                "out_types": [out.get("type") for out in (node.get("outputs") or [])],
                "subgraph": node.get("type") in self.subgraphs,
            }
            if node.get("type") in self.subgraphs:
                self.instance_ctx[eid] = (prefix, in_specs, out_sinks, links)

        for node in nodes:
            if node.get("type") in self.subgraphs:
                self._expand(prefix + str(node.get("id")))

        for node in nodes:
            if node.get("type") in self.subgraphs:
                continue
            eid = prefix + str(node.get("id"))
            for index, inp in enumerate(node.get("inputs") or []):
                link_id = inp.get("link")
                if link_id is None or link_id not in links:
                    continue
                origin_id, origin_slot, _target_id, _target_slot = links[link_id]
                src = self._origin(origin_id, origin_slot, prefix, in_specs)
                if src is not None:
                    self.edges.append({"src": src, "dst": (eid, index)})

        # 子图输出：target_id == -20
        for link_id, (origin_id, origin_slot, target_id, target_slot) in links.items():
            if target_id != -20:
                continue
            src = self._origin(origin_id, origin_slot, prefix, in_specs)
            if src is None:
                continue
            dests = out_sinks[target_slot] if target_slot < len(out_sinks) else []
            for dest in dests:
                self.edges.append({"src": src, "dst": dest})
            if alias_out is not None:
                alias_out[target_slot] = src

    def _origin(self, origin_id, origin_slot, prefix: str, in_specs: list):
        if origin_id == -10:
            return in_specs[origin_slot] if origin_slot < len(in_specs) else None
        eid = prefix + str(origin_id)
        node = self.nodes.get(eid)
        if node is None:
            return None
        if node.get("subgraph"):
            self._expand(eid)
            return self.aliases.get((eid, origin_slot))
        return ("out", (eid, origin_slot))

    def _expand(self, eid: str) -> None:
        if eid in self.expanded:
            return
        if eid in self.expanding:
            raise ValueError(f"subgraph 存在循环引用: {eid}")
        ctx = self.instance_ctx.get(eid)
        if ctx is None:
            return
        self.expanding.add(eid)
        try:
            _prefix, _in_specs, out_sinks, parent_links = ctx
            node_entry = self.nodes[eid]
            raw_node = node_entry["raw"]
            raw_id = raw_node.get("id")
            sg = self.subgraphs[node_entry["type"]]
            subgraph_inputs = sg.get("inputs") or []
            raw_inputs = raw_node.get("inputs") or []

            in_map: list = []
            for slot, sg_input in enumerate(subgraph_inputs):
                outer = None
                for candidate in raw_inputs:
                    if candidate.get("name") == sg_input.get("name"):
                        outer = candidate
                        break
                if outer is None and slot < len(raw_inputs):
                    outer = raw_inputs[slot]
                spec = None
                if outer is not None:
                    link_id = outer.get("link")
                    if link_id is not None and link_id in parent_links:
                        origin_id, origin_slot, _t, _ts = parent_links[link_id]
                        spec = self._origin(origin_id, origin_slot, ctx[0], _in_specs)
                    elif outer.get("widget"):
                        widget = outer["widget"]
                        name = widget.get("name") if isinstance(widget, dict) else outer.get("name")
                        spec = ("lit", _widget_lookup(node_entry, name))
                    elif "widgets_values" in raw_node and outer.get("type") not in SOCKET_TYPES:
                        spec = ("lit", None)
                in_map.append(spec)

            sinks: list[list] = [[] for _ in sg.get("outputs") or []]
            for link_id, (origin_id, origin_slot, target_id, target_slot) in parent_links.items():
                if origin_id != raw_id:
                    continue
                if origin_slot >= len(sinks):
                    continue
                if target_id == -20:
                    if target_slot < len(out_sinks):
                        sinks[origin_slot].extend(out_sinks[target_slot])
                else:
                    target_eid = ctx[0] + str(target_id)
                    target_node = self.nodes.get(target_eid)
                    if target_node is None or target_node.get("subgraph"):
                        continue  # 目标子图实例会通过自己的 in_map 解析该连线
                    sinks[origin_slot].append((target_eid, target_slot))

            sub_aliases: dict[int, tuple] = {}
            self._level(sg, eid + ":", in_map, sinks, sub_aliases)
            for slot, spec in sub_aliases.items():
                self.aliases[(eid, slot)] = spec
        finally:
            self.expanding.discard(eid)
        self.expanded.add(eid)


class _APIBuilder:
    """把扁平图折叠辅助节点后转换成 /prompt 的 API prompt。"""

    def __init__(self, nodes: dict, edges: list[dict], object_info: dict | None = None):
        self.nodes = nodes
        self.incoming: dict[tuple[str, int], tuple] = {}
        for edge in edges:
            self.incoming.setdefault(edge["dst"], edge["src"])
        self.object_info = object_info
        self._folded: set[str] = set()

    # -- 取值 --------------------------------------------------------------
    def resolve(self, spec, stack: set | None = None):
        if spec is None:
            return None
        if isinstance(spec, tuple) and spec and spec[0] == "lit":
            return spec[1]
        if isinstance(spec, tuple) and spec and spec[0] == "out":
            eid, slot = spec[1]
            return self._output_value(eid, slot, stack if stack is not None else set())
        return None

    def _output_value(self, eid: str, slot: int, stack: set):
        node = self.nodes.get(eid)
        if node is None or node.get("subgraph"):
            return None
        key = (eid, slot)
        if key in stack:
            return None
        stack.add(key)
        try:
            mode = node.get("mode", 0)
            if mode == 2:
                return None
            if mode == 4:
                return self._bypass_value(eid, slot, stack)
            class_type = node.get("type")
            if class_type in _SIMPLE_PRIMITIVE_TYPES:
                return self._primitive_value(node)
            if class_type in ("ResolutionSelector", "ComfyMathExpression"):
                try:
                    value = (
                        self._resolution_value(eid, stack)[slot]
                        if class_type == "ResolutionSelector"
                        else self._math_value(eid, stack)[slot]
                    )
                except _NotFoldable:
                    return ("api", eid, slot)
                self._folded.add(eid)
                return value
            return ("api", eid, slot)
        finally:
            stack.discard(key)

    def _bypass_value(self, eid: str, slot: int, stack: set):
        node = self.nodes[eid]
        out_types = node.get("out_types") or []
        out_type = out_types[slot] if slot < len(out_types) else None
        for index, inp in enumerate(node.get("inputs") or []):
            if inp.get("type") != out_type:
                continue
            src = self.incoming.get((eid, index))
            if src is None:
                continue
            return self.resolve(src, stack)
        return None  # 没有同类型输入可透传，输出视为空

    @staticmethod
    def _primitive_value(node: dict):
        class_type = node.get("type")
        widgets = node.get("widgets") or []
        value = widgets[0][1] if widgets else None
        if class_type == "PrimitiveFloat":
            return float(value)
        if class_type == "PrimitiveInt":
            return int(value)
        if class_type == "PrimitiveBoolean":
            return bool(value)
        return value if isinstance(value, str) else str(value)

    def _resolution_value(self, eid: str, stack: set) -> tuple[int, int]:
        aspect = _widget_lookup(self.nodes[eid], "aspect_ratio")
        megapixels = _widget_lookup(self.nodes[eid], "megapixels")
        multiple = _widget_lookup(self.nodes[eid], "multiple")
        for name, current in (("aspect_ratio", aspect), ("megapixels", megapixels), ("multiple", multiple)):
            value = self._literal_input(eid, name, stack)
            if value is not None:
                if name == "aspect_ratio":
                    aspect = value
                elif name == "megapixels":
                    megapixels = value
                else:
                    multiple = value
        if aspect is None or megapixels is None:
            raise _NotFoldable("ResolutionSelector 参数不是字面量")
        return resolve_size(aspect, float(megapixels), int(multiple or CANVAS_MULTIPLE))

    def _literal_input(self, eid: str, name: str, stack: set):
        """取输入的字面量；若来源是真实节点输出（运行期才知道）则返回 None。"""
        node = self.nodes[eid]
        for index, inp in enumerate(node.get("inputs") or []):
            widget = inp.get("widget")
            widget_name = widget.get("name") if isinstance(widget, dict) else None
            if widget_name == name or inp.get("name") == name:
                src = self.incoming.get((eid, index))
                if src is None:
                    return _widget_lookup(node, name)
                value = self.resolve(src, stack)
                if isinstance(value, tuple) and value and value[0] == "api":
                    return None
                return value
        return None

    def _math_value(self, eid: str, stack: set):
        node = self.nodes[eid]
        expression = _widget_lookup(node, "expression")
        if not isinstance(expression, str):
            raise _NotFoldable("ComfyMathExpression 缺少字面量表达式")
        values: dict[str, object] = {}
        for index, inp in enumerate(node.get("inputs") or []):
            name = str(inp.get("name") or "")
            if not name.startswith("values."):
                continue
            letter = name.split(".", 1)[1]
            src = self.incoming.get((eid, index))
            if src is None:
                continue
            value = self.resolve(src, stack)
            if isinstance(value, tuple) and value and value[0] == "api":
                raise _NotFoldable("ComfyMathExpression 变量来源是运行期节点")
            values[letter] = value
        result = _eval_math_expression(expression, values)
        return float(result), int(result), bool(result)

    # -- 输出 --------------------------------------------------------------
    def build(self) -> dict:
        # 预折叠纯静态辅助节点；含运行期依赖的节点会保留为普通节点
        for eid, node in self.nodes.items():
            if node.get("subgraph") or node.get("mode", 0) != 0:
                continue
            if node.get("type") not in ("ResolutionSelector", "ComfyMathExpression"):
                continue
            try:
                value = self._output_value(eid, 0, set())
            except (ValueError, TypeError):
                continue
            if not (isinstance(value, tuple) and value and value[0] == "api"):
                self._folded.add(eid)

        api: dict[str, dict] = {}
        for eid, node in self.nodes.items():
            class_type = node.get("type")
            if node.get("subgraph") or class_type in _DROP_NODE_TYPES:
                continue
            if class_type in _SIMPLE_PRIMITIVE_TYPES or eid in self._folded:
                continue
            if node.get("mode", 0) in (2, 4):
                continue
            inputs: dict[str, object] = {}
            for index, inp in enumerate(node.get("inputs") or []):
                name = _api_input_name(inp)
                if name is None:
                    continue
                src = self.incoming.get((eid, index))
                if src is not None:
                    value = self.resolve(src)
                    if value is None:
                        continue
                    if isinstance(value, tuple) and value and value[0] == "api":
                        inputs[name] = [value[1], value[2]]
                    else:
                        if _schema_type_is_int(self.object_info, class_type, name) and isinstance(value, float) and value.is_integer():
                            value = int(value)
                        inputs[name] = value
                elif inp.get("widget") and inp.get("type") not in _UPLOAD_TYPES:
                    widget_name = name
                    widget_value = _widget_lookup(node, widget_name)
                    if widget_value is not None:
                        inputs[name] = widget_value
            for widget_name, widget_value, widget_inp in node.get("widgets") or []:
                if widget_name in inputs:
                    continue
                if widget_inp.get("type") in _UPLOAD_TYPES:
                    continue
                if widget_value is not None:
                    inputs[widget_name] = widget_value
            api[str(eid)] = {
                "class_type": class_type,
                "inputs": inputs,
                "_meta": {"title": class_type},
            }
        return self._prune(api)

    def _prune(self, api: dict) -> dict:
        output_classes = set(FALLBACK_OUTPUT_NODES)
        if self.object_info:
            output_classes = {
                name for name, info in self.object_info.items()
                if isinstance(info, dict) and info.get("output_node")
            }
        output_ids = [eid for eid in api if self.nodes[eid]["type"] in output_classes]
        reachable: set[str] = set()
        stack = list(output_ids)
        while stack:
            eid = stack.pop()
            if eid in reachable or eid not in self.nodes:
                continue
            reachable.add(eid)
            for index, _inp in enumerate(self.nodes[eid].get("inputs") or []):
                src = self.incoming.get((eid, index))
                if src and src[0] == "out":
                    stack.append(src[1][0])
        return {eid: node for eid, node in api.items() if eid in reachable}


def _schema_type_is_int(object_info: dict | None, class_type: str, input_name: str) -> bool:
    if not object_info:
        return False
    info = object_info.get(class_type)
    if not isinstance(info, dict):
        return False
    required, optional = _schema_parts(info)
    spec = required.get(input_name) or optional.get(input_name)
    return bool(spec) and spec[0] == "INT"


def ui_to_api(ui: dict, object_info: dict | None = None) -> dict:
    """把 UI 工作流（含 definitions.subgraphs）转换成 /prompt 可用的 API prompt。

    支持：
    - 展开 subgraph（节点 id 变成 "<实例id>:<内部id>"）；
    - mode=4（bypass）透传同类型输入，mode=2（mute）节点被丢弃；
    - 折叠 MarkdownNote、Primitive*、ResolutionSelector、ComfyMathExpression；
    - 只保留从输出节点可达的节点。

    限制：嵌套 subgraph 的输入由父级 link 解析，跨 subgraph 的兄弟连线按先展开
    顺序解析；未在 object_info 中的自定义节点会原样保留。
    """
    if not isinstance(ui, dict):
        raise ValueError("ui_to_api 需要 UI 工作流 dict")
    flattener = _UIFlattener(ui)
    nodes, edges = flattener.run(ui)
    if object_info is None:
        object_info = load_cached_object_info()
    builder = _APIBuilder(nodes, edges, object_info)
    return builder.build()
