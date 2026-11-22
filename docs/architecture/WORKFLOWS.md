# MiniMax-H3 工作流模板（comfy_mcp）

本文档说明 `comfy_mcp/templates/` 下三个 **API 格式**工作流模板（可直接作为
`POST /prompt` 的 `prompt` 字段提交），以及 `comfy_mcp/workflow.py` 的参数解析、
校验与 UI→API 转换规则。

> 模板与 MCP 工具都不会自动提交任务；真正的生成由 `generate` 工具在资源守卫
> （safety.py）通过后才入队。本文只描述「怎么构造 prompt」。

---

## 1. 三个模板

| 模板 | 模型 | 条件节点 | 输入 | 适用场景 |
|---|---|---|---|---|
| `minimax_h3_r2v` | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | `MiniMaxH3ReferenceToVideo` | 提示词 + 0-9 张参考图（可选参考视频/音频） | 角色/风格/场景一致性；提示词用 `<Picture i>` 引用参考图 |
| `minimax_h3_fl2v` | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | `MiniMaxH3ImageToVideo` | 提示词 + 可选首帧/尾帧 | 首尾帧插值、镜头延续；可挂 turbo LoRA |
| `minimax_h3_t2v` | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | `MiniMaxH3ImageToVideo` | 仅提示词（不接帧） | 纯文生视频 + 原生立体声 |

`fl2v` 与 `t2v` 使用同一个 `fl2va` 模型和同一个节点；区别只在是否给
`first_frame` / `last_frame`。`t2v` 模板的 `resolve()` 会忽略并 warning 帧参数，
保证语义清晰。

### 1.1 公共节点链路

```
UNETLoader ──┬──────────────────────────────> BasicScheduler ─┐
             └──────────────────────────────> BasicGuider <──┐ │
CLIPLoader ───────────────────────────────┐                  │ │
VAELoader(video) ─────────────────────────┤                  │ │
VAELoader(audio) ─────────────────────────┤   MiniMaxH3*    │ │
                                           └──> (条件节点) ──┤ │
RandomNoise ─────────────────────────────────────────────────> SamplerCustomAdvanced
KSamplerSelect ──────────────────────────────────────────────>        │
                                                                      ├─> VAEDecode(video VAE) ──┐
                                                                      └─> VAEDecodeAudio(audio VAE) ─┤
                                                                                                     v
                                                              CreateVideo(fps=24, bit_depth=8) ─> SaveVideo
```

- 采样链路（无 CFG）：`RandomNoise` + `BasicGuider` + `KSamplerSelect` +
  `BasicScheduler`（model 接 UNETLoader）→ `SamplerCustomAdvanced`。
- 解码：同一个采样 latent 分别给 `VAEDecode`（视频 VAE）和 `VAEDecodeAudio`
  （音频 VAE），再 `CreateVideo` 合成 24fps、8bit 视频，最后 `SaveVideo`。
- `SaveVideo`：`format="auto"`、`codec="auto"`、`filename_prefix` 可参数化。
- 模板不含 `LoadImage` / `LoraLoaderModelOnly` 等可选节点；`resolve()` 只在
  需要时插入，避免出现不执行的「静默节点」。

### 1.2 r2v 参考图节点

`MiniMaxH3ReferenceToVideo` 需要 2 个 VAE（`vae`=视频 VAE、`audio_vae`=音频 VAE）
和 `ref_image_size`；参考图通过 `LoadImage` → `ref_images.ref_image_N` 注入：

```
LoadImage(ref 0) ─┐
LoadImage(ref 1) ─┼─> MiniMaxH3ReferenceToVideo.ref_images.ref_image_0/1/...
LoadImage(ref N) ─┘
```

---

## 2. 参数表

所有模板共享同一套参数（`resolve(name, params)`，未给时按 profile 默认）：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `prompt` | string | 必填 | 提示词；r2v 用 `<Picture 1>`/`<Video 1>`/`<Audio 1>` 引用素材 |
| `profile` | draft/standard/high | `settings.generation.default_profile`（默认 draft） | 档位预设 |
| `width`, `height` | int | 由 megapixels+aspect 计算 | 显式宽高，**必须成对**；会四舍五入对齐 32 |
| `megapixels` | float | profile 值 | 目标像素量（1.0MP 按 1024×1024 计） |
| `aspect` | string | `16:9` | `1:1,2:3,3:2,3:4,4:3,9:16,16:9,21:9`，可带 `(Widescreen)` 后缀 |
| `duration_s` | float | profile 值 | 秒 → 帧；与 `length` 二选一 |
| `length` | int | — | 帧数（24fps），会 snap 到 17k+5 |
| `seed` | int | None=随机 | 随机种子，回传于 `resolved.seed` |
| `steps` | int | profile 值 | 采样步数；turbo LoRA 可用 4-8 |
| `sampler` | string | `res_multistep` | `KSamplerSelect` 取值 |
| `scheduler` | string | `simple` | `BasicScheduler` 取值 |
| `output_prefix` | string | 模板默认 `video/MiniMax_H3_*` | `SaveVideo.filename_prefix` |
| `negative_prompt` | string | None | **H3 无 CFG**，传入会被忽略并 warning |

