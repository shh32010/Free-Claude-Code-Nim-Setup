"""NIM 多 Key 轮换代理。

用法:
  python nim_key_proxy.py              # 启动代理（默认端口 8083）
  python nim_key_proxy.py --port 9000  # 指定端口

free-claude-code 配置:
  NVIDIA_NIM_API_KEY=dummy
  NVIDIA_NIM_PROXY=http://127.0.0.1:8083
"""

import json
import os
import sys
import threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen, install_opener, build_opener, ProxyHandler
from urllib.error import HTTPError

# Windows 控制台 UTF-8 支持
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 检测系统代理
def _detect_proxy() -> str | None:
    """读取系统代理设置。"""
    # 优先用环境变量
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy:
        return proxy
    # Windows 注册表
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
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
    print(f"[proxy] 使用系统代理: {_proxy_url}")
    proxy_handler = ProxyHandler({"http": _proxy_url, "https": _proxy_url})
    install_opener(build_opener(proxy_handler))
else:
    print("[proxy] 未检测到系统代理，直连 NIM")

NIM_BASE = "https://integrate.api.nvidia.com"
KEYS_FILE = Path(__file__).parent / "nim_keys.json"


def load_keys() -> list[str]:
    if KEYS_FILE.exists():
        return json.loads(KEYS_FILE.read_text(encoding="utf-8"))
    return []


class KeyRotator:
    def __init__(self) -> None:
        self._keys: list[str] = []
        self._index = 0
        self._lock = threading.Lock()
        self.reload()

    def reload(self) -> None:
        self._keys = load_keys()
        with self._lock:
            self._index = 0
        print(f"[rotator] 已加载 {len(self._keys)} 个 API Key")

    @property
    def count(self) -> int:
        return len(self._keys)

    def current(self) -> str | None:
        if not self._keys:
            return None
        with self._lock:
            return self._keys[self._index % len(self._keys)]

    def next(self) -> str | None:
        if not self._keys:
            return None
        with self._lock:
            self._index = (self._index + 1) % len(self._keys)
            key = self._keys[self._index]
            print(f"[rotator] 切换到 Key #{self._index + 1}（后4位: ...{key[-4:]}）")
            return key

    def key_info(self, key: str) -> str:
        for i, k in enumerate(self._keys):
            if k == key:
                return f"#{i + 1}"
        return "??"


rotator = KeyRotator()


class ProxyHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        target_url = f"{NIM_BASE}{self.path}"
        max_attempts = rotator.count if rotator.count > 0 else 1

        for attempt in range(max_attempts):
            key = rotator.current()
            if not key:
                self.send_error(503, "No API keys configured. Add keys via nim_manager.py")
                return

            key_label = rotator.key_info(key)
            print(f"[proxy] {self.path} -> Key {key_label} (attempt {attempt + 1})")

            req = Request(target_url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", f"Bearer {key}")

            try:
                with urlopen(req, timeout=120) as resp:
                    self.send_response(resp.status)
                    for header, value in resp.getheaders():
                        if header.lower() not in ("transfer-encoding", "connection"):
                            self.send_header(header, value)
                    self.end_headers()
                    self.wfile.write(resp.read())
                    return
            except HTTPError as e:
                if e.code == 429:
                    print(f"[proxy] Key {key_label} 收到 429，切换到下一个 Key")
                    rotator.next()
                    continue
                # 其他错误直接返回
                self.send_response(e.code)
                self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
                self.end_headers()
                self.wfile.write(e.read())
                return
            except Exception as e:
                print(f"[proxy] Key {key_label} 请求失败: {e}")
                rotator.next()
                continue

        self.send_error(502, "All API keys exhausted")

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            info = {
                "keys_loaded": rotator.count,
                "current_key": f"...{rotator.current()[-4:]}" if rotator.current() else None,
            }
            self.wfile.write(json.dumps(info).encode())
            return

        if self.path == "/keys/reload":
            rotator.reload()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"reloaded": rotator.count}).encode())
            return

        # 其他 GET 请求转发到 NIM（用于 /v1/models 等）
        target_url = f"{NIM_BASE}{self.path}"
        key = rotator.current()
        if not key:
            self.send_error(503, "No API keys configured")
            return

        req = Request(target_url, method="GET")
        req.add_header("Authorization", f"Bearer {key}")
        try:
            with urlopen(req, timeout=30) as resp:
                self.send_response(resp.status)
                for header, value in resp.getheaders():
                    if header.lower() not in ("transfer-encoding", "connection"):
                        self.send_header(header, value)
                self.end_headers()
                self.wfile.write(resp.read())
        except HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.end_headers()
            self.wfile.write(e.read())

    def log_message(self, format: str, *args) -> None:
        pass  # 静默 HTTP 日志，用自定义日志


def main() -> None:
    port = 8083
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        port = int(sys.argv[idx + 1])

    server = HTTPServer(("127.0.0.1", port), ProxyHandler)
    print(f"[proxy] NIM 多 Key 代理已启动: http://127.0.0.1:{port}")
    print(f"[proxy] 已加载 {rotator.count} 个 Key")
    print(f"[proxy] free-claude-code 配置: NVIDIA_NIM_PROXY=http://127.0.0.1:{port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[proxy] 已停止")
        server.server_close()


if __name__ == "__main__":
    main()
