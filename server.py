"""总台服务：消息时间线 + 独立应用面板。

零依赖，只用标准库。启动：

    python3 server.py

端口从 PORT_START 起找第一个空闲的，真实端口写进 .hub_state.json，
just_run.sh 靠这个文件探活，避免端口写死后撞车。

只读 Hermes 与各项目目录，不写、不改、不删任何业务文件。
唯一会写的是自己目录下的 hub.db。
"""

import json
import os
import re
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin, urlparse, parse_qs, unquote

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hub  # noqa: E402

if os.environ.get("HUB_DB"):
    hub.DB_FILE = os.environ["HUB_DB"]

HOST = "127.0.0.1"
PORT_START = int(os.environ.get("HUB_PORT") or 8899)
PORT_TRIES = 60
STATE_FILE = os.environ.get("HUB_STATE") or os.path.join(HERE, ".hub_state.json")
STATIC_DIR = os.path.join(HERE, "static")
APPS_FILE = hub.APPS_FILE

# 允许读取的根目录：媒体浏览限制在这几个根之内
ALLOW_ROOTS = [
    os.path.expanduser("~/.hermes"),
    os.path.dirname(HERE),
]
MEDIA_SUFFIX = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
CONTENT_SUFFIX = {".md", ".txt", ".json", ".csv"}

SYNC_INTERVAL = 30
APP_CACHE_TTL = 5
HOST_RE = re.compile(r"^(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$")

_lock = threading.Lock()
_app_cache = {}


def load_apps():
    with open(APPS_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh).get("apps", [])


def app_path(app):
    """内容应用的目录，支持 ~ 与相对本项目的路径。"""
    raw = os.path.expanduser(app.get("path") or "")
    if not os.path.isabs(raw):
        raw = os.path.join(HERE, raw)
    return os.path.realpath(raw)


def port_open(port, host="127.0.0.1", timeout=0.35):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_status(url, timeout=1.2):
    """请求本地服务的健康检查路径，返回 (状态码, 错误)。"""
    try:
        req = urllib.request.Request(url, method="GET")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, ""
    except urllib.error.HTTPError as exc:
        return exc.code, f"HTTP {exc.code}"
    except Exception as exc:  # 连接被拒、超时、协议错误都算不健康
        return 0, str(exc)


def content_files(root, recursive=False, depth=2):
    """列出内容目录里的可读文件，按修改时间倒序。

    recursive 为真时最多下钻 depth 层，避免把整棵仓库扫一遍。
    """
    if not os.path.isdir(root):
        return []
    base = root.rstrip(os.sep).count(os.sep)
    files = []
    if not recursive:
        for name in os.listdir(root):
            full = os.path.join(root, name)
            if os.path.isfile(full) and os.path.splitext(name)[1].lower() in CONTENT_SUFFIX:
                st = os.stat(full)
                files.append(
                    {"name": name, "rel": name, "path": full, "size": st.st_size,
                     "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")}
                )
    else:
        for dirpath, dirnames, filenames in os.walk(root):
            level = dirpath.rstrip(os.sep).count(os.sep) - base
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and level < depth)
            for name in filenames:
                if os.path.splitext(name)[1].lower() not in CONTENT_SUFFIX:
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root)
                st = os.stat(full)
                files.append(
                    {"name": name, "rel": rel, "path": full, "size": st.st_size,
                     "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")}
                )
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return files


def content_status(root, recursive=False, depth=2):
    if not os.path.isdir(root):
        return {"exists": False, "file_count": 0, "latest": "", "error": "目录不存在"}
    files = content_files(root, recursive, depth)
    return {
        "exists": True,
        "file_count": len(files),
        "latest": files[0]["mtime"] if files else "",
        "error": "" if files else "目录是空的",
    }


def app_status(app):
    key = app.get("key") or app.get("name") or ""
    now = time.time()
    hit = _app_cache.get(key)
    if hit and now - hit[0] < APP_CACHE_TTL:
        return hit[1]

    out = dict(app)
    if app.get("type") == "service":
        live = 0
        for p in app.get("ports", []):
            if port_open(p):
                live = p
                break
        out["online"] = bool(live)
        out["url"] = f"http://127.0.0.1:{live}/" if live else ""
        out["state"] = "offline"
        out["health_code"] = 0
        out["health_error"] = ""
        if live:
            out["state"] = "online"
            path = (app.get("health") or "").lstrip("/")
            if path:
                code, err = http_status(urljoin(out["url"], path))
                out["health_code"] = code
                out["health_error"] = err
                if not 200 <= code < 400:
                    out["state"] = "degraded"
    elif app.get("type") == "content":
        out.update(content_status(app_path(app), app.get("recursive"), app.get("depth", 2)))

    _app_cache[key] = (now, out)
    return out