模板附加参数：

| 模板 | 参数 | 说明 |
|---|---|---|
| r2v | `ref_images` | 输入目录文件名列表（0-9 张）；第 i 张对应 `<Picture i+1>` |
| r2v | `ref_image_size` | `match`（缩到生成像素面积）或 `max`（参考管线 2048 短边，更还原但更慢） |
| fl2v | `first_frame`, `last_frame` | 输入目录图片文件名；可只给首帧或只给尾帧 |
| fl2v/t2v | `lora_name`, `lora_strength` | 可选 LoRA（`loras/` 相对路径，如 `minimax_h3\minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors`） |

`resolve()` 返回：

```python
{
  "name": "minimax_h3_r2v",
  "api": { ... },                     # 可直接 POST /prompt
  "resolved": {                       # 已落地的参数 + 派生值
    "prompt": "...", "width": 864, "height": 480, "length": 124,
    "duration_s": 5.167, "fps": 24, "seed": 123, "steps": 20,
    "sampler": "res_multistep", "scheduler": "simple",
    "ref_image_size": "match", "output_prefix": "video/MiniMax_H3_r2v",
    "profile": "standard", "pixels": 414720,
    "ref_images": [], "first_frame": None, "last_frame": None,
    "lora_name": None, "lora_strength": 1.0
  },
  "warnings": [ ... ],
}
```

参数优先级：

1. 尺寸：显式 `width`/`height` > `megapixels`+`aspect` > profile；
2. 时长：`length` > `duration_s` > profile.duration_s；
3. 采样：显式 `steps`/`sampler`/`scheduler` > profile。

---

## 3. 尺寸规则

- 模型画布要求：宽高**必须 32 对齐**（`nodes_minimax_h3.py` 里
  `CANVAS_MULTIPLE = 32`，latent 尺寸为 `height//16, width//16`，非 32 对齐会让
  latent 与非整数格不对应）。
- `resolve_size(aspect, megapixels, multiple=32)` 与 ComfyUI 内置
  `ResolutionSelector`（`comfy_extras/nodes_resolution.py`）**同算法**：
  `total = megapixels * 1024 * 1024`，`scale = sqrt(total/(w_ratio*h_ratio))`，
  `width = round(w_ratio*scale/32)*32`，height 同理。
- 示例（16:9，与官方模板 MarkdownNote 表格一致）：

| megapixels | 尺寸 |
|---|---|
| 0.2 | 608×352 |
| 0.3 | 736×416 |
| 0.4 | 864×480 |
| 0.6 | 1056×608 |
| 0.8 | 1216×672 |
| 0.98 | 1344×768 |
| 1.0 | 1376×768 |
| 2.0 | 1920×1088 |

- 显式 `width`/`height` 若不在 32 网格上会被**四舍五入对齐**并 warning；
- `pixels > settings.generation.max_pixels`（默认 1920×1088）只 warning，
  最终由 `safety.preflight` 决定是否拒绝；
- 注意：原始 UI 工作流 `video_minimax_h3_r2v.json` 的 `ResolutionSelector` 存了
  `multiple=36`，会算出 864×468（非 32 对齐）。**不要沿用该值**；模板固定 32 对齐，
  转换时 `ui_to_api` 原样保留 468，需要时由调用方对齐（测试里显式 canonicalize）。

---

## 4. 时长 / 帧数规则

- 模型帧率：**24fps**（`nodes_minimax_h3.py::FPS = 24`）。
- 帧数网格：`align_frame_count(n)` 把 n 向上对齐到 **17k+5**：
  5, 22, 39, 56, 73, 90, 107, **124**, 141, ... 345, **362**。
- 5 秒 = 120 帧 → 124；15 秒 = 360 帧 → 362。
- 训练区间 **124-362 帧**（约 5.17s-15.08s）；更短画质可能不稳，更长属于未训练区间。
- `duration_to_length(seconds) = align_frame_count(max(5, round(seconds*24)))`：

