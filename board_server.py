"""本地棋盘 HTTP 服务器：生命周期 + Handler。

从 plugin.py 抽出，只依赖标准库 —— 不 import 插件宿主 SDK，也不 import engine/plugin。
这样服务器生命周期与请求边界可以单独 import、单独测试；plugin.py 仍重新导出
``BoardServer`` / ``make_handler`` 保持旧接口。
"""
from __future__ import annotations

import http.server
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote, parse_qs

try:
    from .theme_resources import runtime_config, resolve_theme_file
except ImportError:
    from theme_resources import runtime_config, resolve_theme_file

# 只允许 art/ 下已知子目录 + 单层文件名；Handler 的路径校验直接用这个常量。
_ART_NAME_RE = re.compile(r"^(poi/|icons/|chibi/|board/|lib/)?[a-z0-9_]+\.(png|jpg|svg|js|glb)$")
_MAX_BODY = 16 * 1024


class BoardServer:
    """本地棋盘服务器。无对局且闲置时回收，board_url 按需重启。

    同一实例重启优先复用端口；有对局时不闲置关服，以免后台棋盘页永久失联。
    """

    IDLE_SHUTDOWN_SECONDS = 30 * 60

    def __init__(self, service: Any, plugin_dir: Path) -> None:
        self.instance_id = uuid.uuid4().hex
        self._service = service
        self._plugin_dir = plugin_dir
        self._lifecycle_lock = threading.RLock()
        self._httpd: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._watchdog: threading.Timer | None = None
        self._watchdog_version = 0
        self._last_request = time.time()
        # Server/asset/health activity is not evidence of a person viewing the board.
        self._last_visible_page = 0.0
        self._port = 0
        self.url = ""

    def start(self) -> str:
        with self._lifecycle_lock:
            if self._httpd is not None:
                self._touch()
                return self.url
            handler = make_handler(self._service, self._plugin_dir)
            try:
                self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", self._port), handler)
            except OSError:
                if not self._port:
                    raise
                # 原端口被别的程序占用时仍允许打开一个新棋盘。
                self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            self._port = self._httpd.server_address[1]
            self.url = f"http://127.0.0.1:{self._port}/"
            self._thread = threading.Thread(target=self._httpd.serve_forever, name="boardgame-board", daemon=True)
            self._thread.start()
            self._touch()
            return self.url

    def _arm_idle(self, delay: float) -> None:
        """调用方持有生命周期锁；旧回调不能影响重启后的新实例。"""
        self._watchdog_version += 1
        version = self._watchdog_version
        self._watchdog = threading.Timer(delay, lambda: self._idle_check(version))
        self._watchdog.daemon = True
        self._watchdog.start()

    def _touch(self) -> None:
        # 请求只更新时间，不为每张素材/每次轮询创建一个新 Timer 线程。
        with self._lifecycle_lock:
            self._last_request = time.time()
            if self._httpd is not None and self._watchdog is None:
                self._arm_idle(self.IDLE_SHUTDOWN_SECONDS)

    def _idle_check(self, version: int | None = None) -> None:
        with self._lifecycle_lock:
            if self._httpd is None or (version is not None and version != self._watchdog_version):
                return
            if self._watchdog is not None:
                self._watchdog.cancel()
                self._watchdog = None
            idle = time.time() - self._last_request
            # 不在生命周期锁内拿 action_lock：动作可能正持 action_lock 访问 board_url。
            if idle >= self.IDLE_SHUTDOWN_SECONDS and self._service._game is None:
                self.stop()
            else:
                delay = self.IDLE_SHUTDOWN_SECONDS if idle >= self.IDLE_SHUTDOWN_SECONDS else self.IDLE_SHUTDOWN_SECONDS - idle
                self._arm_idle(max(1, delay))

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._watchdog_version += 1
            if self._watchdog is not None:
                self._watchdog.cancel()
                self._watchdog = None
            if self._httpd is not None:
                self._httpd.shutdown()
                self._httpd.server_close()
                self._httpd = None
            if self._thread is not None:
                self._thread.join(timeout=5)
                self._thread = None
            self.url = ""


