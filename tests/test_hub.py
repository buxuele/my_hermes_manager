"""总台数据层的自测：解析、去重、排序、已读、静默过滤。

跑法：python3 -m unittest discover -s tests -v
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hub  # noqa: E402

SAMPLE = """# Cron Job: r/pics 图片

**Job ID:** job-a
**Run Time:** 2026-10-27 00:46:02
**Mode:** no_agent (script)

---

**【r/pics】**

MEDIA:/tmp/a.jpeg

[1. 多伦多蓝调时刻](https://example.com/1)
"""

SILENT = """# Cron Job: 商机雷达

**Job ID:** job-b
**Run Time:** 2026-10-27 00:40:00
**Mode:** no_agent (script)
**Status:** silent (empty output)
"""


def add_message(conn, mid, job_id, name, ts, silent, body):
    run_at = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """INSERT INTO messages
           (id, job_id, job_name, run_at, run_ts, mode, silent, body, media, source_path)
           VALUES(?, ?, ?, ?, ?, '', ?, ?, '[]', 'test')""",
        (mid, job_id, name, run_at, ts, silent, body),
    )
    conn.commit()


def write(directory, job_id, name, text):
    d = os.path.join(directory, job_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
        fh.write(text)


class HubTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "output")
        write(self.out, "job-a", "2026-10-27_00-46-02.md", SAMPLE)
        write(self.out, "job-b", "2026-10-27_00-40-00.md", SILENT)
        write(
            self.out, "job-a", "2026-10-26_23-00-00.md",
            SAMPLE.replace("2026-10-27 00:46:02", "2026-10-26 23:00:00").replace("job-a", "job-a"),
        )
        self.db = os.path.join(self.tmp, "test.db")
        self.conn = hub.connect(self.db)

    def tearDown(self):
        self.conn.close()

    def test_parse_header_and_media(self):
        msg = hub.parse_output(os.path.join(self.out, "job-a", "2026-10-27_00-46-02.md"))
        self.assertEqual(msg["job_id"], "job-a")
        self.assertEqual(msg["job_name"], "r/pics 图片")
        self.assertEqual(msg["run_at"], "2026-10-27 00:46:02")
        self.assertEqual(msg["media"], '["/tmp/a.jpeg"]')
        self.assertNotIn("MEDIA:", msg["body"])
        self.assertEqual(msg["silent"], 0)

    def test_silent_detected(self):
        msg = hub.parse_output(os.path.join(self.out, "job-b", "2026-10-27_00-40-00.md"))
        self.assertEqual(msg["silent"], 1)
        self.assertEqual(msg["body"], "")

    def test_sync_is_idempotent(self):
        first = hub.sync(self.conn, self.out)
        second = hub.sync(self.conn, self.out)
        self.assertEqual(first, 3)
        self.assertEqual(second, 0)

    def test_newest_first_and_paging(self):
        hub.sync(self.conn, self.out)
        page = hub.query_messages(self.conn, page=1, size=1)
        self.assertEqual(page["total"], 2)          # 静默默认不显示
        self.assertEqual(page["pages"], 2)
        self.assertEqual(page["items"][0]["run_at"], "2026-10-27 00:46:02")

        with_silent = hub.query_messages(self.conn, page=1, size=10, include_silent=True)
        self.assertEqual(with_silent["total"], 3)
        self.assertEqual(with_silent["items"][0]["job_id"], "job-a")

    def test_unread_then_mark_all(self):
        hub.sync(self.conn, self.out)
        self.assertEqual(hub.unread_count(self.conn), 2)
        hub.mark_all_read(self.conn)
        self.assertEqual(hub.unread_count(self.conn), 0)

    def test_job_filter(self):
        hub.sync(self.conn, self.out)
        data = hub.query_messages(self.conn, job_id="job-a", size=10)
        self.assertEqual(data["total"], 2)
        self.assertTrue(all(m["job_id"] == "job-a" for m in data["items"]))

    def test_search(self):
        hub.sync(self.conn, self.out)
        data = hub.query_messages(self.conn, q="多伦多", size=10)
        self.assertEqual(data["total"], 2)

    def test_fingerprint_skips_unchanged_files(self):
        hub.sync(self.conn, self.out)
        marks = self.conn.execute("SELECT COUNT(*) c FROM scanned_files").fetchone()["c"]
        self.assertEqual(marks, 3)
        self.assertEqual(hub.sync(self.conn, self.out), 0)
        write(self.out, "job-a", "2026-10-27_01-00-00.md",
              SAMPLE.replace("2026-10-27 00:46:02", "2026-10-27 01:00:00"))
        self.assertEqual(hub.sync(self.conn, self.out), 1)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM scanned_files").fetchone()["c"], 4
        )

    def test_fingerprint_pruned_when_file_rotates(self):
        hub.sync(self.conn, self.out)
        os.remove(os.path.join(self.out, "job-b", "2026-10-27_00-40-00.md"))
        self.assertEqual(hub.sync(self.conn, self.out), 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM scanned_files").fetchone()["c"], 2
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"], 3
        )

    def test_job_health_status(self):
        jobs_file = os.path.join(self.tmp, "jobs.json")
        now = datetime(2026, 10, 28, 12, 0, 0).timestamp()
        jobs = [
            {"id": "job-a", "name": "正常任务", "enabled": True, "last_status": "ok",
             "last_run_at": "2026-10-28T11:58:00+08:00", "next_run_at": "2026-10-28T12:01:00+08:00",
             "schedule": {"kind": "interval", "minutes": 3, "display": "every 3m"}},
            {"id": "job-b", "name": "停摆任务", "enabled": True, "last_status": "ok",
             "last_run_at": "2026-10-28T06:00:00+08:00",
             "schedule": {"kind": "interval", "minutes": 3, "display": "every 3m"}},
            {"id": "job-c", "name": "故障任务", "enabled": True, "last_status": "failed",
             "failure_streak": 2, "last_error": "HTTP 500",
             "schedule": {"kind": "interval", "minutes": 60, "display": "every 60m"}},
            {"id": "job-d", "name": "暂停任务", "enabled": False,
             "schedule": {"kind": "interval", "minutes": 60, "display": "every 60m"}},
        ]
        with open(jobs_file, "w", encoding="utf-8") as fh:
            json.dump({"jobs": jobs}, fh)
        hub.sync(self.conn, self.out)
        health = hub.job_health(self.conn, jobs_file=jobs_file, now=now)
        got = {j["id"]: j["status"] for j in health}
        self.assertEqual(got["job-a"], "ok")
        self.assertEqual(got["job-b"], "stalled")
        self.assertEqual(got["job-c"], "error")
        self.assertEqual(got["job-d"], "paused")
        summary = hub.health_summary(health)
        self.assertEqual(summary["error"], 1)
        self.assertEqual(summary["stalled"], 1)
        self.assertEqual(summary["attention"], 2)

    def test_unread_per_job(self):
        hub.sync(self.conn, self.out)
        self.assertEqual(hub.unread_count(self.conn, "job-a"), 2)
        hub.mark_read(self.conn, "job-a")
        self.assertEqual(hub.unread_count(self.conn, "job-a"), 0)
        self.assertEqual(hub.unread_count(self.conn), 0)
        write(self.out, "job-a", "2026-10-27_02-00-00.md",
              SAMPLE.replace("2026-10-27 00:46:02", "2026-10-27 02:00:00"))
        hub.sync(self.conn, self.out)
        self.assertEqual(hub.unread_count(self.conn, "job-a"), 1)
        self.assertEqual(hub.unread_count(self.conn), 1)

    def test_search_wildcards_are_literal(self):
        hub.sync(self.conn, self.out)
        self.assertEqual(hub.query_messages(self.conn, q="%", size=10)["total"], 0)
        self.assertEqual(hub.query_messages(self.conn, q="_", size=10)["total"], 0)
        self.assertEqual(hub.query_messages(self.conn, q="多伦多", size=10)["total"], 2)

    def test_anchor_locates_page(self):
        hub.sync(self.conn, self.out)
        items = hub.query_messages(self.conn, size=10)["items"]
        anchor = items[1]["id"]
        data = hub.query_messages(self.conn, size=1, anchor=anchor)
        self.assertEqual(data["page"], 2)
        self.assertEqual(data["items"][0]["id"], anchor)

    def test_silent_total_reported(self):
        hub.sync(self.conn, self.out)
        data = hub.query_messages(self.conn, job_id="job-b", size=10)
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["silent_total"], 1)

    def test_orphan_keeps_name_and_content_time(self):
        jobs_file = os.path.join(self.tmp, "empty_jobs.json")
        with open(jobs_file, "w", encoding="utf-8") as fh:
            json.dump({"jobs": []}, fh)
        hub.sync(self.conn, self.out)
        health = hub.job_health(self.conn, jobs_file=jobs_file)
        got = {j["id"]: j for j in health}
        self.assertEqual(got["job-a"]["status"], "orphan")
        self.assertEqual(got["job-a"]["name"], "r/pics 图片")
        self.assertTrue(got["job-a"]["last_content_at"])
        self.assertEqual(got["job-a"]["unread"], 2)

    def test_cron_job_stalled_by_next_run(self):
        jobs_file = os.path.join(self.tmp, "cron_jobs.json")
        now = datetime(2026, 10, 28, 12, 0, 0).timestamp()
        jobs = [{
            "id": "job-cron", "name": "每日任务", "enabled": True, "last_status": "ok",
            "last_run_at": "2026-10-27T08:00:00+08:00",
            "next_run_at": "2026-10-27T08:00:00+08:00",
            "schedule": {"kind": "cron", "expr": "0 8 * * *", "display": "0 8 * * *"},
        }]
        with open(jobs_file, "w", encoding="utf-8") as fh:
            json.dump({"jobs": jobs}, fh)
        health = hub.job_health(self.conn, jobs_file=jobs_file, now=now)
        self.assertEqual(health[0]["status"], "stalled")

    def test_quiet_exempt(self):
        apps_file = os.path.join(self.tmp, "apps.json")
        with open(apps_file, "w", encoding="utf-8") as fh:
            json.dump({"quiet_exempt": ["job-quiet"]}, fh)
        old_apps = hub.APPS_FILE
        hub.APPS_FILE = apps_file
        hub._EXEMPT_CACHE = {"mtime": None, "ids": set()}
        try:
            now = datetime(2026, 10, 28, 12, 0, 0).timestamp()
            for n in range(12):
                ts = now - 2 * 86400 - n * 60
                add_message(self.conn, f"q{n}", "job-quiet", "快检", ts, 0, "有内容")
            jobs_file = os.path.join(self.tmp, "quiet_jobs.json")
            jobs = [{
                "id": "job-quiet", "name": "快检", "enabled": True, "last_status": "ok",
                "last_run_at": "2026-10-28T11:50:00+08:00",
                "next_run_at": "2026-10-28T12:20:00+08:00",
                "schedule": {"kind": "interval", "minutes": 15, "display": "every 15m"},
            }]
            with open(jobs_file, "w", encoding="utf-8") as fh:
                json.dump({"jobs": jobs}, fh)
            self.assertEqual(hub.job_health(self.conn, jobs_file=jobs_file, now=now)[0]["status"], "ok")
            hub.APPS_FILE = os.path.join(self.tmp, "no_apps.json")
            hub._EXEMPT_CACHE = {"mtime": None, "ids": set()}
            self.assertEqual(hub.job_health(self.conn, jobs_file=jobs_file, now=now)[0]["status"], "quiet")
        finally:
            hub.APPS_FILE = old_apps
            hub._EXEMPT_CACHE = {"mtime": None, "ids": set()}


if __name__ == "__main__":
    unittest.main()