| duration_s | length（帧） | 实际时长 |
|---|---|---|
| 1 | 39 | 1.625s |
| 2 | 56 | 2.333s |
| 5 | 124 | 5.167s |
| 10 | 243 | 10.125s |
| 15 | 362 | 15.083s |

- `length` 超过 `settings.generation.max_length`（默认 362）会被**截断**并 warning
  （避免 8GB VRAM 机器直接 OOM）。

---

## 5. 参考图用法（r2v）

- 参考图最多 **9 张**（节点 schema `ref_images` 的 `max=9`，`min=0`），文件名是
  ComfyUI `input/` 目录下的图片名（可用 `upload` 工具先上传）。
- 提示词里用 **1-based** 标签引用：第 1 张 → `<Picture 1>`，第 2 张 → `<Picture 2>`。
  参考视频/音频同理：`<Video k>` / `<Audio j>`；带声轨的参考视频，其音轨标签
  `<Audio j>` 出现在对应 `<Video k>` 之前。
- `ref_image_size`：
  - `match`（默认）：每张参考图按「只缩小、保持比例」压到生成画面的像素面积；
  - `max`：使用参考管线的 2048px 短边，身份还原更好，但 reference token 每步都参与
    采样，可能慢数倍。
- 示例：

```python
tpl.resolve("minimax_h3_r2v", {
    "prompt": "<Picture 1> 里的少女在雨夜街头回头，镜头缓慢推近。",
    "ref_images": ["girl_ref.png"],
    "ref_image_size": "match",
    "profile": "standard",
})
```

生成出的 API 片段（注意键名是**扁平点号**）：

```json
"10": {
  "class_type": "MiniMaxH3ReferenceToVideo",
  "inputs": {
    "clip": ["2", 0], "vae": ["3", 0], "audio_vae": ["4", 0],
    "prompt": "<Picture 1> 里的少女……",
    "width": 864, "height": 480, "length": 124,
    "ref_image_size": "match",
    "ref_images.ref_image_0": ["100", 0]
  }
}
```

---

## 6. autogrow 输入键名（重要，含证据）

**结论：API prompt 里 autogrow 输入必须写成扁平点号键**
`"ref_images.ref_image_0"`、`"ref_images.ref_image_1"`……（以及
`ref_videos.ref_video_0`、`ref_video_audios.ref_video_audio_0`、
`ref_audios.ref_audio_0`）。**不要**写成嵌套字典
`{"ref_images": {"ref_image_0": [...]}}`，也不要传容器名 `"ref_images"`。

证据（本机 ComfyUI 0.33.1 源码，路径相对 `ComfyUI/`）：

1. `comfy_api/latest/_io.py`
   - `finalize_prefix()`（约 L1074-1080）：用 `"."` 连接层级前缀与输入名；
   - `parse_class_inputs()`（约 L1845-1859）：对 `COMFY_AUTOGROW_V3` 调用
     `_expand_schema_for_dynamic`，并传入 `new_prefix = handle_prefix(curr_prefix, id)`；
   - `Autogrow._expand_schema_for_dynamic()`（约 L1168-1217）：
     `expected_id = finalize_prefix(curr_prefix, name)`，即 `"ref_images.ref_image_0"`，
     然后 `if expected_id in live_inputs:` —— 这里的 `live_inputs` 就是 /prompt 里
     节点的 `inputs` 字典。**命中判断直接使用点号扁平键**。
2. `execution.py::validate_inputs()`（约 L880-883）：
   `inputs = prompt[unique_id]['inputs']`，再
   `get_finalized_class_inputs(class_inputs, inputs)` —— 校验时同样按扁平键展开。
3. `execution.py::_async_map_node_over_list()`（约 L286-288）在真正执行前调用
   `_io.build_nested_inputs(inputs, v3_data)`，由 `dynamic_paths` 把
   `"ref_images.ref_image_0"` 重新组装成嵌套 dict 传给节点函数
   `execute(..., ref_images={"ref_image_0": tensor, ...})`。
   → 节点实现 `for img in (ref_images or {}).values()` 只关心组装后的 dict。
4. 原始 UI 工作流里保存的 socket 名同样是
   `"ref_images.ref_image_0"`（`video_minimax_h3_r2v.json` 节点 136），
   与前端 graphToPrompt 的输出一致。

**风险**：如果键名写错（例如用嵌套 dict 或容器名），ComfyUI 的
`validate_inputs()` **不会报错**（未声明的键被静默忽略），但参考图不会生效——
生成结果会「看起来成功但没用到参考图」。因此 `workflow.validate()` 把
autogrow 容器名/嵌套写法标为 **error**，把未知输入名标为 **warning**。
新增参考视频/音频时同样保持点号键。