def make_handler(service: Any, plugin_dir: Path):
    board_page = plugin_dir / "board.html"
    art_dir = plugin_dir / "art"
    # Validate once per server start, not on every large GLB/texture request.
    config = runtime_config(plugin_dir)
    config_script = ("globalThis.BoardRuntimeConfig = " + json.dumps(config, ensure_ascii=False) + ";\n").encode("utf-8")
    content_types = {".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml",
                     ".html": "text/html; charset=utf-8",
                     ".js": "application/javascript; charset=utf-8",
                     ".glb": "model/gltf-binary"}

    class Handler(http.server.BaseHTTPRequestHandler):
        def _trusted_request(self) -> bool:
            # 监听 loopback 不等于可信：拒绝 DNS rebinding Host 和跨站浏览器请求。
            port = self.server.server_address[1]
            host = (self.headers.get("Host") or "").lower()
            origin = self.headers.get("Origin")
            if (len(self.headers.get_all("Host", [])) != 1
                    or host not in {f"127.0.0.1:{port}", f"localhost:{port}"}
                    or (origin is not None and origin != f"http://{host}")):
                self._send(403, b"forbidden origin or host", "text/plain")
                return False
            return True

        def do_GET(self) -> None:  # noqa: N802
            if not self._trusted_request():
                return
            service._server._touch()          # 有请求 = 页面还开着，重置闲时关闭倒计时
            path = self.path.split("?", 1)[0]
            try:
                if path in ("/", "/index.html"):
                    self._send_file(board_page)
                elif path == "/board-config.js":
                    self._send(200, config_script, "application/javascript; charset=utf-8")
                elif path.startswith("/theme/"):
                    target = resolve_theme_file(plugin_dir, unquote(path[len("/theme/"):])) if config else None
                    if target is None:
                        self._send(404, b"not found", "text/plain")
                    else:
                        self._send_file(target)
                elif path == "/state":
                    # busy=1 表示页面上正开着弹窗（骰子/事件卡/说明）或用户正在操作——
                    # 主动搭话要避开这种时刻（借用 SillyTavern 那个模拟器扩展的礼貌规则）
                    query = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                    service._server._last_visible_page = time.time() if query.get("visible") == ["1"] else 0.0
                    service.note_page_busy(query.get("busy") == ["1"])
                    body = json.dumps(service.state_payload(), ensure_ascii=False).encode("utf-8")
                    self._send(200, body, "application/json; charset=utf-8")
                elif path == "/portrait":
                    # 当前角色的立绘（换角色后 URL 会带上新 id，不会吃到旧图）
                    target = service.portrait_file()
                    if target is None:
                        self._send(404, b"no portrait", "text/plain")
                    else:
                        self._send_file(target)
                elif path.startswith("/art/"):
                    name = path[len("/art/"):]
                    if not _ART_NAME_RE.match(name):
                        self._send(404, b"not found", "text/plain")
                        return
                    # /art/poi/xxx.png = AI 出的地标插图（放在 art/poi/ 子目录里）
                    self._send_file(art_dir / name)
                else:
                    self._send(404, b"not found", "text/plain")
            except OSError:
                self._send(500, b"internal error", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            if not self._trusted_request():
                return
            service._server._touch()
            path = self.path.split("?", 1)[0]
            if path != "/api/action":
                self._send(404, b"not found", "text/plain")
                return
            if self.headers.get_content_type() != "application/json":
                self._send(415, b"application/json required", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length < 0:
                    raise ValueError("negative Content-Length")
                if length > _MAX_BODY:
                    self._send(413, b"too large", "text/plain")
                    return
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                if not isinstance(payload, dict):
                    raise ValueError("JSON object required")
                action = str(payload.get("action") or "")
                arguments = payload.get("arguments") or {}
                # Older local clients/tools may omit metadata. Protocol-aware clients
                # send it separately from game arguments; never let it become a move.
                if "metadata" in payload:
                    result = service.handle_action(action, arguments if isinstance(arguments, dict) else {},
                                                   metadata=payload["metadata"])
                else:
                    result = service.handle_action(action, arguments if isinstance(arguments, dict) else {})
                body = json.dumps(result, ensure_ascii=False).encode("utf-8")
                status = result.get("httpStatus", 200)
                self._send(status if status in (200, 400, 409, 500) else 200, body,
                           "application/json; charset=utf-8")
            except (ValueError, json.JSONDecodeError) as error:
                body = json.dumps({"ok": False, "error": f"请求无效：{type(error).__name__}"}, ensure_ascii=False).encode("utf-8")
                self._send(400, body, "application/json; charset=utf-8")

        def _send_file(self, target: Path) -> None:
            suffix = target.suffix.lower()
            with target.open("rb") as handle:
                self._send(200, handle.read(), content_types.get(suffix, "application/octet-stream"))

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # 一律 no-store：本地 localhost 拉文件毫秒级，缓存只会让"同步了新资产还看到旧的"
            # （glb/烤图/立绘都被 WebView 缓过 1 小时，用户实测换新不见效就是它）
            self.send_header("Cache-Control", "no-store")
            # A replacement plugin may restart version numbering; clients must discard old UI state.
            instance = getattr(service._server, "instance_id", "")
            if instance:
                self.send_header("X-Board-Instance", instance)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # 静默默认访问日志
            return

    return Handler


# 旧名保留：plugin._make_handler 以及历史测试引用的私有入口。
_make_handler = make_handler
