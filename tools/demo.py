"""Демо без CS2 и без Telegram.

    python -m tools.demo            # напечатать отчёт по смоделированному матчу
    python -m tools.demo --send URL TOKEN
                                    # отправить смоделированный матч на GSI-сервер бота,
                                    # как это делает CS2 (отчёт придёт в Telegram)
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request

from core.console_import import replay_gsi
from core.report import rounds_report, stat_report
from tests.sim import simulate
from tests.test_core import bad_match


def _plain(html: str) -> str:
    return re.sub(r"</?(b|i|code|pre)>", "", html)


def main() -> None:
    payloads = simulate(bad_match())
    if len(sys.argv) >= 4 and sys.argv[1] == "--send":
        url, token = sys.argv[2], sys.argv[3]
        for p in payloads:
            p["auth"] = {"token": token}
            req = urllib.request.Request(url, data=json.dumps(p).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5).read()
            time.sleep(0.02)
        print(f"Отправлено {len(payloads)} payload'ов — отчёт должен прийти в Telegram")
        return
    m = replay_gsi(payloads)
    print(_plain(stat_report(m)))
    print()
    print(_plain(rounds_report(m)))


if __name__ == "__main__":
    main()