---

## 7. SaveVideo 的 codec / format（含证据）

**结论：API prompt 里 `"format": "auto"`、`"codec": "auto"` 都是字符串选项键，
直接传字符串即可。**

证据：

- `comfy_extras/nodes_video.py::SaveVideo`（L75-149）：`format` 是普通
  `io.Combo`（选项 `auto`/`mp4`）；`codec` 是
  `io.DynamicCombo.Input("codec", options=[Option("auto", []), Option("h264", [...])])`；
  `execute(..., codec)` 拿到的是嵌套 dict（`codec["codec"]`），由
  `build_nested_inputs` 从扁平键 `"codec": "auto"` 组装而来。
- `comfy_api/latest/_io.py::DynamicCombo._expand_schema_for_dynamic()`（约 L1255-1270）：
  `key = live_inputs[finalized_id]`，在 options 里按 `option["key"]` 匹配——即
  /prompt 传入的就是 option key 字符串。
- `GET /object_info/SaveVideo` 实测：`codec` 的 `options[].key` 为 `["auto", "h264"]`。

**风险**：`COMFY_DYNAMICCOMBO_V3` 不在 `execution.py` 的 combo 白名单校验分支里，
传入非法 codec 字符串不会被 `validate_inputs` 拦下，可能到执行期才报错。
`workflow.validate()` 会显式校验它是否属于 option key。

---

## 8. 推荐档位与风险（8GB VRAM / 32GB RAM, RTX 4070 Laptop）

| 档位 | 分辨率 | 帧数 | steps | 说明 |
|---|---|---|---|---|
| draft | 608×352（0.2MP, 16:9） | 56（≈2.3s） | 8 | 冒烟/试提示词，显存最稳 |
| standard | 864×480（0.4MP） | 124（≈5.2s） | 20 | 默认推荐；配 r2v `match` |
| high | 1216×672（0.8MP） | 124 | 24 | 质量优先；与其它进程同时跑有 OOM 风险 |

风险点：

1. **模型很大**：UNET ~19.5GB（int8）、文本编码器 ~14.6GB，首次加载慢且吃内存；
   单飞执行（`safety.single_flight`）必须保持开启。
2. **ref `max` 模式**：参考 token 每步参与，速度可能慢数倍，显存压力更大。
3. **15s/362 帧**：属于训练区间上沿，显存与耗时都最高；8GB 机器建议先 standard。
4. **H3 无负面提示词**：`negative_prompt` 会被忽略（warning），不要依赖它修画面。
5. **UI 工作流 multiple=36 的坑**：见第 3 节，宽高非 32 对齐可能导致 latent/像素尺寸
   不一致，模板统一用 32。
6. **参考图未生效是静默失败**：见第 6 节，务必用 `validate()` 检查无 error。
7. **autogrow 槽位名**：`ref_image_9` 超出 `max=9`（合法 0-8），会被静默忽略；
   `validate()` 会报错。

---

## 9. validate() 能发现什么

`validate(api, object_info=None)` 返回 `{"ok", "errors", "warnings"}`：

- ❌ 未知 `class_type`（节点未安装/拼写错误）；
- ❌ 必需输入缺失、必需输入为 null、字面量类型错误、combo 值不在选项内、
  数值越界；
- ❌ 连线目标节点不存在、槽位越界、socket 类型不匹配；
- ❌ autogrow 使用容器名/嵌套写法、autogrow 槽位越界；
- ❌ 没有输出节点（不会有任何文件产出）；
- ⚠️ 静默节点（不在输出依赖链上，不会执行）、输出无人使用、未知输入名。

`object_info` 为 `None` 时读取 `tests/fixtures/object_info.json` 快照；读不到就只做
图结构校验并给出 warning。生产端建议传入
`client.object_info()` 的实时结果。

---

## 10. ui_to_api()：导入旧 UI 工作流

`ui_to_api(ui_workflow, object_info=None)` 把 ComfyUI 前端保存的 UI 工作流转成
API prompt：

- 展开 `definitions.subgraphs`：内部节点 id 变为 `"<实例id>:<内部id>"`；
  subgraph 的 promoted widget（如 VIDOH3 的 prompt/width/length/noise_seed）会作为
  字面量传给内部节点；
- `mode=4`（bypass）：若存在同类型输入则透传，否则输出视为空并丢弃节点；
- `mode=2`（mute）：节点及其下游连线丢弃；
- 折叠 `MarkdownNote`、`PrimitiveFloat/Int/String`、`ResolutionSelector`、
  `ComfyMathExpression`（静态求值；含运行期依赖时保留为普通节点）；
