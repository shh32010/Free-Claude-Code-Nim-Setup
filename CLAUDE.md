# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 架构

```
Claude Code → fcc-server (8082) → 多Key代理 (8083) → NVIDIA NIM API
OpenClaw    ─────────────────────↗       ↓
                                round-robin 轮换 Key
                                429 时自动切换下一个
                                日志显示 Key 编号 + 后4位
```

请求链路：Claude Code 发请求到 fcc-server（free-claude-code 代理），fcc-server 转发到多 Key 代理。OpenClaw 直连多 Key 代理。代理从 `nim_keys.json` 轮选 Key 请求 NVIDIA NIM API。收到 429 时自动切换下一个 Key 重试。

## 文件总览

| 文件 | 用途 |
|------|------|
| `nim_tool_gui.py` | **GUI 管理工具**（customtkinter）：状态监控 + Key 管理 + 服务控制 + 配置切换，**内置代理服务**（无需单独的 proxy 脚本） |
| `nim_keys.json` | Key 存储（JSON 数组，每个 Key 以 `nvapi-` 开头） |
| `free-claude-code/` | 上游项目源码（已含 `base_url_attr` 补丁） |
| `~/.openclaw/openclaw.json` | OpenClaw 配置（启动脚本自动切换 nvidia provider 到代理） |
| `backups/` | 备份目录：mimo 配置备份 + OpenClaw 配置备份 |
| `proxy_out.txt` / `proxy_err.txt` | 代理运行日志 |

## 常用命令

### nim_tool_gui.py（GUI 管理工具）

```bash
# 依赖
pip install customtkinter

# 启动 GUI
python nim_tool_gui.py
```

GUI 提供以下功能：
- **状态总览** — 服务状态（8082/8083）、Claude 配置模式、OpenClaw 配置、Key 数量、NIM API 延迟，每 12 秒自动刷新
- **密钥管理** — 添加/删除/测试 Key，热加载缓存
- **服务控制** — 一键启动/停止代理和 fcc-server，切换 NIM/mimo 配置，切换 OpenClaw 配置
- **启动 Claude Code** — 输入项目目录，自动备份配置 → 切换 NIM 模式 → 启动 fcc-claude

### 代理 API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `http://127.0.0.1:8083/health` | GET | 返回 `{"keys_loaded": N, "current_key": "...xxxx"}` |
| `http://127.0.0.1:8083/keys/reload` | GET | 热加载 `nim_keys.json`，返回 `{"reloaded": N}` |
| `http://127.0.0.1:8082/v1/models` | GET | fcc-server 模型列表（HTTP 200 表示正常） |
| `http://127.0.0.1:8082/admin` | GET | Admin UI，修改模型配置、验证 Key、重启服务 |

### free-claude-code 开发（在 `free-claude-code/` 子目录内）

```bash
uv run ruff format        # 格式化
uv run ruff check         # lint
uv run ty check           # 类型检查
uv run pytest             # 测试
```

## 日常使用

### 启动 NIM 模式

运行 `python nim_tool_gui.py` 打开 GUI，操作步骤：
1. 点「▶ 启动全部」启动代理和 fcc-server
2. 点「启动 Claude Code」输入项目目录，自动切换配置并启动
3. 如需 OpenClaw 代理，点「应用 OpenClaw 代理」

### 切换回 mimo 模式

在 GUI 中点「恢复 mimo 配置」和「恢复 OpenClaw」。

## 配置

### .env 关键配置 (`~/.config/free-claude-code/.env`)

```env
# 指向多 Key 代理（而非直连 NIM）
NVIDIA_NIM_API_KEY=dummy
NVIDIA_NIM_BASE_URL=http://127.0.0.1:8083/v1

# 模型路由（全部走 kimi-k2.6）
MODEL=nvidia_nim/moonshotai/kimi-k2.6
MODEL_OPUS=nvidia_nim/moonshotai/kimi-k2.6
MODEL_SONNET=nvidia_nim/moonshotai/kimi-k2.6
MODEL_HAIKU=nvidia_nim/moonshotai/kimi-k2.6

ENABLE_MODEL_THINKING=true
PORT=8082
ANTHROPIC_AUTH_TOKEN=freecc
```

### 模型路由（已验证可用）

| NIM 模型 | 说明 |
|----------|------|
| `nvidia_nim/moonshotai/kimi-k2.6` | 默认，质量最高但较慢（冷启动 60-120s） |
| `nvidia_nim/moonshotai/kimi-k2.5` | 轻量版，速度更快 |
| `nvidia_nim/nvidia/nemotron-3-super-49b-v1` | 478 tokens/s，目前最快 |

