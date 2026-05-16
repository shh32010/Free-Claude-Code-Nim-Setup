"""Claude Code / NIM 状态面板。

用法: python fcc-status.py
"""

import json
import os
import shutil
import socket
import sys
import time
from pathlib import Path

# Windows 控制台 UTF-8 支持
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).parent
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
BACKUP_PATH = Path.home() / ".claude" / "settings.json.mimo-backup"
KEYS_PATH = SCRIPT_DIR / "nim_keys.json"


def test_port(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def kill_port_process(port: int) -> bool:
    """终止占用指定端口的进程。"""
    import subprocess
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             f"(Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue).OwningProcess"],
            capture_output=True, text=True
        )
        pids = set(result.stdout.strip().split())
        pids.discard("")
        if not pids:
            return False
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
        return True
    except Exception:
        return False


def section(title: str) -> None:
    print(f"\n  {title}")
    print(f"  {'─' * 40}")


def row(label: str, value: str, color: str = "0") -> None:
    colors = {"Red": "91", "Green": "92", "Yellow": "93", "Cyan": "96", "White": "0", "DarkGray": "90"}
    code = colors.get(color, "0")
    print(f"  {label:<25} \033[{code}m{value}\033[0m")


def load_json(path: Path) -> dict | list | None:
    """加载 JSON，自动处理 BOM。"""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def load_keys() -> list[str]:
    data = load_json(KEYS_PATH)
    return data if isinstance(data, list) else []


def show_status() -> bool:
    """显示状态，返回是否有 mimo 备份。"""
    os.system("cls" if os.name == "nt" else "clear")
    print("\n  \033[96m============================================\033[0m")
    print("  \033[96m  Claude Code / NIM  状态面板\033[0m")
    print("  \033[96m============================================\033[0m")

    # 服务状态
    section("服务状态")
    fcc = test_port(8082)
    proxy = test_port(8083)
    row("fcc-server  :8082", "运行中" if fcc else "已停止", "Green" if fcc else "Red")
    row("key-proxy   :8083", "运行中" if proxy else "已停止", "Green" if proxy else "Red")

    # 当前模式
    section("当前模式")
    mimo_backup = BACKUP_PATH.exists()
    if mimo_backup:
        row("模式", "NIM 模式  (mimo 已备份)", "Yellow")
    else:
        row("模式", "mimo 模式  (默认)", "Cyan")

    settings = load_json(SETTINGS_PATH)
    if settings and isinstance(settings, dict):
        env = settings.get("env", {})
        url = env.get("ANTHROPIC_BASE_URL", "(未设置)")
        mdl = env.get("ANTHROPIC_MODEL", "(未设置)")
        row("ANTHROPIC_BASE_URL", url)
        row("ANTHROPIC_MODEL", mdl)
    elif SETTINGS_PATH.exists():
        row("settings.json", "解析失败", "Red")
    else:
        row("settings.json", "文件不存在", "Red")

    # NIM Keys
    section("NIM Keys")
    keys = load_keys()
    if keys:
        row("Key 总数", str(len(keys)))
        for i, key in enumerate(keys, 1):
            row(f"  Key #{i}", f"....{key[-4:]}", "DarkGray")
    else:
        row("nim_keys.json", "文件不存在或为空", "Red")

    # 连通性测试
    section("连通性测试")
    if fcc:
        try:
            import urllib.request
            with urllib.request.urlopen("http://127.0.0.1:8082/v1/models", timeout=3) as r:
                row("fcc /v1/models", f"正常  ({r.status})", "Green")
        except Exception:
            row("fcc /v1/models", "失败", "Red")
    else:
        row("fcc /v1/models", "跳过 (服务未运行)", "DarkGray")

    if proxy:
        try:
            import urllib.request
            with urllib.request.urlopen("http://127.0.0.1:8083/health", timeout=3) as r:
                row("proxy /health", f"正常  ({r.status})", "Green")
        except Exception:
            row("proxy /health", "失败", "Red")
    else:
        row("proxy /health", "跳过 (服务未运行)", "DarkGray")

    # 操作菜单
    print(f"\n  \033[96m{'=' * 44}\033[0m")
    print("  操作：")
    if mimo_backup:
        print("    \033[93m[1] 恢复 mimo 配置\033[0m")
    print("    \033[90m[2] 刷新状态\033[0m")
    print("    \033[91m[3] 关闭所有服务\033[0m")
    print("    \033[90m[0] 退出\033[0m")
    print(f"  \033[96m{'=' * 44}\033[0m")

    return mimo_backup


def main() -> None:
    import msvcrt

    while True:
        mimo_backup = show_status()
        print("\n  请选择 > ", end="", flush=True)

        ch = msvcrt.getwch()

        if ch == "0":
            print()
            break
        elif ch == "1" and mimo_backup:
            shutil.copy2(BACKUP_PATH, SETTINGS_PATH)
            BACKUP_PATH.unlink()
            print("\n  \033[92m已恢复 mimo 配置\033[0m")
            time.sleep(1)
        elif ch == "3":
            print("\n  正在关闭服务...")
            killed_proxy = kill_port_process(8083)
            killed_fcc = kill_port_process(8082)
            if killed_fcc or killed_proxy:
                print("  \033[92m服务已关闭\033[0m")
            else:
                print("  \033[90m没有运行中的服务\033[0m")
            time.sleep(1)
        # 其他按键刷新


if __name__ == "__main__":
    main()
