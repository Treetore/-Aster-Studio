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
