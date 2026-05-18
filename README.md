# NIM Tool GUI

NVIDIA NIM 多 Key 代理 + free-claude-code 的图形化管理工具。

## 依赖

```bash
pip install customtkinter
```

Python ≥ 3.10，仅支持 Windows。

## 启动

```bash
python nim_tool_gui.py
```

## 文件结构

```
工作目录/
├── nim_tool_gui.py          ← 本工具
├── nim_key_proxy.py         ← 多 Key 轮换代理（端口 8083，需自行准备）
├── nim_keys.json            ← Key 存储，首次添加后自动创建
└── backups/                 ← 配置备份，自动创建
    ├── settings.json.mimo-backup
    └── openclaw.json.openclaw-backup
```

---

## 功能说明

### 📊 状态总览

打开即显示，每 12 秒自动刷新，也可手动点「刷新」。

**服务状态**

| 指示灯 | 含义 |
|--------|------|
| 🟢 绿色 | 服务运行且 HTTP 健康检查通过 |
| 🟡 黄色 | 端口被占用但非本工具的服务，显示占用进程名和 PID |
| 🔴 红色 | 服务未运行 |

- `fcc-server :8082` — 通过 `/v1/models` 验证
- `nim-proxy  :8083` — 通过 `/health` 验证

**Claude 配置**

读取 `~/.claude/settings.json`，显示：
- 模式：`NIM 模式`（黄色）或 `mimo 模式`（蓝色）
- 当前 `ANTHROPIC_BASE_URL`

**OpenClaw 配置**

读取 `~/.openclaw/openclaw.json`，显示：
- 模式：`代理模式`（黄色，baseUrl 指向 127.0.0.1）或 `直连 NIM（默认）`（蓝色）
- 当前 `nvidia baseUrl`
- 文件不存在时显示「未检测到」

**API Keys**

显示已配置 Key 的数量及末四位预览（最多显示 5 个）。

**连通性**

- `NIM API 延迟` — 用第一个 Key 请求 `https://integrate.api.nvidia.com/v1/models`，显示延迟或错误

---

### 🔑 密钥管理

**添加 Key**

在输入框粘贴 `nvapi-` 开头的 Key，按回车或点「添加」。Key 已存在时提示重复。

**Key 列表**

每行显示序号、掩码后的 Key（保留末四位）、操作按钮：

- `测试` — 单独测试该 Key 的连通性，显示延迟或错误原因
- `删除` — 二次确认后删除

**列表顶部按钮**

- `↻ 刷新缓存` — 调用 `http://127.0.0.1:8083/keys/reload`，热加载 Key 到代理，无需重启
- `✓ 全部测试` — 依次测试所有 Key，完成后弹窗显示结果

---

### ⚙️ 服务控制

**代理服务**

| 按钮 | 说明 |
|------|------|
| ▶ 启动全部 | 启动 nim-proxy（等待最多 5s，轮询 `/health`）和 fcc-server（等待最多 8s，轮询 `/v1/models`）|
| ■ 停止全部 | 终止由本工具启动的进程，并强制释放 8082 / 8083 端口 |

启动结果以弹窗显示；失败时显示错误信息（含进程 stderr 末尾 5 行）。

**Claude 配置**

修改 `~/.claude/settings.json`：

| 按钮 | 说明 |
|------|------|
| 切换到 NIM 模式 | 备份原配置（若备份已存在则保留），将 `ANTHROPIC_BASE_URL` 指向 `http://127.0.0.1:8082` |
| 恢复 mimo 配置 | 从备份还原，备份不存在时提示失败 |

切换写入的字段：

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8082",
    "ANTHROPIC_AUTH_TOKEN": "freecc",
    "ANTHROPIC_MODEL": "",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": ""
  }
}
```

**OpenClaw 配置**

修改 `~/.openclaw/openclaw.json` 中的 nvidia provider：

| 按钮 | 说明 |
|------|------|
| 应用 OpenClaw 代理 | 备份原配置，将 `baseUrl` 改为 `http://127.0.0.1:8083/v1`，`apiKey` 改为 `dummy` |
| 恢复 OpenClaw | 从备份还原 |

**启动 Claude Code**

输入项目目录（留空使用当前目录），点「启动」后：
1. 备份 settings.json（已有备份则保留）
2. 切换到 NIM 模式
3. 在指定目录运行 `fcc-claude`

---

## 代理 API

| 端点 | 说明 |
|------|------|
| `GET http://127.0.0.1:8083/health` | 返回 `{"keys_loaded": N, "current_key": "...xxxx"}` |
| `GET http://127.0.0.1:8083/keys/reload` | 热加载 nim_keys.json，返回 `{"reloaded": N}` |
| `GET http://127.0.0.1:8082/v1/models` | fcc-server 模型列表（HTTP 200 表示正常） |
| `GET http://127.0.0.1:8082/admin` | fcc-server 内置 Admin 面板 |

---

## 常见问题

**启动崩溃，没看到错误**

用命令行运行，可以看到完整 traceback：

```bash
python nim_tool_gui.py
```

程序也会自动弹出错误弹窗显示 traceback。

**提示「无法导入 customtkinter」**

```bash
pip install customtkinter
```

**nim-proxy 启动失败**

查看同目录下的 `proxy_err.txt`，错误信息写入该文件。常见原因：`nim_key_proxy.py` 不在同一目录。

**fcc-server 启动失败**

查看 `fcc_err.txt`。常见原因：
- `fcc-server` 不在 PATH，检查 `~/.local/bin` 或 uv 安装目录
- 首次启动需下载 tiktoken 编码文件，需要代理：设置 `HTTPS_PROXY=http://127.0.0.1:xxxxx`

**端口被占用**

状态栏会显示占用进程名和 PID（通过 PowerShell `Get-NetTCPConnection` 查询）。用「停止全部」或手动在任务管理器结束该进程。

**NIM 返回 429**

单分钟 RPM 限额用尽，代理自动轮换下一个 Key。在「密钥管理」添加更多 Key 提升并发上限。
