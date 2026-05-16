# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 架构

```
Claude Code → fcc-server (8082) → 多Key代理 (8083) → NVIDIA NIM API
                                        ↓
                                round-robin 轮换 Key
                                429 时自动切换下一个
                                日志显示 Key 编号 + 后4位
```

## 已安装组件

| 组件 | 路径/版本 | 说明 |
|------|----------|------|
| free-claude-code | uv tool install，v2.0.0 | 从本地源码安装（含 `base_url_attr` 补丁） |
| 源码 | `E:\Claude Code\free-claude-code` | 已修改 `config/provider_catalog.py` 和 `config/settings.py` |
| 多 Key 代理 | `E:\Claude Code\nim_key_proxy.py` | round-robin + 429 自动切换 |
| Key 管理工具 | `E:\Claude Code\nim_manager.py` | 交互式菜单：添加/删除/测试/重载 |
| Key 存储 | `E:\Claude Code\nim_keys.json` | 当前 11 个 Key（全部已验证可用） |
| 配置文件 | `~/.config/free-claude-code/.env` | |
| 启动脚本 | `E:\Claude Code\start-fcc.ps1` / `.bat` | 一键启动，支持拖拽文件夹或弹窗选择 |
| 状态面板 | `E:\Claude Code\fcc-status.py` | 查看服务状态，支持关闭服务、恢复 mimo |
| Key 来源 | `E:\Claude Code\英伟达api.xlsx` | Key 来源备份 |

## 脚本文件说明

| 文件 | 用途 |
|------|------|
| `start-fcc.bat` | 双击启动：弹窗选择项目目录；或将文件夹拖拽到图标上直接启动 |
| `start-fcc.ps1` | 启动主逻辑：自动检测异常退出并恢复 mimo，try-finally 保障退出时恢复 |
| `fcc-status.py` | 状态面板：显示服务/模式/Key/连通性，数字键交互（1=恢复 mimo，2=刷新，3=关闭服务，0=退出） |

## 常用命令

### 服务管理
| 命令 | 用途 |
|------|------|
| `python nim_key_proxy.py` | 启动多 Key 轮换代理（端口 8083） |
| `python nim_key_proxy.py --port 9000` | 指定端口启动代理 |
| `fcc-server` | 启动 free-claude-code 代理（端口 8082） |
| `fcc-claude` | 启动 Claude Code 并自动连接代理 |
| `python fcc-status.py` | 打开状态面板 |

### Key 管理 (`nim_manager.py`)
| 命令 | 用途 |
|------|------|
| `python nim_manager.py` | 交互式菜单（双击也可） |
| `python nim_manager.py list` | 列出所有 Key |
| `python nim_manager.py add nvapi-xxx` | 添加 Key |
| `python nim_manager.py remove <编号或Key>` | 删除 Key |
| `python nim_manager.py test` | 测试所有 Key 可用性 |

### free-claude-code 开发（在 `free-claude-code/` 子目录内）
```bash
uv run ruff format        # 格式化
uv run ruff check         # lint
uv run ty check           # 类型检查
uv run pytest             # 测试
```

## 源码修改记录

修改了两处以支持 `NVIDIA_NIM_BASE_URL` 配置（原生不支持）：

1. `config/provider_catalog.py` — NIM provider 添加 `base_url_attr="nvidia_nim_base_url"`
2. `config/settings.py` — 添加 `nvidia_nim_base_url` 字段

> 更新 free-claude-code 后需重新应用这两处修改并 `uv tool install --force "E:/Claude Code/free-claude-code"`。

## 日常使用

### 启动 NIM 模式

**方式一（推荐）：** 双击 `start-fcc.bat`，弹窗选择项目目录

**方式二：** 将项目文件夹直接拖拽到 `start-fcc.bat` 图标上

**方式三（手动）：**

```powershell
# 终端 1：多 Key 代理
python "E:\Claude Code\nim_key_proxy.py"

# 终端 2：fcc-server
fcc-server

# 终端 3：Claude Code
fcc-claude
```

### 切换回 mimo 模式

**正常退出：** 在 Claude Code 内按 `Ctrl+C` 或输入 `/exit`，脚本自动恢复 mimo 配置。

**异常退出（直接关闭窗口）：**
- 下次启动 `start-fcc.bat` 时会自动检测并恢复
- 或运行 `python fcc-status.py`，按 1 恢复
- 或手动运行：

```powershell
Copy-Item "$env:USERPROFILE\.claude\settings.json.mimo-backup" "$env:USERPROFILE\.claude\settings.json" -Force
Remove-Item "$env:USERPROFILE\.claude\settings.json.mimo-backup" -Force
```

### Admin UI

打开 `http://127.0.0.1:8082/admin` 可以修改模型配置、验证 Key、重启服务。

## 配置

### .env 关键配置 (`~/.config/free-claude-code/.env`)

```env
# 指向多 Key 代理（而非直连 NIM）
NVIDIA_NIM_API_KEY=dummy
NVIDIA_NIM_BASE_URL=http://127.0.0.1:8083/v1

# 模型路由
MODEL=nvidia_nim/moonshotai/kimi-k2.6
MODEL_OPUS=nvidia_nim/moonshotai/kimi-k2.6
MODEL_SONNET=nvidia_nim/moonshotai/kimi-k2.6
MODEL_HAIKU=nvidia_nim/moonshotai/kimi-k2.6

ENABLE_MODEL_THINKING=true
PORT=8082
ANTHROPIC_AUTH_TOKEN=freecc
```

### 模型路由（已验证可用）

| Claude 模型 | NIM 模型 | 说明 |
|-------------|----------|------|
| Opus / Sonnet / Haiku / Fallback | `nvidia_nim/moonshotai/kimi-k2.6` | 默认，质量最高但较慢 |
| 可切换 | `nvidia_nim/moonshotai/kimi-k2.5` | 轻量版，速度更快 |
| 可切换 | `nvidia_nim/nvidia/nemotron-3-super-49b-v1` | 478 tokens/s，目前最快 |

> **速度说明：** kimi-k2.6 冷启动可能需要 60-120 秒，后续请求会加快。如果响应太慢，切换到 kimi-k2.5 或 nemotron-3-super。

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

> **注意：** mimo 不支持 Thinking 模式，使用时按 Tab 确认显示 `Thinking off`。

## 多 Key 管理

### 轮换机制

- **round-robin：** 按顺序轮流使用每个 Key
- **429 自动切换：** 收到 429 立即切换到下一个 Key 并重试
- **日志：** 显示 `Key #N（后4位: xxxx）`，不泄露完整 Key
- **热加载：** 访问 `GET http://127.0.0.1:8083/keys/reload` 重新加载 Key 列表

### 命令行模式

```powershell
python nim_manager.py list               # 列出
python nim_manager.py add nvapi-新Key    # 添加
python nim_manager.py remove 2           # 按编号删除
python nim_manager.py remove nvapi-xxx   # 按值删除
python nim_manager.py test               # 测试全部 Key
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
| fcc /v1/models 显示 FAIL | 已知问题，不影响正常使用，忽略即可 |
| 关闭窗口后 settings.json 未恢复 | `python fcc-status.py` 按 1 恢复，或下次启动时自动恢复 |
| 响应极慢（60s+） | kimi-k2.6 冷启动正常现象；可切换到 kimi-k2.5 提升速度 |
| mimo 报模型不存在 | BASE_URL 必须用 `/anthropic` 结尾，不是 `/v1` |
| PowerShell 中文乱码 | 确认脚本为 UTF-8 BOM 编码，bat 文件首行有 `chcp 65001` |
