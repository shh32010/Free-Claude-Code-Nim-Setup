"""
NIM / FCC 管理工具 - GUI 版（含内置代理）
风格：简洁扁平暗色

依赖安装:
  pip install customtkinter

运行:
  python nim_tool_gui.py

无需 nim_key_proxy.py，代理服务已内置。
"""

import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import (
    Request, ProxyHandler, build_opener, install_opener, urlopen
)

import tkinter
from tkinter import messagebox

try:
    import customtkinter as ctk
except Exception as _e:
    tkinter.Tk().withdraw()
    messagebox.showerror(
        "启动失败 - 缺少依赖",
        f"无法导入 customtkinter：\n{_e}\n\n请运行：\n  pip install customtkinter",
    )
    sys.exit(1)

# ── DPI 感知（Windows 高分屏不模糊）──
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# ── 主题 ──
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── 路径常量（打包后仍正确定位）──
if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).parent
else:
    SCRIPT_DIR = Path(__file__).parent

SETTINGS_PATH        = Path.home() / ".claude" / "settings.json"
BACKUP_PATH          = SCRIPT_DIR / "backups" / "settings.json.mimo-backup"
KEYS_PATH            = SCRIPT_DIR / "nim_keys.json"
OPENCLAW_PATH        = Path.home() / ".openclaw" / "openclaw.json"
OPENCLAW_BACKUP_PATH = SCRIPT_DIR / "backups" / "openclaw.json.openclaw-backup"
PROXY_PORT = 8083
FCC_PORT   = 8082

_spawned_procs: list[subprocess.Popen] = []
_proxy_server: HTTPServer | None = None   # 内置代理服务器实例

NIM_BASE = "https://integrate.api.nvidia.com"


class KeyRotator:
    """round-robin Key 轮换器，线程安全。"""
    def __init__(self) -> None:
        self._keys: list[str] = []
        self._index = 0
        self._lock = threading.Lock()
        self.reload()

    def reload(self) -> None:
        keys = load_keys_raw()
        with self._lock:
            self._keys = keys
            self._index = 0

    @property
    def count(self) -> int:
        return len(self._keys)

    def current(self) -> str | None:
        with self._lock:
            if not self._keys:
                return None
            return self._keys[self._index % len(self._keys)]

    def next(self) -> str | None:
        with self._lock:
            if not self._keys:
                return None
            self._index = (self._index + 1) % len(self._keys)
            return self._keys[self._index]

    def key_info(self, key: str) -> str:
        with self._lock:
            for i, k in enumerate(self._keys):
                if k == key:
                    return f"#{i + 1}"
        return "??"


class NimProxyHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器，将请求转发到 NIM API 并做 Key 轮换。"""

    rotator: "KeyRotator"   # 由 _start_proxy_server 注入

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        target = f"{NIM_BASE}{self.path}"
        max_attempts = max(self.rotator.count, 1)

        for attempt in range(max_attempts):
            key = self.rotator.current()
            if not key:
                self.send_error(503, "No API keys configured")
                return
            req = Request(target, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", f"Bearer {key}")
            try:
                with urlopen(req, timeout=120) as resp:
                    self.send_response(resp.status)
                    for h, v in resp.getheaders():
                        if h.lower() not in ("transfer-encoding", "connection"):
                            self.send_header(h, v)
                    self.end_headers()
                    self.wfile.write(resp.read())
                    return
            except HTTPError as e:
                if e.code == 429:
                    # 限流：换下一个 Key 重试
                    self.rotator.next()
                    continue
                # 其他 HTTP 错误（401/403/410/5xx 等）换 Key 无意义，直接返回
                self.send_response(e.code)
                self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
                self.end_headers()
                self.wfile.write(e.read())
                return
            except Exception as e:
                # 网络层错误（超时、断连、SSL 等）换 Key 无意义，直接返回 502
                self.send_error(502, str(e))
                return
        self.send_error(502, "All keys rate-limited (429)")

    def do_GET(self) -> None:
        if self.path == "/health":
            cur = self.rotator.current()
            info = {
                "keys_loaded": self.rotator.count,
                "current_key": f"...{cur[-4:]}" if cur else None,
            }
            body = json.dumps(info).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/keys/reload":
            self.rotator.reload()
            body = json.dumps({"reloaded": self.rotator.count}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return

        # 其他 GET 转发（/v1/models 等）
        target = f"{NIM_BASE}{self.path}"
        key = self.rotator.current()
        if not key:
            self.send_error(503, "No API keys configured")
            return
        req = Request(target, method="GET")
        req.add_header("Authorization", f"Bearer {key}")
        try:
            with urlopen(req, timeout=30) as resp:
                self.send_response(resp.status)
                for h, v in resp.getheaders():
                    if h.lower() not in ("transfer-encoding", "connection"):
                        self.send_header(h, v)
                self.end_headers()
                self.wfile.write(resp.read())
        except HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.end_headers()
            self.wfile.write(e.read())

    def log_message(self, *args) -> None:
        pass  # 静默 HTTP 日志


def _start_proxy_server() -> None:
    """在后台线程中启动内置代理服务器（仅调用一次）。"""
    global _proxy_server
    rotator = KeyRotator()

    class _Handler(NimProxyHandler):
        pass
    _Handler.rotator = rotator

    _proxy_server = HTTPServer(("127.0.0.1", PROXY_PORT), _Handler)
    _proxy_server.serve_forever()


def _stop_proxy_server() -> None:
    global _proxy_server
    if _proxy_server is not None:
        _proxy_server.shutdown()
        _proxy_server = None

# ─────────────────────────────────────────────────
#  调色板
# ─────────────────────────────────────────────────
C = {
    "bg"          : "#0d1117",
    "sidebar"     : "#161b22",
    "card"        : "#1c2128",
    "border"      : "#30363d",
    "accent"      : "#238636",
    "accent_h"    : "#2ea043",
    "blue"        : "#1f6feb",
    "blue_h"      : "#388bfd",
    "red_bg"      : "#3d1c1c",
    "red"         : "#f85149",
    "green"       : "#3fb950",
    "yellow"      : "#d29922",
    "cyan"        : "#58a6ff",
    "text"        : "#e6edf3",
    "dim"         : "#8b949e",
    "muted"       : "#484f58",
    "danger"      : "#da3633",
    "danger_h"    : "#f85149",
}

FONT       = "Segoe UI"
FONT_MONO  = "Consolas"

# ─────────────────────────────────────────────────
#  业务逻辑层（与原 nim_tool.py 保持一致）
# ─────────────────────────────────────────────────

def _detect_proxy() -> str | None:
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy:
        return proxy
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            )
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if enabled:
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
                winreg.CloseKey(key)
                return f"http://{server}"
            winreg.CloseKey(key)
        except Exception:
            pass
    return None


_proxy_url = _detect_proxy()
if _proxy_url:
    install_opener(
        build_opener(ProxyHandler({"http": _proxy_url, "https": _proxy_url}))
    )


def test_port(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _http_health(url: str, timeout: float = 3) -> dict | None:
    """请求 health 端点，成功返回解析后的 JSON，失败返回 None。"""
    try:
        with urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _get_port_pids(port: int) -> list[str]:
    """获取占用指定端口的 PID 列表。"""
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             f"(Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue).OwningProcess"],
            capture_output=True, text=True,
        )
        pids = [p for p in result.stdout.strip().split() if p]
        return list(dict.fromkeys(pids))   # 去重保序
    except Exception:
        return []


def _get_process_name(pid: str) -> str:
    """获取 PID 对应的进程名。"""
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).ProcessName"],
            capture_output=True, text=True,
        )
        return result.stdout.strip() or "未知"
    except Exception:
        return "未知"


def _describe_port_occupant(port: int) -> str:
    """返回端口占用者的描述字符串。"""
    pids = _get_port_pids(port)
    if not pids:
        return "未知进程"
    parts = []
    for pid in pids[:3]:
        name = _get_process_name(pid)
        parts.append(f"{name}(PID:{pid})")
    return ", ".join(parts)


def kill_port_process(port: int) -> bool:
    pids = _get_port_pids(port)
    if not pids:
        return False
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
    return True


def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return None
    except UnicodeDecodeError:
        return None
    except OSError:
        return None


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_keys_raw() -> list[str]:
    """直接读取 Key 列表，供 KeyRotator 内部使用。"""
    if not KEYS_PATH.exists():
        return []
    try:
        data = json.loads(KEYS_PATH.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def load_keys() -> list[str]:
    data = load_json(KEYS_PATH)
    return data if isinstance(data, list) else []


def save_keys(keys: list[str]) -> None:
    save_json(KEYS_PATH, keys)


def switch_to_nim_mode() -> None:
    if not SETTINGS_PATH.exists():
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text('{"env":{}}', encoding="utf-8")
    data = load_json(SETTINGS_PATH)
    if not isinstance(data, dict):
        data = {}
    env = data.setdefault("env", {})
    env["ANTHROPIC_BASE_URL"]            = "http://127.0.0.1:8082"
    env["ANTHROPIC_AUTH_TOKEN"]          = "freecc"
    env["ANTHROPIC_MODEL"]               = ""
    env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = ""
    env["ANTHROPIC_DEFAULT_SONNET_MODEL"]= ""
    env["ANTHROPIC_DEFAULT_OPUS_MODEL"]  = ""
    save_json(SETTINGS_PATH, data)


def backup_settings() -> bool:
    if not SETTINGS_PATH.exists():
        return False
    if BACKUP_PATH.exists():
        return True
    BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SETTINGS_PATH, BACKUP_PATH)
    return True


def restore_settings() -> bool:
    if not BACKUP_PATH.exists():
        return False
    shutil.copy2(BACKUP_PATH, SETTINGS_PATH)
    return True


def backup_openclaw() -> bool:
    if not OPENCLAW_PATH.exists():
        return False
    if OPENCLAW_BACKUP_PATH.exists():
        return True
    OPENCLAW_BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OPENCLAW_PATH, OPENCLAW_BACKUP_PATH)
    return True


def restore_openclaw() -> bool:
    if not OPENCLAW_BACKUP_PATH.exists():
        return False
    shutil.copy2(OPENCLAW_BACKUP_PATH, OPENCLAW_PATH)
    return True


def switch_openclaw_to_proxy() -> bool:
    data = load_json(OPENCLAW_PATH)
    if not isinstance(data, dict):
        return False
    try:
        nvidia = data["models"]["providers"]["nvidia"]
        nvidia["baseUrl"] = "http://127.0.0.1:8083/v1"
        nvidia["apiKey"]  = "dummy"
        save_json(OPENCLAW_PATH, data)
        return True
    except (KeyError, TypeError):
        return False


def get_openclaw_nvidia() -> dict | None:
    data = load_json(OPENCLAW_PATH)
    if not data or not isinstance(data, dict):
        return None
    try:
        return data["models"]["providers"]["nvidia"]
    except (KeyError, TypeError):
        return None


def start_proxy() -> tuple[str, str]:
    """返回 (状态, 描述)。状态: ok / already / fail / occupied"""
    if test_port(PROXY_PORT):
        if _http_health(f"http://127.0.0.1:{PROXY_PORT}/health") is not None:
            return "already", "nim-proxy 已在运行"
        occupant = _describe_port_occupant(PROXY_PORT)
        return "occupied", f"端口 {PROXY_PORT} 被占用: {occupant}"

    # 在后台线程启动内置代理
    t = threading.Thread(target=_start_proxy_server, daemon=True)
    t.start()

    for _ in range(10):   # 最多等 5s
        time.sleep(0.5)
        if _http_health(f"http://127.0.0.1:{PROXY_PORT}/health") is not None:
            return "ok", "nim-proxy 启动成功"

    return "fail", "代理启动超时(5s)"


def start_fcc_server() -> tuple[str, str]:
    """返回 (状态, 描述)。状态: ok / already / missing / fail / occupied"""
    if test_port(FCC_PORT):
        try:
            with urlopen(f"http://127.0.0.1:{FCC_PORT}/v1/models", timeout=3) as r:
                if r.status == 200:
                    return "already", "fcc-server 已在运行"
        except Exception:
            pass
        occupant = _describe_port_occupant(FCC_PORT)
        return "occupied", f"端口 {FCC_PORT} 被占用: {occupant}"

    err_path = SCRIPT_DIR / "fcc_err.txt"
    try:
        with open(err_path, "w", encoding="utf-8") as stderr_file:
            p = subprocess.Popen(
                ["fcc-server"],
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                stderr=stderr_file,
            )
    except FileNotFoundError:
        return "missing", "fcc-server 未找到，请检查 PATH"

    _spawned_procs.append(p)

    for _ in range(8):           # 最多等 8s
        time.sleep(1)
        if p.poll() is not None:
            err_text = err_path.read_text(encoding="utf-8", errors="replace").strip()
            last = "\n".join(err_text.splitlines()[-5:]) if err_text else "（无输出）"
            return "fail", f"fcc-server 异常退出(code={p.returncode})\n{last}"
        if test_port(FCC_PORT):
            try:
                with urlopen(f"http://127.0.0.1:{FCC_PORT}/v1/models", timeout=3) as r:
                    if r.status == 200:
                        return "ok", "fcc-server 启动成功"
            except Exception:
                pass

    # 超时最后一次确认
    try:
        with urlopen(f"http://127.0.0.1:{FCC_PORT}/v1/models", timeout=3) as r:
            if r.status == 200:
                return "ok", "fcc-server 启动成功"
    except Exception:
        pass
    return "fail", f"fcc-server 启动超时(8s)，详见 {err_path}"


def stop_all_services() -> None:
    # 停止内置代理线程
    _stop_proxy_server()
    # 停止通过 subprocess 启动的进程（fcc-server 等）
    for p in _spawned_procs:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
    _spawned_procs.clear()
    kill_port_process(FCC_PORT)


def reload_proxy() -> int | None:
    try:
        with urlopen("http://127.0.0.1:8083/keys/reload", timeout=5) as resp:
            return json.loads(resp.read()).get("reloaded", 0)
    except Exception:
        return None


def test_key(key: str) -> tuple[bool, str]:
    try:
        req = Request("https://integrate.api.nvidia.com/v1/models", method="GET")
        req.add_header("Authorization", f"Bearer {key}")
        t0 = time.time()
        with urlopen(req, timeout=15) as resp:
            ms = (time.time() - t0) * 1000
            return True, f"{ms:.0f} ms"
    except HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────
#  GUI 组件
# ─────────────────────────────────────────────────

class Divider(ctk.CTkFrame):
    def __init__(self, parent, **kw):
        super().__init__(parent, height=1, fg_color=C["border"], **kw)


class StatusPill(ctk.CTkFrame):
    """运行状态小胶囊"""
    def __init__(self, parent, active=False, **kw):
        super().__init__(parent, corner_radius=20,
                         fg_color=C["accent"] if active else C["muted"],
                         width=10, height=10, **kw)
        self._active = active

    def set(self, active: bool):
        self.configure(fg_color=C["accent"] if active else C["muted"])


class Card(ctk.CTkFrame):
    def __init__(self, parent, **kw):
        super().__init__(parent,
                         fg_color=C["card"],
                         corner_radius=10,
                         border_width=1,
                         border_color=C["border"],
                         **kw)


class SectionLabel(ctk.CTkLabel):
    def __init__(self, parent, text, **kw):
        super().__init__(parent, text=text,
                         font=(FONT, 11),
                         text_color=C["dim"],
                         **kw)


class NavButton(ctk.CTkButton):
    def __init__(self, parent, text, icon="", **kw):
        super().__init__(
            parent,
            text=f"  {icon}   {text}",
            fg_color="transparent",
            hover_color=C["muted"],
            text_color=C["dim"],
            anchor="w",
            corner_radius=6,
            height=38,
            font=(FONT, 13),
            **kw,
        )

    def set_active(self, active: bool):
        self.configure(
            fg_color=C["border"] if active else "transparent",
            text_color=C["text"] if active else C["dim"],
        )


class PrimaryBtn(ctk.CTkButton):
    def __init__(self, parent, text, **kw):
        kw.setdefault("height", 36)
        kw.setdefault("corner_radius", 6)
        kw.setdefault("font", (FONT, 13))
        super().__init__(
            parent, text=text,
            fg_color=C["blue"], hover_color=C["blue_h"],
            **kw,
        )


class GreenBtn(ctk.CTkButton):
    def __init__(self, parent, text, **kw):
        kw.setdefault("height", 36)
        kw.setdefault("corner_radius", 6)
        kw.setdefault("font", (FONT, 13))
        super().__init__(
            parent, text=text,
            fg_color=C["accent"], hover_color=C["accent_h"],
            **kw,
        )


class GhostBtn(ctk.CTkButton):
    def __init__(self, parent, text, **kw):
        kw.setdefault("height", 36)
        kw.setdefault("corner_radius", 6)
        kw.setdefault("font", (FONT, 12))
        super().__init__(
            parent, text=text,
            fg_color=C["muted"], hover_color=C["border"],
            **kw,
        )


class DangerBtn(ctk.CTkButton):
    def __init__(self, parent, text, **kw):
        kw.setdefault("height", 36)
        kw.setdefault("corner_radius", 6)
        kw.setdefault("font", (FONT, 13))
        super().__init__(
            parent, text=text,
            fg_color=C["red_bg"], hover_color=C["danger"],
            text_color=C["red"],
            **kw,
        )


# ─────────────────────────────────────────────────
#  主应用
# ─────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("NIM 管理工具")
        self.geometry("900x640")
        self.minsize(820, 560)
        self.configure(fg_color=C["bg"])

        self._current_page = "status"
        self._build_layout()
        self._show_page("status")
        self._auto_refresh_loop()

    # ── 布局骨架 ──────────────────────────────────

    def _build_layout(self):
        # 侧边栏
        self.sidebar = ctk.CTkFrame(self, width=195,
                                    fg_color=C["sidebar"],
                                    corner_radius=0)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self._build_sidebar()

        # 分割线
        ctk.CTkFrame(self, width=1, fg_color=C["border"],
                     corner_radius=0).pack(side="left", fill="y")

        # 内容区
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.pack(side="left", fill="both", expand=True)

        # 构建各页面
        self.pages: dict[str, ctk.CTkFrame] = {}
        self.pages["status"]   = self._page_status()
        self.pages["keys"]     = self._page_keys()
        self.pages["services"] = self._page_services()

    def _build_sidebar(self):
        # Logo 区
        logo = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        logo.pack(fill="x", padx=16, pady=(22, 18))
        ctk.CTkLabel(logo, text="⚡", font=(FONT, 24)).pack(side="left")
        ctk.CTkLabel(logo, text=" NIM Tool",
                     font=(FONT, 16, "bold"),
                     text_color=C["text"]).pack(side="left")

        Divider(self.sidebar).pack(fill="x", padx=14, pady=(0, 14))

        # 导航按钮
        nav = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        nav.pack(fill="x", padx=10)

        self._nav_btns: dict[str, NavButton] = {}
        pages = [
            ("status",   "📊", "状态总览"),
            ("keys",     "🔑", "密钥管理"),
            ("services", "⚙️", "服务控制"),
        ]
        for pid, icon, label in pages:
            btn = NavButton(nav, label, icon=icon,
                            command=lambda p=pid: self._show_page(p))
            btn.pack(fill="x", pady=2)
            self._nav_btns[pid] = btn

        # 底部：代理状态
        Divider(self.sidebar).pack(fill="x", padx=14, side="bottom", pady=(0, 12))
        proxy_row = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        proxy_row.pack(side="bottom", fill="x", padx=16, pady=(0, 6))
        ctk.CTkLabel(proxy_row, text="系统代理",
                     font=(FONT, 11), text_color=C["dim"]).pack(side="left")
        self.lbl_proxy_sys = ctk.CTkLabel(
            proxy_row,
            text="已检测" if _proxy_url else "未检测",
            font=(FONT, 11),
            text_color=C["green"] if _proxy_url else C["muted"],
        )
        self.lbl_proxy_sys.pack(side="right")

    def _show_page(self, pid: str):
        for p, f in self.pages.items():
            f.pack_forget()
            self._nav_btns[p].set_active(False)
        self.pages[pid].pack(fill="both", expand=True, padx=24, pady=20)
        self._nav_btns[pid].set_active(True)
        self._current_page = pid
        if pid == "status":
            self._do_refresh_status()
        elif pid == "keys":
            self._render_key_list()

    # ─────────────────────────────────────────────
    #  页面：状态总览
    # ─────────────────────────────────────────────

    def _page_status(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self.content, fg_color="transparent")

        # 标题栏
        hdr = ctk.CTkFrame(frame, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 14))
        ctk.CTkLabel(hdr, text="状态总览",
                     font=(FONT, 20, "bold"),
                     text_color=C["text"]).pack(side="left")
        GhostBtn(hdr, text="↻  刷新", width=80, height=30,
                 command=self._do_refresh_status).pack(side="right")

        scroll = ctk.CTkScrollableFrame(frame, fg_color="transparent",
                                        scrollbar_button_color=C["muted"])
        scroll.pack(fill="both", expand=True)

        # ── 服务状态卡 ──
        svc = Card(scroll)
        svc.pack(fill="x", pady=(0, 10))
        SectionLabel(svc, "服务").pack(anchor="w", padx=16, pady=(12, 6))
        Divider(svc).pack(fill="x", padx=16, pady=(0, 8))

        def _svc_row(parent, name, port):
            r = ctk.CTkFrame(parent, fg_color="transparent")
            r.pack(fill="x", padx=16, pady=5)
            pill = StatusPill(r)
            pill.pack(side="left", padx=(0, 10))
            ctk.CTkLabel(r, text=name,
                         font=(FONT, 13), text_color=C["text"]).pack(side="left")
            ctk.CTkLabel(r, text=f":{port}",
                         font=(FONT_MONO, 12), text_color=C["dim"]).pack(side="left", padx=(6, 0))
            lbl = ctk.CTkLabel(r, text="—",
                               font=(FONT, 12), text_color=C["dim"])
            lbl.pack(side="right")
            return pill, lbl

        self._pill_fcc,   self._lbl_fcc   = _svc_row(svc, "fcc-server",  FCC_PORT)
        self._pill_proxy, self._lbl_proxy = _svc_row(svc, "nim-proxy",   PROXY_PORT)
        ctk.CTkFrame(svc, height=8, fg_color="transparent").pack()

        # ── 配置模式卡 ──
        mode = Card(scroll)
        mode.pack(fill="x", pady=(0, 10))
        SectionLabel(mode, "Claude 配置").pack(anchor="w", padx=16, pady=(12, 6))
        Divider(mode).pack(fill="x", padx=16, pady=(0, 8))

        mode_r = ctk.CTkFrame(mode, fg_color="transparent")
        mode_r.pack(fill="x", padx=16, pady=5)
        ctk.CTkLabel(mode_r, text="模式",
                     font=(FONT, 13), text_color=C["dim"]).pack(side="left")
        self._lbl_mode = ctk.CTkLabel(mode_r, text="—",
                                      font=(FONT, 13, "bold"),
                                      text_color=C["cyan"])
        self._lbl_mode.pack(side="right")

        url_r = ctk.CTkFrame(mode, fg_color="transparent")
        url_r.pack(fill="x", padx=16, pady=(2, 12))
        ctk.CTkLabel(url_r, text="BASE_URL",
                     font=(FONT_MONO, 12), text_color=C["dim"]).pack(side="left")
        self._lbl_url = ctk.CTkLabel(url_r, text="—",
                                     font=(FONT_MONO, 12), text_color=C["text"])
        self._lbl_url.pack(side="right")

        # ── OpenClaw 配置卡 ──
        oc = Card(scroll)
        oc.pack(fill="x", pady=(0, 10))
        SectionLabel(oc, "OpenClaw 配置").pack(anchor="w", padx=16, pady=(12, 6))
        Divider(oc).pack(fill="x", padx=16, pady=(0, 8))

        oc_mode_r = ctk.CTkFrame(oc, fg_color="transparent")
        oc_mode_r.pack(fill="x", padx=16, pady=5)
        ctk.CTkLabel(oc_mode_r, text="模式",
                     font=(FONT, 13), text_color=C["dim"]).pack(side="left")
        self._lbl_oc_mode = ctk.CTkLabel(oc_mode_r, text="—",
                                         font=(FONT, 13, "bold"),
                                         text_color=C["dim"])
        self._lbl_oc_mode.pack(side="right")

        oc_url_r = ctk.CTkFrame(oc, fg_color="transparent")
        oc_url_r.pack(fill="x", padx=16, pady=(2, 12))
        ctk.CTkLabel(oc_url_r, text="nvidia baseUrl",
                     font=(FONT_MONO, 12), text_color=C["dim"]).pack(side="left")
        self._lbl_oc_url = ctk.CTkLabel(oc_url_r, text="—",
                                        font=(FONT_MONO, 12), text_color=C["text"])
        self._lbl_oc_url.pack(side="right")

        # ── Key 汇总卡 ──
        kcard = Card(scroll)
        kcard.pack(fill="x", pady=(0, 10))
        SectionLabel(kcard, "API Keys").pack(anchor="w", padx=16, pady=(12, 6))
        Divider(kcard).pack(fill="x", padx=16, pady=(0, 8))
        self._keys_summary = ctk.CTkFrame(kcard, fg_color="transparent")
        self._keys_summary.pack(fill="x", padx=16, pady=(0, 12))

        # ── 连通性卡 ──
        lat = Card(scroll)
        lat.pack(fill="x", pady=(0, 10))
        SectionLabel(lat, "连通性").pack(anchor="w", padx=16, pady=(12, 6))
        Divider(lat).pack(fill="x", padx=16, pady=(0, 8))

        def _conn_row(parent, label):
            r = ctk.CTkFrame(parent, fg_color="transparent")
            r.pack(fill="x", padx=16, pady=4)
            ctk.CTkLabel(r, text=label, font=(FONT, 12),
                         text_color=C["dim"]).pack(side="left")
            lbl = ctk.CTkLabel(r, text="—", font=(FONT_MONO, 12),
                               text_color=C["dim"])
            lbl.pack(side="right")
            return lbl

        self._lbl_conn_fcc   = _conn_row(lat, "fcc-server  /v1/models")
        self._lbl_conn_proxy = _conn_row(lat, "nim-proxy   /health")
        self._lbl_latency    = _conn_row(lat, "NIM API 延迟")
        ctk.CTkFrame(lat, height=6, fg_color="transparent").pack()

        return frame

    def _do_refresh_status(self):
        def _run():
            # ── 服务健康状态（用实际 HTTP 检查，不只看端口）──
            port_fcc   = test_port(FCC_PORT)
            port_proxy = test_port(PROXY_PORT)

            fcc_ok = False
            if port_fcc:
                try:
                    with urlopen(f"http://127.0.0.1:{FCC_PORT}/v1/models", timeout=3) as r:
                        fcc_ok = r.status == 200
                except Exception:
                    pass

            proxy_ok = False
            if port_proxy:
                proxy_ok = _http_health(f"http://127.0.0.1:{PROXY_PORT}/health") is not None

            self._pill_fcc.set(fcc_ok)
            self._pill_proxy.set(proxy_ok)

            if fcc_ok:
                fcc_text, fcc_color = "运行中", C["green"]
            elif port_fcc:
                occupant = _describe_port_occupant(FCC_PORT)
                fcc_text, fcc_color = f"端口占用: {occupant}", C["yellow"]
            else:
                fcc_text, fcc_color = "已停止", C["red"]

            if proxy_ok:
                proxy_text, proxy_color = "运行中", C["green"]
            elif port_proxy:
                occupant = _describe_port_occupant(PROXY_PORT)
                proxy_text, proxy_color = f"端口占用: {occupant}", C["yellow"]
            else:
                proxy_text, proxy_color = "已停止", C["red"]

            self._lbl_fcc.configure(text=fcc_text, text_color=fcc_color)
            self._lbl_proxy.configure(text=proxy_text, text_color=proxy_color)

            # ── Claude 配置 ──
            settings = load_json(SETTINGS_PATH)
            if settings and isinstance(settings, dict):
                env    = settings.get("env", {})
                url    = env.get("ANTHROPIC_BASE_URL", "")
                is_nim = "127.0.0.1" in url
                self._lbl_mode.configure(
                    text="NIM 模式" if is_nim else "mimo 模式（默认）",
                    text_color=C["yellow"] if is_nim else C["cyan"],
                )
                self._lbl_url.configure(text=url or "（未设置）")
            else:
                self._lbl_mode.configure(text="配置读取失败", text_color=C["red"])
                self._lbl_url.configure(text="—")

            # ── OpenClaw 配置 ──
            nvidia = get_openclaw_nvidia()
            if nvidia is None:
                self._lbl_oc_mode.configure(text="未检测到", text_color=C["dim"])
                self._lbl_oc_url.configure(text="openclaw.json 不存在或无 nvidia provider")
            else:
                base_url = nvidia.get("baseUrl", "（未设置）")
                is_proxy = "127.0.0.1" in base_url
                self._lbl_oc_mode.configure(
                    text="代理模式" if is_proxy else "直连 NIM（默认）",
                    text_color=C["yellow"] if is_proxy else C["cyan"],
                )
                self._lbl_oc_url.configure(text=base_url)

            # ── Key 汇总 ──
            for w in self._keys_summary.winfo_children():
                w.destroy()
            keys = load_keys()
            if keys:
                ctk.CTkLabel(self._keys_summary,
                             text=f"{len(keys)} 个 Key   ",
                             font=(FONT, 13), text_color=C["text"]).pack(side="left")
                preview = "  ".join(f"···{k[-4:]}" for k in keys[:5])
                if len(keys) > 5:
                    preview += f"  +{len(keys)-5}"
                ctk.CTkLabel(self._keys_summary, text=preview,
                             font=(FONT_MONO, 12),
                             text_color=C["dim"]).pack(side="left")
            else:
                ctk.CTkLabel(self._keys_summary,
                             text="暂无 Key，请前往「密钥管理」添加",
                             font=(FONT, 13), text_color=C["red"]).pack(side="left")

            # ── 连通性：fcc /v1/models ──
            if fcc_ok:
                self._lbl_conn_fcc.configure(text="正常 (200)", text_color=C["green"])
            elif port_fcc:
                self._lbl_conn_fcc.configure(text="端口占用，服务异常", text_color=C["yellow"])
            else:
                self._lbl_conn_fcc.configure(text="服务未运行", text_color=C["red"])

            # ── 连通性：proxy /health ──
            health = _http_health(f"http://127.0.0.1:{PROXY_PORT}/health")
            if health:
                loaded  = health.get("keys_loaded", "?")
                current = health.get("current_key", "")
                suffix  = f"  当前 {current}" if current else ""
                self._lbl_conn_proxy.configure(
                    text=f"正常 · {loaded} 个 Key{suffix}",
                    text_color=C["green"],
                )
            elif port_proxy:
                self._lbl_conn_proxy.configure(text="端口占用，服务异常", text_color=C["yellow"])
            else:
                self._lbl_conn_proxy.configure(text="服务未运行", text_color=C["red"])

            # ── 连通性：NIM API 延迟 ──
            keys = load_keys()
            if keys:
                ok, info = test_key(keys[0])
                self._lbl_latency.configure(
                    text=info,
                    text_color=C["green"] if ok else C["red"],
                )
            else:
                self._lbl_latency.configure(text="无 Key", text_color=C["dim"])

        threading.Thread(target=_run, daemon=True).start()

    def _auto_refresh_loop(self):
        def _tick():
            while True:
                time.sleep(12)
                if self._current_page == "status":
                    self._do_refresh_status()
        threading.Thread(target=_tick, daemon=True).start()

    # ─────────────────────────────────────────────
    #  页面：密钥管理
    # ─────────────────────────────────────────────

    def _page_keys(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self.content, fg_color="transparent")

        # 标题
        hdr = ctk.CTkFrame(frame, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 14))
        ctk.CTkLabel(hdr, text="密钥管理",
                     font=(FONT, 20, "bold"),
                     text_color=C["text"]).pack(side="left")

        # 添加 Key 卡
        add_card = Card(frame)
        add_card.pack(fill="x", pady=(0, 10))
        SectionLabel(add_card, "添加新 Key").pack(anchor="w", padx=16, pady=(12, 6))
        Divider(add_card).pack(fill="x", padx=16, pady=(0, 10))

        inp_row = ctk.CTkFrame(add_card, fg_color="transparent")
        inp_row.pack(fill="x", padx=16, pady=(0, 14))

        self._key_input = ctk.CTkEntry(
            inp_row,
            placeholder_text="nvapi-xxxxxxxxxxxxxxxxxx...",
            font=(FONT_MONO, 13),
            height=38, corner_radius=6,
        )
        self._key_input.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self._key_input.bind("<Return>", lambda e: self._add_key())
        GreenBtn(inp_row, text="添加", width=80, command=self._add_key).pack(side="left")

        # Key 列表卡
        list_card = Card(frame)
        list_card.pack(fill="both", expand=True)

        list_hdr = ctk.CTkFrame(list_card, fg_color="transparent")
        list_hdr.pack(fill="x", padx=16, pady=(12, 6))
        SectionLabel(list_hdr, "Key 列表").pack(side="left")

        btn_group = ctk.CTkFrame(list_hdr, fg_color="transparent")
        btn_group.pack(side="right")
        GhostBtn(btn_group, text="↻ 刷新缓存", width=96, height=28,
                 command=self._reload_proxy_keys).pack(side="left", padx=(0, 8))
        GhostBtn(btn_group, text="✓ 全部测试", width=96, height=28,
                 command=self._test_all_keys).pack(side="left")

        Divider(list_card).pack(fill="x", padx=16, pady=(0, 4))

        self._keys_list = ctk.CTkScrollableFrame(
            list_card, fg_color="transparent",
            scrollbar_button_color=C["muted"],
        )
        self._keys_list.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        return frame

    def _render_key_list(self):
        for w in self._keys_list.winfo_children():
            w.destroy()
        keys = load_keys()
        if not keys:
            ctk.CTkLabel(self._keys_list,
                         text="还没有任何 Key，在上方输入框添加",
                         font=(FONT, 13), text_color=C["dim"]).pack(pady=24)
            return
        for i, key in enumerate(keys):
            self._render_key_row(i, key)

    def _render_key_row(self, idx: int, key: str):
        row = ctk.CTkFrame(self._keys_list,
                           fg_color=C["bg"],
                           corner_radius=6)
        row.pack(fill="x", padx=4, pady=3)

        ctk.CTkLabel(row, text=f" #{idx+1}",
                     width=34, font=(FONT, 12),
                     text_color=C["dim"]).pack(side="left", padx=(6, 0))

        masked = "*" * max(len(key) - 8, 6) + key[-4:]
        ctk.CTkLabel(row, text=masked,
                     font=(FONT_MONO, 13),
                     text_color=C["text"]).pack(side="left", padx=8)

        status_lbl = ctk.CTkLabel(row, text="",
                                  font=(FONT, 12), text_color=C["dim"])
        status_lbl.pack(side="right", padx=(0, 8))

        DangerBtn(row, text="删除", width=58, height=28,
                  command=lambda k=key: self._remove_key(k)).pack(side="right", pady=6, padx=(0, 4))

        GhostBtn(row, text="测试", width=58, height=28,
                 command=lambda k=key, lbl=status_lbl: self._test_single_key(k, lbl)
                 ).pack(side="right", pady=6, padx=(0, 4))

    def _add_key(self):
        key = self._key_input.get().strip()
        if not key:
            return
        if not key.startswith("nvapi-"):
            messagebox.showerror("格式错误", "Key 必须以 nvapi- 开头")
            return
        keys = load_keys()
        if key in keys:
            messagebox.showinfo("重复", "该 Key 已存在")
            return
        keys.append(key)
        save_keys(keys)
        self._key_input.delete(0, "end")
        self._render_key_list()

    def _remove_key(self, key: str):
        if not messagebox.askyesno("确认删除", f"确定删除 Key  ···{key[-4:]}？"):
            return
        keys = load_keys()
        if key in keys:
            keys.remove(key)
            save_keys(keys)
        self._render_key_list()

    def _test_single_key(self, key: str, label: ctk.CTkLabel):
        label.configure(text="测试中…", text_color=C["dim"])
        def _run():
            ok, info = test_key(key)
            label.configure(
                text=f"✓ {info}" if ok else f"✗ {info}",
                text_color=C["green"] if ok else C["red"],
            )
        threading.Thread(target=_run, daemon=True).start()

    def _test_all_keys(self):
        keys = load_keys()
        if not keys:
            messagebox.showinfo("提示", "暂无 Key")
            return
        def _run():
            lines = []
            for k in keys:
                ok, info = test_key(k)
                lines.append(f"{'✓' if ok else '✗'}  ···{k[-4:]}   {info}")
            messagebox.showinfo(f"全部测试结果（共 {len(keys)} 个）", "\n".join(lines))
        threading.Thread(target=_run, daemon=True).start()

    def _reload_proxy_keys(self):
        n = reload_proxy()
        if n is not None:
            messagebox.showinfo("成功", f"代理已重载 {n} 个 Key")
        else:
            messagebox.showerror("失败", "代理未运行，请先启动服务")

    # ─────────────────────────────────────────────
    #  页面：服务控制
    # ─────────────────────────────────────────────

    def _page_services(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self.content, fg_color="transparent")

        ctk.CTkLabel(frame, text="服务控制",
                     font=(FONT, 20, "bold"),
                     text_color=C["text"]).pack(anchor="w", pady=(0, 14))

        scroll = ctk.CTkScrollableFrame(frame, fg_color="transparent",
                                        scrollbar_button_color=C["muted"])
        scroll.pack(fill="both", expand=True)

        # ── 代理服务 ──
        svc_card = Card(scroll)
        svc_card.pack(fill="x", pady=(0, 10))
        SectionLabel(svc_card, "代理服务").pack(anchor="w", padx=16, pady=(12, 6))
        Divider(svc_card).pack(fill="x", padx=16, pady=(0, 10))
        ctk.CTkLabel(svc_card,
                     text="Claude Code → fcc-server(:8082) → nim-proxy(:8083) → NIM API\nOpenClaw 直连 nim-proxy(:8083)",
                     font=(FONT, 12), text_color=C["dim"], justify="left").pack(anchor="w", padx=16)
        svc_btns = ctk.CTkFrame(svc_card, fg_color="transparent")
        svc_btns.pack(fill="x", padx=16, pady=(10, 14))
        GreenBtn(svc_btns, text="▶  启动全部", command=self._start_services).pack(side="left", padx=(0, 10))
        DangerBtn(svc_btns, text="■  停止全部", command=self._stop_services).pack(side="left")

        # ── Claude 配置 ──
        claude_card = Card(scroll)
        claude_card.pack(fill="x", pady=(0, 10))
        SectionLabel(claude_card, "Claude 配置").pack(anchor="w", padx=16, pady=(12, 6))
        Divider(claude_card).pack(fill="x", padx=16, pady=(0, 10))
        ctk.CTkLabel(claude_card,
                     text="修改 ~/.claude/settings.json，将 API 请求重定向至本地 fcc-server",
                     font=(FONT, 12), text_color=C["dim"]).pack(anchor="w", padx=16)
        c_btns = ctk.CTkFrame(claude_card, fg_color="transparent")
        c_btns.pack(fill="x", padx=16, pady=(10, 14))
        PrimaryBtn(c_btns, text="切换到 NIM 模式", command=self._apply_nim).pack(side="left", padx=(0, 10))
        GhostBtn(c_btns, text="恢复 mimo 配置", command=self._restore_mimo).pack(side="left")

        # ── OpenClaw ──
        oc_card = Card(scroll)
        oc_card.pack(fill="x", pady=(0, 10))
        SectionLabel(oc_card, "OpenClaw 配置").pack(anchor="w", padx=16, pady=(12, 6))
        Divider(oc_card).pack(fill="x", padx=16, pady=(0, 10))
        ctk.CTkLabel(oc_card,
                     text="将 openclaw.json 中的 nvidia provider 指向本地代理（:8083）",
                     font=(FONT, 12), text_color=C["dim"]).pack(anchor="w", padx=16)
        oc_btns = ctk.CTkFrame(oc_card, fg_color="transparent")
        oc_btns.pack(fill="x", padx=16, pady=(10, 14))
        PrimaryBtn(oc_btns, text="应用 OpenClaw 代理", command=self._apply_openclaw).pack(side="left", padx=(0, 10))
        GhostBtn(oc_btns, text="恢复 OpenClaw", command=self._restore_openclaw).pack(side="left")

        # ── Admin UI ──
        admin_card = Card(scroll)
        admin_card.pack(fill="x", pady=(0, 10))
        SectionLabel(admin_card, "Admin 面板").pack(anchor="w", padx=16, pady=(12, 6))
        Divider(admin_card).pack(fill="x", padx=16, pady=(0, 10))
        ctk.CTkLabel(admin_card,
                     text="fcc-server 内置 Web 面板：可修改模型路由、验证 Key、重启服务",
                     font=(FONT, 12), text_color=C["dim"]).pack(anchor="w", padx=16)
        admin_btns = ctk.CTkFrame(admin_card, fg_color="transparent")
        admin_btns.pack(fill="x", padx=16, pady=(10, 14))
        PrimaryBtn(admin_btns, text="打开 Admin UI", command=self._open_admin).pack(side="left")

        # ── 启动 Claude Code ──
        launch_card = Card(scroll)
        launch_card.pack(fill="x", pady=(0, 10))
        SectionLabel(launch_card, "启动 Claude Code").pack(anchor="w", padx=16, pady=(12, 6))
        Divider(launch_card).pack(fill="x", padx=16, pady=(0, 10))
        ctk.CTkLabel(launch_card,
                     text="自动切换 NIM 配置并以指定目录运行 fcc-claude",
                     font=(FONT, 12), text_color=C["dim"]).pack(anchor="w", padx=16)

        dir_row = ctk.CTkFrame(launch_card, fg_color="transparent")
        dir_row.pack(fill="x", padx=16, pady=(10, 14))
        self._dir_entry = ctk.CTkEntry(
            dir_row,
            placeholder_text="项目目录（留空 = 当前目录）",
            font=(FONT, 13), height=36, corner_radius=6,
        )
        self._dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        GreenBtn(dir_row, text="启动", width=80, command=self._launch_claude).pack(side="left")

        return frame

    # ── 服务控制回调 ──────────────────────────────

    def _start_services(self):
        def _run():
            msgs = []
            icons = {"ok": "✓", "already": "·", "missing": "✗", "fail": "✗", "occupied": "⚠"}
            status, desc = start_proxy()
            msgs.append(f"{icons.get(status,'?')}  {desc}")
            status, desc = start_fcc_server()
            msgs.append(f"{icons.get(status,'?')}  {desc}")
            title = "启动结果"
            if any(s in m for m in msgs for s in ("✗", "⚠")):
                messagebox.showwarning(title, "\n\n".join(msgs))
            else:
                messagebox.showinfo(title, "\n\n".join(msgs))
        threading.Thread(target=_run, daemon=True).start()

    def _open_admin(self):
        import webbrowser
        if not test_port(FCC_PORT):
            messagebox.showwarning("提示", "fcc-server 未运行，请先启动服务")
            return
        webbrowser.open("http://127.0.0.1:8082/admin")

    def _stop_services(self):
        if messagebox.askyesno("确认", "停止全部后台服务？"):
            stop_all_services()
            messagebox.showinfo("完成", "全部服务已停止")

    def _apply_nim(self):
        (SCRIPT_DIR / "backups").mkdir(parents=True, exist_ok=True)
        backup_settings()
        switch_to_nim_mode()
        messagebox.showinfo("成功", "已切换到 NIM 模式\n原配置已备份")

    def _restore_mimo(self):
        if restore_settings():
            messagebox.showinfo("成功", "已恢复 mimo 配置")
        else:
            messagebox.showerror("失败", "备份文件不存在，无法恢复")

    def _apply_openclaw(self):
        if not OPENCLAW_PATH.exists():
            messagebox.showerror("失败", "openclaw.json 不存在")
            return
        (SCRIPT_DIR / "backups").mkdir(parents=True, exist_ok=True)
        if backup_openclaw() and switch_openclaw_to_proxy():
            messagebox.showinfo("成功", "已应用 OpenClaw 代理配置\n原配置已备份")
        else:
            messagebox.showerror("失败", "切换失败，请检查 openclaw.json 结构")

    def _restore_openclaw(self):
        if restore_openclaw():
            messagebox.showinfo("成功", "已恢复 OpenClaw 配置")
        else:
            messagebox.showerror("失败", "备份文件不存在，无法恢复")

    def _launch_claude(self):
        d = self._dir_entry.get().strip() or os.getcwd()
        project_dir = str(Path(d).resolve())
        (SCRIPT_DIR / "backups").mkdir(parents=True, exist_ok=True)
        backup_settings()
        switch_to_nim_mode()
        def _run():
            try:
                subprocess.run(["fcc-claude"], cwd=project_dir)
            except FileNotFoundError:
                messagebox.showerror("错误", "fcc-claude 未找到，请检查 PATH")
            except Exception as e:
                messagebox.showerror("错误", str(e))
        threading.Thread(target=_run, daemon=True).start()


# ─────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        app = App()
        app.mainloop()
    except Exception as _exc:
        import traceback
        tkinter.Tk().withdraw()
        messagebox.showerror(
            "启动崩溃",
            f"程序遇到未处理的异常，请截图反馈：\n\n{traceback.format_exc()}",
        )
