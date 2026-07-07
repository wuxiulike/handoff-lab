# Handoff Lab

面向 AI 编程代理的本地“规划者 - 执行者”委托桥接工具。

Handoff Lab 让一个代理负责规划、拆任务和验收，另一个独立执行者负责改代码、运行命令、测试并提交精简证据。默认本地流程支持 Codex 风格的规划/QA 代理和 Reasonix 风格的实现 worker，但本项目不是 OpenAI、DeepSeek 或 Reasonix 的官方项目。

商标和第三方关系说明见 [NOTICE.md](NOTICE.md)。

## 功能

- 唯一产品入口是 `/qa-viewer`。
- 访问 `/` 会自动跳转到 `/qa-viewer`。
- 运行态文件保存在 `.agent/` 和 `.reasonix/`，默认不会进入版本控制。
- 支持本地授权模式：`ask`、`allow`、`deny`、`yolo`。
- 提供 Codex skill，让其他 Codex 对话能把结构化任务包提交到本地桥接服务，而不是创建普通 Codex 子代理来假装实现。
- 页面只展示精简过程和证据路径，完整日志与产物留在工作目录里。
- 如果 worker 连续 3 次没有解决同一个 Codex 验收问题，Codex 会临时兜底实现这一个 task；兜底完成后，如果还需要继续修复，流程会切回正常 worker 路径。

## 环境要求

- Python 3.11+。
- Windows、macOS 或 Linux。
- 如果使用 Codex 做规划/验收，需要本机安装并登录 `codex` CLI。
- 如果使用 Reasonix 做实现 worker，需要本机安装 `reasonix` CLI。
- 如果使用 DeepSeek 兼容 API 或视觉模型，需要自行配置对应 API。

安装 Python 依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

macOS/Linux 可使用：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

## 配置

参考 [.env.example](.env.example) 设置环境变量。不要提交真实密钥。

常用变量：

- `HANDOFF_LAB_HOST`：绑定地址，默认 `127.0.0.1`。
- `HANDOFF_LAB_PORT`：端口，默认 `51514`。
- `CODEX_CLI`：Codex CLI 路径，默认 `codex`。
- `REASONIX_CLI`：worker CLI 路径，默认 `reasonix`。
- `OPENAI_PROFILE`、`OPENAI_MODEL`、`OPENAI_REASONING`：可选 Codex 控制项。Codex 默认使用本机客户端登录态，不要求配置 OpenAI API Key。
- `DEEPSEEK_API_KEY`：DeepSeek 兼容 API Key。
- `DEEPSEEK_BASE_URL`：DeepSeek 兼容 API 地址，默认 `https://api.deepseek.com`。
- `REASONIX_MODEL`：worker 模型名，默认 `deepseek-v4-pro`。
- `VISION_PROVIDER`、`VISION_BASE_URL`、`VISION_MODEL`、`VISION_API_KEY`：可选视觉 QA 模型配置。

配置优先级建议：

```text
环境变量 > Web/本地保存的 .agent/model_config.json > 默认值
```

[config.example.json](config.example.json) 只作为配置结构参考，不应保存真实密钥。

## 启动

```powershell
python server.py
```

打开：

[http://127.0.0.1:51514/qa-viewer](http://127.0.0.1:51514/qa-viewer)

访问 [http://127.0.0.1:51514/](http://127.0.0.1:51514/) 会跳转到同一个页面。

Windows 也可以双击：

```text
start_51514_qa_viewer.bat
```

macOS/Linux：

```bash
sh ./start_handoff_lab.sh
```

修改端口：

```powershell
$env:HANDOFF_LAB_PORT = "51515"
python server.py
```

macOS/Linux：

```bash
HANDOFF_LAB_PORT=51515 python server.py
```

## 安装 Skill

仓库内置中性命名的 skill：

```text
skills/handoff-lab-delegation
```

复制到 Codex skills 目录：

```powershell
Copy-Item -Recurse -Force .\skills\handoff-lab-delegation $env:USERPROFILE\.codex\skills\
```

然后在其他 Codex 对话中使用：

```text
Use $handoff-lab-delegation for this task.
```

这个 skill 必须走真实的 Handoff Lab / worker transport。它不应该用普通 Codex 子代理替代实现 worker。

## 快速测试

运行核心 smoke tests：

```powershell
python -m pytest tests/test_start_direct_reasonix.py tests/test_qa_workspace_watch.py tests/test_qa_viewer_page.py -q
```

运行完整 Python 测试：

```powershell
python -m pytest -q
```

## 开源发布前检查

发布前建议先看 [docs/open-source-release.md](docs/open-source-release.md)。

最低要求：

- `.agent/`、`.reasonix/`、日志、密钥、生成产物不能进入版本控制。
- 不提交 `.env`、auth、token、API Key、模型配置等本机文件。
- 搜索并清理私有绝对路径。
- 在干净 clone 中验证 `python server.py` 能启动。
- 验证 `/qa-viewer` 能打开并接收事件。
- 验证 `skills/handoff-lab-delegation` 能复制到 Codex skills 目录并被识别。

## 安全说明

Handoff Lab 是本地开发工具，不应暴露到不可信网络。默认绑定 `127.0.0.1`。

`yolo` 模式会允许 worker 更自由地执行本地命令，只适合可信工作目录。使用前请确认目标目录没有生产凭据、敏感文件或不可恢复数据。