def safe_path(raw):
    """把请求路径限制在 ALLOW_ROOTS 之内，越界返回 None。"""
    if not raw:
        return None
    path = os.path.realpath(unquote(raw))
    for root in ALLOW_ROOTS:
        real_root = os.path.realpath(root)
        if path == real_root or path.startswith(real_root + os.sep):
            return path
    return None


def static_path(raw):
    """静态文件只允许在 STATIC_DIR 内，路径穿越直接拒绝。"""
    target = os.path.realpath(os.path.join(STATIC_DIR, unquote(raw)))
    root = os.path.realpath(STATIC_DIR)
    if target != root and not target.startswith(root + os.sep):
        return None
    return target if os.path.isfile(target) else None


def content_roots():
    """内容阅读器允许访问的目录，只来自 apps.json 的 content 应用。

    不能沿用 ALLOW_ROOTS：那个范围包含整个 ~/.hermes，会让
    /api/file 读到 auth.json 这类含凭据的文件。
    """
    roots = []
    for app in load_apps():
        if app.get("type") == "content" and app.get("path"):
            roots.append(app_path(app))
    return roots


def in_roots(path, roots):
    return any(path == root or path.startswith(root + os.sep) for root in roots)


def int_param(qs, key, default, low, high):
    try:
        value = int((qs.get(key) or [str(default)])[0])
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def float_param(qs, key):
    raw = (qs.get(key) or [""])[0]
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "HermesHub/2.0"

    def log_message(self, fmt, *args):
        if os.environ.get("HUB_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def host_ok(self):
        host = (self.headers.get("Host") or "").strip().lower()
        return bool(HOST_RE.match(host))

    def send_json(self, obj, code=200):
        body = json_bytes(obj)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        ext = os.path.splitext(path)[1].lower()
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".json": "application/json; charset=utf-8",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(ext, "application/octet-stream")
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            self.send_json({"error": "文件不存在"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.host_ok():
            return self.send_json({"error": "拒绝非本机访问"}, 403)
        try:
            self.route_get()
        except Exception as exc:  # 单次请求出错不能拖垮服务
            self.send_json({"error": str(exc)}, 500)

    def do_POST(self):
        if not self.host_ok():
            return self.send_json({"error": "拒绝非本机访问"}, 403)
        try:
            self.route_post()
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def route_get(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            return self.send_file(os.path.join(STATIC_DIR, "index.html"))
        if path.startswith("/static/"):
            target = static_path(path[len("/static/"):])
            if not target:
                return self.send_json({"error": "未找到"}, 404)
            return self.send_file(target)
        if path == "/health":
            return self.send_json({"ok": True, "time": datetime.now().isoformat(timespec="seconds")})

        if path == "/api/messages":
            return self.api_messages(qs)
        if path == "/api/overview":
            return self.api_overview()
        if path == "/api/apps":
            return self.send_json({"apps": [app_status(a) for a in load_apps()]})
        if path.startswith("/api/apps/") and path.endswith("/files"):
            key = path[len("/api/apps/"):-len("/files")]
            return self.api_app_files(key)
        if path == "/api/file":
            return self.api_file(qs)
        if path == "/media":
            target = safe_path((qs.get("path") or [""])[0])
            if not target or not os.path.isfile(target):
                return self.send_json({"error": "媒体已失效"}, 404)
            if os.path.splitext(target)[1].lower() not in MEDIA_SUFFIX:
                return self.send_json({"error": "不支持的媒体类型"}, 403)
            return self.send_file(target)

        return self.send_json({"error": "未找到"}, 404)

    def route_post(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if parsed.path == "/api/read":
            payload = {}
            if raw:
                try:
                    payload = json.loads(raw)
                except ValueError:
                    payload = {}
            job_id = (payload.get("job") or "").strip() if isinstance(payload, dict) else ""
            with _lock:
                conn = hub.connect()
                try:
                    hub.mark_read(conn, job_id)
                    unread = hub.unread_count(conn, job_id)
                    total_unread = hub.unread_count(conn)
                finally:
                    conn.close()
            return self.send_json({"ok": True, "job": job_id, "unread": unread, "total_unread": total_unread})
        if parsed.path == "/api/sync":
            added = run_sync()
            conn = hub.connect()
            try:
                return self.send_json({"ok": True, "added": added, "stats": hub.stats(conn)})
            finally:
                conn.close()
        return self.send_json({"error": "未找到"}, 404)

    def api_messages(self, qs):
        page = int_param(qs, "page", 1, 1, 100000)
        size = int_param(qs, "size", 30, 1, 100)
        job_id = (qs.get("job") or [""])[0]
        q = (qs.get("q") or [""])[0].strip()
        unread = (qs.get("unread") or ["0"])[0] == "1"
        silent = (qs.get("silent") or ["0"])[0] == "1"
        until = float_param(qs, "until")
        anchor = (qs.get("anchor") or [""])[0]
        conn = hub.connect()
        try:
            data = hub.query_messages(
                conn, page=page, size=size, job_id=job_id, q=q, only_unread=unread,
                include_silent=silent, until=until, anchor=anchor,
            )
            data["stats"] = hub.stats(conn)
            return self.send_json(data)
        finally:
            conn.close()

    def api_overview(self):
        conn = hub.connect()
        try:
            jobs = hub.job_health(conn)
            return self.send_json(
                {
                    "stats": hub.stats(conn),
                    "jobs": jobs,
                    "health": hub.health_summary(jobs),
                    "apps": [app_status(a) for a in load_apps()],
                }
            )
        finally:
            conn.close()

    def api_app_files(self, key):
        app = next((a for a in load_apps() if a.get("key") == key), None)
        if not app or app.get("type") != "content":
            return self.send_json({"error": "未找到该应用"}, 404)
        root = app_path(app)
        if not os.path.isdir(root):
            return self.send_json({"files": [], "error": "目录不存在"}, 404)
        files = content_files(root, app.get("recursive"), app.get("depth", 2))
        return self.send_json({"files": files, "error": "" if files else "目录是空的"})

    def api_file(self, qs):
        target = safe_path((qs.get("path") or [""])[0])
        if not target or not os.path.isfile(target):
            return self.send_json({"error": "文件不存在"}, 404)
        if os.path.splitext(target)[1].lower() not in CONTENT_SUFFIX:
            return self.send_json({"error": "不支持的文件类型"}, 403)
        if not in_roots(target, content_roots()):
            return self.send_json({"error": "不在可读目录内"}, 403)
        with open(target, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        return self.send_json({"name": os.path.basename(target), "content": text})


def run_sync():
    with _lock:
        conn = hub.connect()
        try:
            return hub.sync(conn)
        finally:
            conn.close()


def sync_loop():
    while True:
        try:
            added = run_sync()
            if added:
                sys.stderr.write(f"[hub] 新增 {added} 条消息\n")
        except Exception as exc:  # 同步失败不能让服务停摆
            sys.stderr.write(f"[hub] 同步失败: {exc}\n")
        time.sleep(SYNC_INTERVAL)


def pick_port():
    for port in range(PORT_START, PORT_START + PORT_TRIES):
        if not port_open(port):
            return port
    raise SystemExit("端口全部被占用")


def remove_state_if_ours(pid):
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if int(data.get("pid") or 0) == pid:
            os.remove(STATE_FILE)
    except (OSError, ValueError, TypeError):
        pass


def main():
    added = run_sync()
    port = pick_port()
    server = ThreadingHTTPServer((HOST, port), Handler)
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump({"port": port, "pid": os.getpid()}, fh)

    def stop(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    threading.Thread(target=sync_loop, daemon=True).start()
    conn = hub.connect()
    try:
        st = hub.stats(conn)
    finally:
        conn.close()
    sys.stderr.write(
        f"[hub] 已入库 {st['total']} 条（本次新增 {added}），未读 {st['unread']}，"
        f"http://{HOST}:{port}/\n"
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        remove_state_if_ours(os.getpid())


if __name__ == "__main__":
    main()
