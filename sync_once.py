#!/usr/bin/env python3
"""给 launchd 用的单次同步入口。

页面关着的时候也要把 cron 输出落库，否则 r/pics 这类每 4 分钟一条的任务
三个多小时之后就被 Hermes 的 50 条上限滚掉了。
新增条数才输出一行到 logs/sync.log，没新增时完全安静。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hub  # noqa: E402


def main():
    conn = hub.connect()
    try:
        added = hub.sync(conn)
        st = hub.stats(conn)
    finally:
        conn.close()
    if added:
        print(f"[hub-sync] 新增 {added} 条，库内共 {st['total']} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
