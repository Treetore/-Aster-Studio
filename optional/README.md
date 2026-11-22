# 可选组件

本目录存放不属于主流程的可选组件源码。这些组件**默认不安装、不参与启动**，未安装时 Web 工作台、桌面启动器与 MCP 接口的行为不受影响。

| 组件 | 用途 | 依赖 |
|---|---|---|
| `astrbot_plugin_comfy_bridge/` | AstrBot 聊天插件：把聊天提示词转发到本机生成服务并回传结果 | AstrBot 4.16+（自带 `aiohttp`），本机 Aster Studio 运行中 |

## 安装 astrbot_plugin_comfy_bridge

1. 确认主服务可访问：`GET /api/bridge/health` 返回 `{"ok": true}`；
2. 取得 bridge 令牌：服务端 `data/bridge_token.txt`，或部署时设置的 `COMFY_BRIDGE_TOKEN`；
3. 将 `astrbot_plugin_comfy_bridge/` 整个目录复制到 AstrBot 的插件目录（通常位于 AstrBot 数据目录下的 `data/plugins/`）；
4. 重启 AstrBot，在插件配置页填写：
   - `base_url`：同机部署填 `http://127.0.0.1:8190`；跨机部署填 `http://<HOST>:8190`；经隧道访问填 `<BRIDGE_URL>`；
   - `token`：上一步取得的令牌；
5. 在聊天中执行 `/诊断` 或 `/模型` 验证连通性。

配置项与指令说明见 [astrbot_plugin_comfy_bridge/README.md](astrbot_plugin_comfy_bridge/README.md)。

## 安全注意事项

- 插件只应访问 `/api/bridge/*`。不要将 `/studio`、`/api/settings`、`/api/logs` 暴露给聊天平台。
- 对外暴露时必须同时启用令牌与 IP 白名单；服务端默认回环绑定，不要改成 `0.0.0.0`。
- 令牌属于 bearer 凭据：不要写入版本库、截图或公开日志；轮换方式与报告流程见项目根 [SECURITY.md](../SECURITY.md)。
- 本目录文件中的地址、主机名与 IP 均为占位示例（`<BRIDGE_URL>`、`<HOST>`、`203.0.113.10`），部署时替换为实际值。

## 维护说明

- 插件源码与主项目分离演进，版本号记录在 `metadata.yaml`；本目录副本随主项目发布，不承诺与其他分支同步。
- 修改插件时不要引入令牌、隧道域名、私有 IP 或账号标识；不确定的值一律使用占位符。
