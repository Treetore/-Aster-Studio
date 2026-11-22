# 长片自动成片管线（2026-11-15）

面向"只填提示词 + 参考图"的自动化：分段生成、首帧接力、拼接、进度与取消，全部由程序完成。
整段直出（ContextWindows）作为备选模式保留。

## 1. 界面

Web 工作台左侧新增 **长片**（`/studio?tab=film`）：

- 提示词（单条，所有分段共用；模型自己保持连续）
- 参考图（≤4 张）：
  - 1 张 → 作为第 1 段首帧（fl2v）
  - ≥2 张 → 第 1 段走参考图模型（r2v），后续段用首帧接力
  - 0 张 → 纯文生视频
- 模式：**分段（推荐，约 20–30 分钟）** / **整段直出（约 45–60 分钟）**
- 段数 2–4、尺寸 1280×736（720p）/1024×576/608×352、步数 4/6/8
- 开始 / 取消；右侧实时显示分段进度、用时、状态与成片预览

启动器「小工具」页的「长片成片」卡片直接打开该页面。

## 2. 自动化流程

```
POST /api/film/start
  └─ tools/segment_film.py（后台子进程）
       ├─ 第 1 段：refs≥2 → r2v；refs=1 → fl2v(first_frame)；否则 t2v
       ├─ 每段生成后 ffmpeg 抽最后一帧 → ComfyUI/input/sf_<tag>_segNN_last.png
       ├─ 下一段 fl2v(first_frame=该 PNG) 自动接力
       ├─ 全部完成 → ffmpeg concat（视频+音频，重编码 h264/aac）
       └─ 状态写入 data/segment_film_state.json；data/segment_film_cancel.flag 可取消
```

- 参数默认：1280×736、124 帧/段、4 步 turbo4 LoRA、Sage 优化、普通 VAE 解码
- 分段缓存：`logs/segment_film_<tag>/segNN.mp4`，中断后重跑会复用已完成段
- 输出：`ComfyUI/output/video/<tag>.mp4`（或 `<tag>_single.mp4`）

## 3. API

```
POST /api/film/start   {prompt, refs[], segments, frames, width, height, steps, mode}
GET  /api/film/state   → {ok, state:{status,segments_done,current_segment,elapsed_s,message,output,segments[]}, log_tail}
POST /api/film/cancel  → 写取消标记，工具在下一个轮询点中断并清理队列
```

状态值：`running | concat | done | failed | cancelled | timeout`。

## 4. 冒烟验证（2026-11-15）

1 段 × 22 帧 / 608×352 / 2 步：**233s 完成**，输出 `smoke.mp4` 0.92s、608×352、24fps、AAC 立体声；
状态文件 `data/segment_film_state.json` 正确记录 `done` 与分段信息；网页「长片」页显示完成与预览。

## 5. 限制

1. 分段拼接处音频由各段独立生成，听感可能不连续；需要连续配乐可后期替换。
2. 首次模型初始化在 RAM 紧张时可达 5 分钟；后续段约 1.5 分钟。
3. 4 步 turbo 画质偏软；可把步数调到 6/8（每段 +1.5/+3 分钟）。
4. 720p 下每段解码约 3–6 分钟，是除采样外的主要开销。
