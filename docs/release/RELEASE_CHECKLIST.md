# 发布检查清单（1.0.0）

本清单用于源码发布前的自检。每项都应留下可复核的结果（命令输出、截图或结论），不要仅凭记忆勾选。

环境变量约定（示例）：

```powershell
$Repo = "K:\path\to\comfy_mcp"
Set-Location $Repo
```

## 1. 许可与法务

- [ ] `LICENSE` 为 Apache License 2.0 全文，`END OF TERMS AND CONDITIONS` 与 `APPENDIX` 段落完整。
- [ ] `NOTICE` 列出第三方组件（ComfyUI、Qwen、MiniMax H3、MOSS、ffmpeg）及其上游链接，并包含"不分发模型权重、用户须遵守上游模型许可"的声明。
- [ ] `pyproject.toml` 的 `[project].license` 与 `LICENSE` 一致（当前仍为 `Proprietary`，发布前必须改为 Apache-2.0 表述或移除该字段）。
- [ ] 仓库内不存在 `*.safetensors`、`*.ckpt`、`*.pt`、`*.pth`、`*.gguf`、`*.mp4`、`*.webm`。
- [ ] `data/`、`logs/`、`.env`、`backups/`、`dist/`、`bundle/` 未被跟踪（`data/.gitkeep` 除外）。

```powershell
git ls-files | Select-String -Pattern '\.(safetensors|ckpt|pt|pth|bin|gguf|mp4|webm|mov)$'
git ls-files | Select-String -Pattern '(^|/)(data|logs)/' | Select-String -NotMatch '\.gitkeep'
```

## 2. 文档一致性（已实现 vs 规划中）

- [ ] `README.md` 的"已实现能力"逐项能在代码中找到入口；"未实现与限制"覆盖模型下载、模型上传、向导安装 ComfyUI、鉴权、并发与跨平台。
- [ ] `SETUP.md` 的路径解析顺序、环境变量名与 `config.py`、`.env.example` 一致。
- [ ] 自检项名称与 `setup/doctor.py` 输出的 `code` 一致（`python_version`、`comfyui_main`、`web_assets`、`ffmpeg`、`models`、`data_writable`、`templates`、`torch`、`comfy_port`）。
- [ ] 文档未出现"自动下载模型""自动下载 ComfyUI 向导""模型上传"等与实际不符的表述。
- [ ] 文档中的主机名、IP、令牌一律为占位符（`<BRIDGE_URL>`、`<HOST>`、`203.0.113.10`）。
- [ ] `CHANGELOG.md` 含 `Added` / `Changed` / `Fixed` / `Known limitations` 四类，版本与日期正确。

## 3. 链接与文案检查

- [ ] 所有相对链接可解析（下段脚本输出为空）。
- [ ] 无个人叙事、测试日记、账号信息或内部路径泄漏到大纲类文档。

```powershell
# 相对链接解析检查：输出缺失目标，正常应为空
Get-ChildItem -Recurse -File -Include *.md |
  Where-Object { $_.FullName -notmatch '\\\.git\\|\\optional\\astrbot_plugin_comfy_bridge\\README\.md$' } |
  ForEach-Object {
    $file = $_
    $text = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
    [regex]::Matches($text, '\]\(([^)\s]+)\)') | ForEach-Object {
      $target = $_.Groups[1].Value
      if ($target -match '^(https?:|mailto:|#)') { return }
      $path = ($target -split '#')[0]
      if (-not $path) { return }
      $resolved = Join-Path $file.DirectoryName $path
      if (-not (Test-Path -LiteralPath $resolved)) {
        "MISSING  $($file.FullName)  ->  $target"
      }
    }
  }
```

## 4. 代码与测试

- [ ] 字节编译通过：

```powershell
python -m compileall -q -x "data|logs|artifacts|backups|dist|bundle" .
```

- [ ] 离线测试结果与 CI 一致（在包含 `comfy_mcp` 目录的父目录执行）：

```powershell
python -m pytest comfy_mcp/tests -q
```

