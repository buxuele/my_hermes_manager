"""总台的数据层：把 Hermes 定时任务的输出沉淀成一条不会丢的消息流。

## 为什么需要这一层

Hermes 自己的 cron 输出目录只保留每个任务的最近 50 条
（上游 cron/jobs.py 的 _CRON_OUTPUT_DEFAULT_KEEP = 50）。
r/pics 每 4 分钟跑一次，50 条只够 3 个多小时，用户来不及读就没了。
执行记录 executions.db 也只保留 1000 条，一天多就滚完。

所以这里做三件事：
1. 扫描 cron/output，把每条消息写进自己的 SQLite，永不删除；
2. 用 (job_id, run_at) 做唯一键，重复扫描不会产生重复；
3. 维护全局已读水位与任务级已读水位，用来算「还有多少条没读」。

只读 Hermes 的目录，不写、不改、不删任何 Hermes 文件。
"""

import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import datetime

CRON_DIR = os.path.expanduser("~/.hermes/cron")
OUTPUT_DIR = os.path.join(CRON_DIR, "output")
JOBS_FILE = os.path.join(CRON_DIR, "jobs.json")
EXEC_DB = os.path.join(CRON_DIR, "executions.db")

HERE = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(HERE, "hub.db")
APPS_FILE = os.path.join(HERE, "apps.json")
ARCHIVE_DIR = os.path.join(HERE, "media_archive")
ARCHIVE_SUFFIX = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".md"}

HEADER_JOB = re.compile(r"^#\s*Cron Job:\s*(.+?)\s*$", re.M)
HEADER_KV = re.compile(r"^\*\*([A-Za-z ]+):\*\*\s*(.*?)\s*$", re.M)
MEDIA_LINE = re.compile(r"^MEDIA:\s*(\S+)\s*$", re.M)

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL,
    job_name    TEXT NOT NULL,
    run_at      TEXT NOT NULL,
    run_ts      REAL NOT NULL,
    mode        TEXT,
    silent      INTEGER NOT NULL DEFAULT 0,
    body        TEXT NOT NULL DEFAULT '',
    media       TEXT NOT NULL DEFAULT '[]',
    source_path TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(run_ts DESC);
CREATE INDEX IF NOT EXISTS idx_msg_job ON messages(job_id, run_ts DESC);

CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS scanned_files (
    path  TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    size  INTEGER NOT NULL
);
"""


def connect(path=None):
    path = path or DB_FILE
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


def parse_ts(text):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    return None


def parse_output(path):
    """解析一个 cron 输出文件，返回消息字典；解析不出时间就返回 None。"""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()

    m_job = HEADER_JOB.search(raw)
    meta = {k.strip().lower(): v for k, v in HEADER_KV.findall(raw)}

    run_at = meta.get("run time") or ""
    run_ts = parse_ts(run_at)
    if run_ts is None:
        stem = os.path.basename(path)[:-3]
        try:
            run_ts = datetime.strptime(stem, "%Y-%m-%d_%H-%M-%S").timestamp()
            run_at = datetime.fromtimestamp(run_ts).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    # 正文在第一个单独一行的 --- 之后
    parts = raw.split("\n---\n", 1)
    body = parts[1].strip() if len(parts) == 2 else ""
    media = MEDIA_LINE.findall(body)
    body = MEDIA_LINE.sub("", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()

    status = (meta.get("status") or "").lower()
    silent = 0 if body else 1
    if "silent" in status or "empty output" in status:
        silent = 1

    job_id = meta.get("job id") or os.path.basename(os.path.dirname(path))
    job_name = m_job.group(1) if m_job else job_id
    digest = hashlib.sha1(f"{job_id}|{run_at}".encode()).hexdigest()[:16]

    return {
        "id": digest,
        "job_id": job_id,
        "job_name": job_name,
        "run_at": run_at,
        "run_ts": run_ts,
        "mode": meta.get("mode") or "",
        "silent": silent,
        "body": body,
        "media": json.dumps(media, ensure_ascii=False),
        "source_path": path,
    }


def archive_paths(job_id, media):
    """把消息引用的图片硬链接进 media_archive，返回归档后的路径列表。

    备货池投递完 6 小时就会删图，硬链接让总台自己留一份，不占额外空间。
    知乎日报的附件是导出的 markdown 文章，也一起归档。
    文件已经不存在的保留原路径，前端如实显示「图片已失效」。
    """
    out = []
    for raw in media:
        if not raw or raw.startswith(ARCHIVE_DIR):
            out.append(raw)
            continue
        if not os.path.isfile(raw) or os.path.splitext(raw)[1].lower() not in ARCHIVE_SUFFIX:
            out.append(raw)
            continue
        dest_dir = os.path.join(ARCHIVE_DIR, job_id)
        dest = os.path.join(dest_dir, os.path.basename(raw))
        try:
            if not os.path.exists(dest):
                os.makedirs(dest_dir, exist_ok=True)
                os.link(raw, dest)
            out.append(dest)
        except OSError:
            out.append(raw)
    return out


def archive_backfill(conn):
    """升级后的一次性迁移：把库里还能救的图归档，然后写标记不再扫。"""
    if get_state(conn, "archive_backfilled"):
        return 0
    fixed = 0
    for row in conn.execute("SELECT id, job_id, media FROM messages WHERE media != '[]'"):
        media = json.loads(row["media"])
        new = archive_paths(row["job_id"], media)
        if new != media:
            conn.execute(
                "UPDATE messages SET media = ? WHERE id = ?",
                (json.dumps(new, ensure_ascii=False), row["id"]),
            )
            fixed += 1
    set_state(conn, "archive_backfilled", 1)
    conn.commit()
    return fixed


def iter_output_files(output_dir=OUTPUT_DIR):
    if not os.path.isdir(output_dir):
        return
    for job_dir in sorted(os.listdir(output_dir)):
        full = os.path.join(output_dir, job_dir)
        if not os.path.isdir(full):
            continue
        for name in sorted(os.listdir(full)):
            if name.endswith(".md"):
                yield os.path.join(full, name)


def sync(conn=None, output_dir=OUTPUT_DIR, force=False):
    """增量扫描并入库存。返回新增条数。

    每个文件记下 mtime 与大小，没变就跳过解析。服务每 30 秒扫一次，
    库里存了几万条以后也不会因为反复解析全量文件而变慢。
    库是空的或 force=True 时忽略指纹，全量重建。
    """
    own = conn is None
    conn = conn or connect()
    try:
        count = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
        marks = {}
        if not force and count:
            marks = {
                r["path"]: (r["mtime"], r["size"])
                for r in conn.execute("SELECT path, mtime, size FROM scanned_files")
            }
        known = {(r["job_id"], r["run_at"]) for r in conn.execute("SELECT job_id, run_at FROM messages")}
        added = 0
        seen = set()
        for path in iter_output_files(output_dir):
            seen.add(path)
            try:
                st = os.stat(path)
            except OSError:
                continue
            sig = (st.st_mtime, float(st.st_size))
            if marks.get(path) == sig:
                continue
            msg = parse_output(path)
            if not msg:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO scanned_files(path, mtime, size) VALUES(?, ?, ?)",
                (path, sig[0], st.st_size),
            )
            if (msg["job_id"], msg["run_at"]) in known:
                continue
            if msg["media"] != "[]":
                msg["media"] = json.dumps(
                    archive_paths(msg["job_id"], json.loads(msg["media"])), ensure_ascii=False
                )
            conn.execute(
                """INSERT OR IGNORE INTO messages
                   (id, job_id, job_name, run_at, run_ts, mode, silent, body, media, source_path)
                   VALUES (:id, :job_id, :job_name, :run_at, :run_ts, :mode, :silent, :body, :media, :source_path)""",
                msg,
            )
            known.add((msg["job_id"], msg["run_at"]))
            added += 1
        if marks and os.path.isdir(output_dir):
            stale = [(p,) for p in marks if p not in seen]
            if stale:
                conn.executemany("DELETE FROM scanned_files WHERE path = ?", stale)
        archive_backfill(conn)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES('last_sync', ?)", (stamp,))
        conn.commit()
        return added
    finally:
        if own:
            conn.close()


def get_state(conn, key, default=None):
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES(?, ?)", (key, str(value)))
    conn.commit()


def read_watermark(conn, job_id=""):
    """已读水位。全局水位覆盖所有任务，任务水位只影响该任务。"""
    marks = []
    global_mark = get_state(conn, "read_upto_ts", "") or ""
    if global_mark:
        try:
            marks.append(float(global_mark))
        except ValueError:
            pass
    if job_id:
        job_mark = get_state(conn, "read_job_" + job_id, "") or ""
        if job_mark:
            try:
                marks.append(float(job_mark))
            except ValueError:
                pass
    return max(marks) if marks else 0.0


def unread_count(conn, job_id=""):
    if job_id:
        mark = read_watermark(conn, job_id)
        row = conn.execute(
            "SELECT COUNT(*) c FROM messages WHERE silent = 0 AND job_id = ? AND run_ts > ?",
            (job_id, mark),
        ).fetchone()
        return row["c"]
    row = conn.execute(
        """
        SELECT COUNT(*) c FROM messages m
        WHERE m.silent = 0 AND m.run_ts > (
            SELECT MAX(
                COALESCE((SELECT CAST(value AS REAL) FROM state WHERE key = 'read_upto_ts'), 0),
                COALESCE((SELECT CAST(value AS REAL) FROM state WHERE key = 'read_job_' || m.job_id), 0)
            )
        )
        """
    ).fetchone()
    return row["c"]



def mark_read(conn, job_id=""):
    """把全部消息或单个任务标为已读，返回新的水位。"""
    if job_id:
        row = conn.execute("SELECT MAX(run_ts) m FROM messages WHERE job_id = ?", (job_id,)).fetchone()
        newest = row["m"] if row and row["m"] is not None else 0
        set_state(conn, "read_job_" + job_id, newest)
        return newest
    row = conn.execute("SELECT MAX(run_ts) m FROM messages").fetchone()
    newest = row["m"] if row and row["m"] is not None else 0
    set_state(conn, "read_upto_ts", newest)
    return newest


def mark_all_read(conn):
    return mark_read(conn)


def list_jobs_file(path=None):
    path = path or JOBS_FILE
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    jobs = data.get("jobs", []) if isinstance(data, dict) else data
    out = []
    for j in jobs:
        sched = j.get("schedule") or {}
        out.append(
            {
                "id": j.get("id"),
                "name": j.get("name"),
                "schedule": sched.get("display") or sched.get("expr") or "",
                "schedule_kind": sched.get("kind") or "",
                "schedule_minutes": sched.get("minutes"),
                "schedule_expr": sched.get("expr") or "",
                "enabled": bool(j.get("enabled")),
                "paused_reason": j.get("paused_reason") or "",
                "deliver": j.get("deliver") or "",
                "script": j.get("script") or "",
                "no_agent": bool(j.get("no_agent")),
                "last_run_at": j.get("last_run_at") or "",
                "last_status": j.get("last_status") or "",
                "last_error": j.get("last_error") or "",
                "next_run_at": j.get("next_run_at") or "",
                "failure_streak": j.get("failure_streak") or 0,
                "completed": (j.get("repeat") or {}).get("completed") or 0,
            }
        )
    return out


def schedule_seconds(job):
    """只解析间隔型任务；cron 表达式交给 next_run_at 兜底。"""
    if job.get("schedule_kind") == "interval":
        minutes = job.get("schedule_minutes")
        if minutes:
            return float(minutes) * 60
    return None


def parse_iso(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def quiet_exempt():
    """apps.json 里声明可以长期静默的任务，不参与「久无产出」判定。"""
    try:
        mtime = os.stat(APPS_FILE).st_mtime
    except OSError:
        return set()
    if _EXEMPT_CACHE["mtime"] == mtime:
        return _EXEMPT_CACHE["ids"]
    try:
        with open(APPS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        ids = {str(x) for x in (data.get("quiet_exempt") or [])}
    except (OSError, ValueError):
        ids = set()
    _EXEMPT_CACHE.update(mtime=mtime, ids=ids)
    return ids


_EXEMPT_CACHE = {"mtime": None, "ids": set()}


def _job_status(job, agg, now, exempt=None):
    """ok 正常，error 故障，stalled 停摆，quiet 久无产出，paused 已暂停，nofile 无留档。"""
    if not job.get("enabled"):
        return "paused"
    if job.get("failure_streak", 0) > 0 or (job.get("last_status") and job.get("last_status") != "ok"):
        return "error"
    interval = schedule_seconds(job)
    last_run = parse_iso(job.get("last_run_at"))
    next_run = parse_iso(job.get("next_run_at"))
    if interval and last_run and now - last_run > max(interval * 2.5, 900):
        return "stalled"
    if next_run and now - next_run > max(900, (interval or 0) * 1.5):
        return "stalled"
    if not agg.get("total"):
        return "nofile"
    last_content = agg.get("last_content_ts")
    if (
        job.get("id") not in (exempt or set())
        and interval
        and interval <= 21600
        and agg.get("content7", 0) >= 10
        and not agg.get("content24", 0)
        and last_content
        and now - last_content > max(interval * 4, 21600)
    ):
        return "quiet"
    return "ok"


def _fmt_ts(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""


def job_health(conn, jobs_file=None, now=None):
    """任务健康度：调度、运行状态、24 小时产出、最近一次错误。

    状态判定只用真实数据：jobs.json 的 last_run_at / next_run_at / failure_streak，
    加上本库入库消息的产出时间。没有留档就如实标 nofile，不猜。
    """
    now = now if now is not None else time.time()
    since = now - 86400
    since7 = now - 7 * 86400
    rows = conn.execute(
        """
        SELECT job_id,
               MAX(job_name) job_name,
               COUNT(*) total,
               SUM(CASE WHEN silent=0 THEN 1 ELSE 0 END) content,
               MAX(run_ts) last_ts,
               MAX(CASE WHEN silent=0 THEN run_ts END) last_content_ts,
               SUM(CASE WHEN run_ts >= ? THEN 1 ELSE 0 END) runs24,
               SUM(CASE WHEN run_ts >= ? AND silent=0 THEN 1 ELSE 0 END) content24,
               SUM(CASE WHEN run_ts >= ? AND silent=0 THEN 1 ELSE 0 END) content7
        FROM messages GROUP BY job_id
        """,
        (since, since, since7),
    ).fetchall()
    aggs = {r["job_id"]: dict(r) for r in rows}
    exempt = quiet_exempt()
    result = []
    for job in list_jobs_file(jobs_file):
        agg = aggs.get(job["id"]) or {
            "job_name": job.get("name"), "total": 0, "content": 0, "last_ts": None,
            "last_content_ts": None, "runs24": 0, "content24": 0, "content7": 0,
        }
        status = _job_status(job, agg, now, exempt)
        last_run = parse_iso(job.get("last_run_at"))
        next_run = parse_iso(job.get("next_run_at"))
        result.append(
            {
                **job,
                "status": status,
                "archived": agg["total"],
                "content": agg["content"],
                "unread": unread_count(conn, job["id"]),
                "last_message_at": _fmt_ts(agg["last_ts"]),
                "last_content_at": _fmt_ts(agg["last_content_ts"]),
                "runs24": agg["runs24"],
                "content24": agg["content24"],
                "content7": agg["content7"],
                "last_run_ts": last_run,
                "last_ts": agg["last_ts"],
                "next_run_ts": next_run,
                "interval_seconds": schedule_seconds(job),
                "silent": max(0, agg["total"] - agg["content"]),
            }
        )
    known = {j["id"] for j in result}
    for jid, agg in aggs.items():
        if jid in known:
            continue
        result.append(
            {
                "id": jid,
                "name": agg.get("job_name") or jid,
                "schedule": "",
                "schedule_kind": "",
                "schedule_minutes": None,
                "schedule_expr": "",
                "enabled": False,
                "paused_reason": "",
                "deliver": "",
                "script": "",
                "no_agent": False,
                "last_run_at": "",
                "last_status": "",
                "last_error": "",
                "next_run_at": "",
                "failure_streak": 0,
                "completed": 0,
                "status": "orphan",
                "archived": agg["total"],
                "content": agg["content"],
                "unread": unread_count(conn, jid),
                "last_message_at": _fmt_ts(agg["last_ts"]),
                "last_content_at": _fmt_ts(agg["last_content_ts"]),
                "runs24": agg["runs24"],
                "content24": agg["content24"],
                "content7": agg["content7"],
                "last_run_ts": None,
                "last_ts": agg["last_ts"],
                "next_run_ts": None,
                "interval_seconds": None,
                "silent": max(0, agg["total"] - agg["content"]),
            }
        )
    # 异常的排最前，已删除的排最后；同一状态按最近动静排序。
    order = {"error": 0, "stalled": 1, "quiet": 2, "ok": 3, "paused": 4, "nofile": 5, "orphan": 6}
    result.sort(key=lambda j: (order.get(j["status"], 9), -(j["last_run_ts"] or j["last_ts"] or 0)))
    return result


def health_summary(jobs):
    out = {"total": len(jobs), "ok": 0, "error": 0, "stalled": 0, "quiet": 0,
           "paused": 0, "nofile": 0, "orphan": 0}
    for j in jobs:
        key = j.get("status") or "ok"
        out[key] = out.get(key, 0) + 1
    out["attention"] = out["error"] + out["stalled"] + out["quiet"]
    return out


def like_pattern(text):
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def query_messages(
    conn, page=1, size=30, job_id="", q="", only_unread=False, include_silent=False,
    until=None, anchor="",
):
    base = []
    params = []
    if not include_silent:
        base.append("silent = 0")
    if job_id:
        base.append("job_id = ?")
        params.append(job_id)
    if q:
        base.append("(body LIKE ? ESCAPE '\\' OR job_name LIKE ? ESCAPE '\\')")
        pat = like_pattern(q)
        params.extend([pat, pat])

    scope = list(base)
    scope_params = list(params)
    if until:
        scope.append("run_ts <= ?")
        scope_params.append(float(until))
    scope_clause = (" WHERE " + " AND ".join(scope)) if scope else ""

    silent_where = []
    silent_params = []
    if job_id:
        silent_where.append("job_id = ?")
        silent_params.append(job_id)
    if q:
        silent_where.append("(body LIKE ? ESCAPE '\\' OR job_name LIKE ? ESCAPE '\\')")
        pat = like_pattern(q)
        silent_params.extend([pat, pat])
    if until:
        silent_where.append("run_ts <= ?")
        silent_params.append(float(until))
    silent_clause = " WHERE " + " AND ".join(silent_where + ["silent = 1"])
    silent_total = conn.execute(
        f"SELECT COUNT(*) c FROM messages{silent_clause}", silent_params
    ).fetchone()["c"]

    mark = read_watermark(conn, job_id)
    global_mark = read_watermark(conn)
    job_marks = {}
    for row in conn.execute("SELECT key, value FROM state WHERE key LIKE 'read_job_%'"):
        try:
            job_marks[row["key"][len("read_job_"):]] = float(row["value"])
        except ValueError:
            continue
    total = conn.execute(
        f"SELECT COUNT(*) c FROM messages{scope_clause}", scope_params
    ).fetchone()["c"]
    size = max(1, min(int(size), 100))
    pages = max(1, (total + size - 1) // size)
    page = max(1, min(int(page), pages))

    if anchor:
        row = conn.execute("SELECT run_ts FROM messages WHERE id = ?", (anchor,)).fetchone()
        if row:
            newer = conn.execute(
                f"SELECT COUNT(*) c FROM messages{scope_clause}"
                + (" AND " if scope_clause else " WHERE ")
                + "run_ts > ?",
                scope_params + [row["run_ts"]],
            ).fetchone()["c"]
            page = max(1, min(newer // size + 1, pages))

    list_where = list(scope)
    list_params = list(scope_params)
    if only_unread:
        if job_id:
            list_where.append("run_ts > ?")
            list_params.append(mark)
        else:
            list_where.append(
                "run_ts > (SELECT MAX("
                "COALESCE((SELECT CAST(value AS REAL) FROM state WHERE key = 'read_upto_ts'), 0), "
                "COALESCE((SELECT CAST(value AS REAL) FROM state WHERE key = 'read_job_' || messages.job_id), 0)"
                "))"
            )
    clause = (" WHERE " + " AND ".join(list_where)) if list_where else ""

    total = conn.execute(f"SELECT COUNT(*) c FROM messages{clause}", list_params).fetchone()["c"]
    pages = max(1, (total + size - 1) // size)
    page = max(1, min(page, pages))
    offset = (page - 1) * size
    rows = conn.execute(
        f"SELECT * FROM messages{clause} ORDER BY run_ts DESC, id DESC LIMIT ? OFFSET ?",
        list_params + [size, offset],
    ).fetchall()

    items = []
    for r in rows:
        media = json.loads(r["media"] or "[]")
        items.append(
            {
                "id": r["id"],
                "job_id": r["job_id"],
                "job_name": r["job_name"],
                "run_at": r["run_at"],
                "run_ts": r["run_ts"],
                "mode": r["mode"],
                "silent": bool(r["silent"]),
                "body": r["body"],
                "media": media,
                "media_missing": [not os.path.isfile(p) for p in media],
                "unread": (not r["silent"]) and r["run_ts"] > max(global_mark, job_marks.get(r["job_id"], 0)),
            }
        )
    return {
        "items": items, "total": total, "page": page, "size": size, "pages": pages,
        "silent_total": silent_total, "until": until,
    }


def stats(conn):
    total = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
    content = conn.execute("SELECT COUNT(*) c FROM messages WHERE silent=0").fetchone()["c"]
    jobs = conn.execute("SELECT COUNT(DISTINCT job_id) c FROM messages").fetchone()["c"]
    return {
        "total": total,
        "content": content,
        "jobs": jobs,
        "unread": unread_count(conn),
        "last_sync": get_state(conn, "last_sync", ""),
        "first_at": (conn.execute("SELECT MIN(run_at) v FROM messages").fetchone()["v"] or ""),
        "newest_at": (conn.execute("SELECT MAX(run_at) v FROM messages").fetchone()["v"] or ""),
    }
