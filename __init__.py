"""comfy_mcp — 本地 ComfyUI（MiniMax-H3 视频生成）MCP 服务。

模块：
    config      路径与可调设置（data/settings.json 覆盖）
    client      ComfyUI HTTP 客户端
    safety      硬件探测 / 成本估算 / 资源守卫
    jobs        本地任务存储
    workflow    工作流模板（API 格式）/ 参数解析 / 校验 / UI→API 转换
    editing     ffmpeg 剪辑
    server      MCP 工具层（stdio JSON-RPC）
"""

__version__ = "1.0.0"