- [ ] 逐条核对测试结果：与默认值不一致的历史断言是否仍然失败；若已被修复，删除 CI 中对应的 `-k` 排除项并更新 `CHANGELOG.md` 的 Known limitations。
- [ ] 需要 GPU、模型或在线 ComfyUI 的用例处于跳过状态，未在发布环境被强制执行。
- [ ] CI 工作流在 `windows-latest` + Python 3.12 上通过（字节编译、跟踪文件守卫、离线测试子集）。

历史断言（发布前复核清单）：

```
test_client_safety.py::GuardPreflightTest::test_below_min_ram_blocks
test_client_safety.py::GuardPreflightTest::test_big_job_blocks
test_client_safety.py::GuardPreflightTest::test_low_vram_blocks
test_client_safety.py::GuardPreflightTest::test_trip_ram_free_blocks
test_workflow.py::TemplateFileTests::test_templates_validate_clean
test_workflow.py::TemplateFileTests::test_three_template_files_load
test_workflow.py::ResolveTests::test_profiles
```

## 5. 敏感信息扫描

- [ ] 用发布负责人本地保存的敏感标记清单（令牌片段、IP 段、域名片段）执行扫描，结果为空。**不要把标记原文写入仓库或本文档。**
- [ ] 通用模式扫描（IP、可疑域名后缀、常见令牌前缀）结果经人工确认后为空或均为占位/文档值：

```powershell
git grep -n -I -E "([0-9]{1,3}\.){3}[0-9]{1,3}|(ghp|gho|ghs|xox[abp]|sk)_[A-Za-z0-9]{16,}|https?://[A-Za-z0-9.-]+\.(xyz|top|club|site|shop)"
```

- [ ] 人工确认 `tests/fixtures/` 中只有节点 schema 快照，无请求日志或凭据。
- [ ] `data/ip_allowlist.json`、`data/bridge_token.txt`、`data/tunnel.json`、`data/tunnel_url.txt` 未被跟踪。

## 6. 可选插件

- [ ] `optional/astrbot_plugin_comfy_bridge/` 仅包含 `main.py`、`metadata.yaml`、`_conf_schema.json`、`requirements.txt`、`README.md`；无 `data/`、`__pycache__/`、令牌或用户配置。
- [ ] 复制来源与仓库副本一致（`README.md` 除外，其内容已按占位符重写）：

```powershell
$src = "K:\path\to\astrbot_plugin_comfy_bridge"
$dst = "optional\astrbot_plugin_comfy_bridge"
foreach ($f in "main.py","metadata.yaml","_conf_schema.json","requirements.txt") {
  $a = (Get-FileHash (Join-Path $src $f) -Algorithm SHA256).Hash
  $b = (Get-FileHash (Join-Path $dst $f) -Algorithm SHA256).Hash
  "{0}  match={1}" -f $f, ($a -eq $b)
}
```

- [ ] 插件文档中的地址均为占位符，且明确说明"默认不安装"。
- [ ] `optional/README.md` 的安装步骤与 `_conf_schema.json` 的配置项名称一致。

## 7. 运行烟测（人工，需本机环境）

- [ ] `python -m setup.doctor` 无阻断项。
- [ ] `start_all.bat -DryRun` 打印的 ComfyUI 路径与解释器符合预期。
- [ ] 启动后 `GET /api/status` 与 `GET /api/doctor` 返回正常，`/studio` 可打开。
- [ ] 提交一个最小任务（如低分辨率单图或 `draft` 档短视频）可完成，任务记录写入 `data/jobs.json`。
- [ ] 资源守卫在内存不足时能中断任务并记录原因（可在测试机上临时下调阈值验证）。
- [ ] 未安装插件时，主流程不受影响。

## 8. 发布动作

- [ ] `CHANGELOG.md` 顶部版本号与 `pyproject.toml` 的 `version` 一致。
- [ ] 生成发布说明时只引用已实现能力，并附 Known limitations 摘要。
- [ ] 打标签与推送由发布负责人显式执行；构建流程不自动提交。

```powershell
git status --short
git tag -a v1.0.0 -m "Aster Studio 1.0.0"
# git push origin v1.0.0
```