> **已下线：** `z-ai/glm4.7` 于 2026-05-14 下线（HTTP 410）。`z-ai/glm-5.1` 响应极慢。

### mimo 模式配置

> mimo 使用 Anthropic 兼容端点（注意是 `/anthropic` 不是 `/v1`）

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://token-plan-cn.xiaomimimo.com/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "<your-tp-key>",
    "ANTHROPIC_MODEL": "mimo-v2.5-pro",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "mimo-v2.5-pro",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "mimo-v2.5-pro",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "mimo-v2.5-pro"
  }
}
```

> mimo 不支持 Thinking 模式，使用时按 Tab 确认显示 `Thinking off`。

### OpenClaw 集成

OpenClaw 也通过多 Key 代理使用 NIM API。GUI 中点「应用 OpenClaw 代理」切换到代理模式，点「恢复 OpenClaw」恢复直连。

**备份文件：** `E:\Claude Code\backups\` 目录下存放原始配置，恢复时从此读取。

## 源码修改记录

free-claude-code 原生不支持 `NVIDIA_NIM_BASE_URL` 配置，修改了两处：

1. `free-claude-code/config/provider_catalog.py` — NIM provider 添加 `base_url_attr="nvidia_nim_base_url"`
2. `free-claude-code/config/settings.py` — 添加 `nvidia_nim_base_url` 字段

> 更新 free-claude-code 后需重新应用这两处修改并 `uv tool install --force "E:/Claude Code/free-claude-code"`。

## free-claude-code 内部结构

```
free-claude-code/
├── api/              # HTTP API 层（FastAPI）
│   ├── app.py        # 应用入口
│   ├── routes.py     # 主路由（/v1/messages, /v1/models）
│   ├── admin_routes.py  # Admin UI 路由
│   ├── model_router.py  # 模型路由逻辑
│   └── services.py   # 业务服务层
├── config/           # 配置
│   ├── provider_catalog.py  # provider 定义（含 base_url_attr 补丁）
│   ├── settings.py   # 设置字段定义（含 nvidia_nim_base_url 补丁）
│   └── nim.py        # NIM 特定配置
├── providers/        # 各 LLM provider 实现
│   ├── nvidia_nim/   # NIM provider
│   ├── kimi/         # Kimi provider
│   ├── deepseek/     # DeepSeek provider
│   └── anthropic_messages.py  # Anthropic 协议转换
├── core/             # 核心逻辑（SSE 流处理、token 计算、rate limit）
├── cli/              # CLI 入口（fcc-server, fcc-claude）
├── messaging/        # 消息平台集成（Discord, Telegram）
├── server.py         # 服务器启动入口
└── pyproject.toml    # 项目配置（uv 管理）
```

## 出错处理

| 症状 | 排查方向 |
|------|----------|
| ConnectionRefused | fcc-server 未运行，检查 8082 端口：`Get-NetTCPConnection -LocalPort 8082` |
| 端口 8082 / 8083 被占用 | `Get-NetTCPConnection -LocalPort <端口>` 查看占用进程 |
| `fcc-server` 启动时 SSL 握手失败 | 首次启动需 `HTTPS_PROXY=http://127.0.0.1:10808`（下载 tiktoken 编码文件），下载后缓存，后续不需要 |
| `fcc-server` 找不到 | 检查 `~/.local/bin` 或 `D:\Tools\uv` 是否在 PATH 中 |
| NIM 返回 410 Gone | 模型已下线，在 .env 中更换其他模型 |
| 所有 Key 都 429 | RPM 限额用尽，等待下一分钟或添加更多 Key |
| 更新 free-claude-code 后 `NVIDIA_NIM_BASE_URL` 不生效 | 需重新应用源码补丁并 `uv tool install --force` |
| 关闭窗口后配置未恢复 | 运行 `python nim_tool_gui.py` 点「恢复 mimo 配置」/「恢复 OpenClaw」 |
| 响应极慢（60s+） | kimi-k2.6 冷启动正常现象；可切换到 kimi-k2.5 提升速度 |
| mimo 报模型不存在 | BASE_URL 必须用 `/anthropic` 结尾，不是 `/v1` |
| PowerShell 中文乱码 | 确认脚本为 UTF-8 BOM 编码 |
