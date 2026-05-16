"""NIM API Key 管理工具。

命令行用法:
  python nim_manager.py list              # 列出所有 Key
  python nim_manager.py add nvapi-xxx     # 添加 Key
  python nim_manager.py remove 2          # 删除第 2 个 Key
  python nim_manager.py remove nvapi-xxx  # 按值删除 Key
  python nim_manager.py test              # 测试所有 Key 可用性

双击启动进入交互式菜单。
"""

import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Windows 控制台 UTF-8 支持
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

KEYS_FILE = Path(__file__).parent / "nim_keys.json"


def load_keys() -> list[str]:
    if KEYS_FILE.exists():
        return json.loads(KEYS_FILE.read_text(encoding="utf-8"))
    return []


def save_keys(keys: list[str]) -> None:
    KEYS_FILE.write_text(json.dumps(keys, indent=2), encoding="utf-8")


def show_keys() -> None:
    keys = load_keys()
    if not keys:
        print("\n  （空，还没有添加任何 Key）")
        return
    print(f"\n  共 {len(keys)} 个 API Key：")
    for i, key in enumerate(keys, 1):
        masked = f"{'*' * (len(key) - 8)}{key[-4:]}"
        print(f"    [{i}] {masked}")


def cmd_add(key: str) -> None:
    if not key.startswith("nvapi-"):
        print("  错误：Key 格式不对，应以 nvapi- 开头")
        return

    keys = load_keys()
    if key in keys:
        print(f"  Key 已存在（第 {keys.index(key) + 1} 个），跳过")
        return

    keys.append(key)
    save_keys(keys)
    print(f"  已添加 Key #{len(keys)}（后4位: ...{key[-4:]}）")


def cmd_remove(target: str) -> None:
    keys = load_keys()
    if not keys:
        print("  没有可删除的 Key")
        return

    if target.isdigit():
        idx = int(target) - 1
        if 0 <= idx < len(keys):
            removed = keys.pop(idx)
            save_keys(keys)
            print(f"  已删除 Key #{idx + 1}（后4位: ...{removed[-4:]}）")
        else:
            print(f"  编号 {target} 超出范围（1-{len(keys)}）")
        return

    if target in keys:
        keys.remove(target)
        save_keys(keys)
        print(f"  已删除 Key（后4位: ...{target[-4:]}）")
    else:
        print("  未找到该 Key")


def cmd_test() -> None:
    keys = load_keys()
    if not keys:
        print("  没有配置任何 API Key")
        return

    print(f"\n  测试 {len(keys)} 个 Key ...")
    for i, key in enumerate(keys, 1):
        masked = f"...{key[-4:]}"
        try:
            req = Request(
                "https://integrate.api.nvidia.com/v1/models",
                method="GET",
            )
            req.add_header("Authorization", f"Bearer {key}")
            with urlopen(req, timeout=10) as resp:
                print(f"    [{i}] {masked} [OK]")
        except HTTPError as e:
            print(f"    [{i}] {masked} [FAIL] HTTP {e.code}")
        except Exception as e:
            print(f"    [{i}] {masked} [FAIL] {e}")


def cmd_reload_proxy() -> None:
    try:
        with urlopen("http://127.0.0.1:8083/keys/reload", timeout=5) as resp:
            data = json.loads(resp.read())
            print(f"  代理已重新加载 {data['reloaded']} 个 Key")
    except Exception as e:
        print(f"  代理未运行或无法连接: {e}")


def interactive_menu() -> None:
    while True:
        print("\n" + "=" * 40)
        print("  NIM Key 管理工具")
        print("=" * 40)

        show_keys()

        print("\n  操作：")
        print("    [1] 添加 Key")
        print("    [2] 删除 Key")
        print("    [3] 测试 Key")
        print("    [4] 重新加载代理")
        print("    [0] 退出")
        print()

        choice = input("  请选择 > ").strip()

        if choice == "1":
            key = input("  输入 Key (nvapi-xxx) > ").strip()
            if key:
                cmd_add(key)
        elif choice == "2":
            keys = load_keys()
            if not keys:
                print("  没有可删除的 Key")
                continue
            target = input("  输入编号或 Key > ").strip()
            if target:
                cmd_remove(target)
        elif choice == "3":
            cmd_test()
        elif choice == "4":
            cmd_reload_proxy()
        elif choice == "0":
            print("  再见")
            break
        else:
            print("  无效选择")


def main() -> None:
    if len(sys.argv) < 2:
        interactive_menu()
        return

    cmd = sys.argv[1]

    if cmd == "list":
        show_keys()
    elif cmd == "add":
        if len(sys.argv) < 3:
            print("用法: python nim_manager.py add nvapi-xxx")
            sys.exit(1)
        cmd_add(sys.argv[2])
    elif cmd == "remove":
        if len(sys.argv) < 3:
            print("用法: python nim_manager.py remove <编号或Key>")
            sys.exit(1)
        cmd_remove(sys.argv[2])
    elif cmd == "test":
        cmd_test()
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
