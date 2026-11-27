# 可选 Skill：handraw-style

来源：https://github.com/yang0/handraw-style

本项目已将完整 Skill 安装到：

```text
optional/skills/handraw-style/
```

用途：

- 372 种手绘/写实风格；
- 36 种主题色；
- 170 种排版图型；
- 生图模块「手绘风格」选择器与提示词生成 API。

## API

```text
GET  /api/handraw/catalog
POST /api/handraw/prompt
```

示例：

```json
{"styles":["FA-001"],"colors":["C-01"],"theme":"雨夜街角的猫"}
```

## 来源与许可

Skill 内容版权归原作者 yang0 及上游贡献者，遵循该仓库的 LICENSE。  
本项目的 `handraw.py` 仅读取其 JSON 元数据并组装提示词。

## Agent / MCP 工具

MCP 工具 `handraw_prompt` 可直接被 agent 调用：

```json
{"styles":["FA-001"],"colors":["C-01"],"theme":"雨夜街角的猫","lang":"zh"}
```

返回基于 handraw-style 规则的优化提示词，不提交图像生成。

## DeepSeek Agent

在生图页手绘面板填写 DeepSeek API Key，或设置环境变量 `DEEPSEEK_API_KEY`。

- `POST /api/agent/key`：保存 Key（本地 `data/deepseek_key.txt`，不提交仓库）
- `POST /api/handraw/agent`：基于 handraw-style 元数据调用 DeepSeek 优化提示词
- `POST /api/handraw/translate`：读取本地 372 个中文名（已随 Skill 缓存，无需 Key）

风格名中文翻译已内置在 `handraw_zh.json`，不需要 Key。DeepSeek Key 仅用于提示词补全/优化；未配置时规则优化仍可用。