- 只保留从输出节点可达的节点（避免静默节点）。

已用两份原始工作流验证：

- `video_minimax_h3_r2v.json` → 15 个节点，`validate()` 0 error；
  折叠 15s→362 帧、864×468（UI 原值）、保留 1 张参考图（另外 2 个 LoadImage 是
  `mode=4` bypass，TESpeed 节点同样被丢弃）；
- `VIDOH3.json` → 14 个节点（含 subgraph 展开），`validate()` 0 error；
  折叠 2s→56 帧、864×480。

测试里把转换结果反向提取成 `resolve()` 参数，与手写模板做
「类多重集 + 类间连线 + 字面量」等价签名比对（`tests/test_workflow.py`）。

限制：未在 `object_info` 中的自定义节点会原样保留；跨多个 subgraph 的复杂嵌套
连线按展开顺序解析，若遇到循环/无法解析会显式报错而不是静默产出错误图。

---

## 11. 测试

```powershell
& 'K:\CONFI\mian\ComfyUI_windows_portable\python_embeded\python.exe' -m unittest discover -s K:\CONFI\comfy_mcp\tests -v
```

测试不会提交任何真实生成任务（H3 模型 19.5GB，禁止误触发）；模板正确性全部用
`tests/fixtures/object_info.json`（从运行中的 ComfyUI `/object_info` GET 缓存）校验。


---

## 12. 图像生成模板（minimax_h3_image，2026-11-15 新增）

在原有三个视频模板之外，新增 **text-to-image 静帧**模板：

- 链路：`MiniMaxH3ImageToVideo(length=5)` → 采样 → `VAEDecode` → **`ImageFromBatch`(取首帧)** → **`SaveImage`(PNG)**；
- 不接 AudioVAE/CreateVideo，无音视频产出；
- 默认 768×768、8 步、`optimize=max`；实测总耗时 180–190s，输出 1 张 PNG（约 600–800KB）；
- 支持 `first_frame`（图生图构图）、LoRA、模型覆盖；**禁 two_pass**（resolve 直接报错）；
- 模板上限 39 帧（多帧仅取第 1 帧）；H3 训练区间 124–362，5 帧为最小合法静帧。

完整参数/用法/实测见 **[WORKFLOW_H3_IMAGE.md](../features/WORKFLOW_H3_IMAGE.md)**。


---

## 13. Qwen-Image 2.1 文生图模板（qwen_image，2026-11-15 新增）

- **前置：ComfyUI 已升级到 v0.45.3**（v0.33.1 没有 Qwen-Image 2.1 节点）；代码/依赖备份见 `docs/features/WORKFLOW_QWEN_IMAGE21.md`。
- 模型：`qwen_image_2.1_int8_convrot`（6.76GB）+ `qwen3vl_8b_int8_convrot`（TE, 8.71GB）+ `qwen_image_2.1_vae_bf16`（0.63GB）。
- 链路：`UNETLoader → CLIPLoader(type=qwen_image) → TextEncodeQwenImage21 → EmptyLatentImage → SamplerCustomAdvanced(euler/simple,25 步) → VAEDecode → SaveImage`。
- 默认 1024×1024、25 步；实测 **51 秒**出图（含加载），1024 PNG 约 1.7MB。
- 支持 LoRA（可配 turbo 模型 5–8 步）、模型覆盖；不支持 two_pass / H3 optimize 补丁（会被忽略并 warning）。
- 完整说明见 **[WORKFLOW_QWEN_IMAGE21.md](../features/WORKFLOW_QWEN_IMAGE21.md)**。


---

## 14. Qwen-Image 2.1 图像编辑模板（qwen_image_edit，2026-11-15）

- `TextEncodeQwenImage21` 的 `images` autogrow 接 `LoadImage`（`images.image_1..10`），latent 取条件节点参考图 latent `[4,2]`；
- 参数 `images: list[string]` + `prompt`（编辑指令）+ `width/height`（→ resolution，默认 1024）；
- 实测：苹果照→水彩 42s、铅笔素描正常，构图保留；
- Web UI 已加「图像编辑 · Qwen」模式（上传参考图）、PNG 结果预览与 `/api/images` 图片作品页。

> 守卫已按模板区分：图像模板允许到 2048×2048（2K），视频仍 1920×1088；Web 预设含 1536/2048。

> 生图独立页面（/studio?tab=image）+ 作品页分页（/api/gallery，去重、24/页）。
