# free-claude-code-nim-setup

NVIDIA NIM 多 Key 轮换代理 + free-claude-code 部署配置，用于通过 NIM 免费使用 Claude Code。

## 架构

```
Claude Code → fcc-server (8082) → 多Key代理 (8083) → NVIDIA NIM API
                                        ↓
                                round-robin 轮换 Key
                                429 时自动切换下一个
```

## 快速开始

### 1. 安装 free-claude-code

```bash
uv tool install --force git+https://github.com/Alishahryar1/free-claude-code.git
```

### 2. 添加 NIM API Key

```bash
python nim_manager.py add nvapi-你的Key
```

或双击 `nim_manager.py` 进入交互式菜单。

### 3. 启动

双击 `start-fcc.bat`，弹窗选择项目目录即可。

## 文件说明

| 文件 | 用途 |
|------|------|
| `start-fcc.bat` | 一键启动：自动启动代理、切换 NIM 模式、打开 Claude Code |
| `start-fcc.ps1` | 启动主逻辑（异常退出自动恢复 mimo 配置） |
| `fcc-status.py` | 状态面板：查看服务状态、连通性测试、关闭服务 |
| `nim_key_proxy.py` | 多 Key 轮换代理（round-robin + 429 自动切换） |
| `nim_manager.py` | Key 管理工具（添加/删除/测试/重载） |
| `nim_keys.json` | Key 存储 |
| `英伟达api.xlsx` | Key 来源备份 |

## 常用命令

```bash
# 服务管理
python nim_key_proxy.py              # 启动多 Key 代理 (8083)
fcc-server                           # 启动 fcc 代理 (8082)
fcc-claude                           # 启动 Claude Code

# Key 管理
python nim_manager.py list           # 列出所有 Key
python nim_manager.py add nvapi-xxx  # 添加 Key
python nim_manager.py remove 2       # 删除第 2 个 Key
python nim_manager.py test           # 测试所有 Key

# 状态面板
python fcc-status.py                 # 查看状态、关闭服务、恢复 mimo
```

## 模型

| 模型 | 说明 |
|------|------|
| `nvidia_nim/moonshotai/kimi-k2.6` | 默认，质量最高（冷启动 60-120s） |
| `nvidia_nim/moonshotai/kimi-k2.5` | 轻量版，速度更快 |
| `nvidia_nim/nvidia/nemotron-3-super-49b-v1` | 478 tokens/s，最快 |

在 `~/.config/free-claude-code/.env` 中修改 `MODEL` 切换。

## 切换回 mimo

正常退出（`Ctrl+C` 或 `/exit`）会自动恢复 mimo 配置。异常退出后：

- 下次启动 `start-fcc.bat` 自动恢复
- 或运行 `python fcc-status.py` 按 1 恢复

## License

MIT
