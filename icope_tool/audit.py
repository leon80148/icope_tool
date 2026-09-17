"""稽核日誌（每台電腦各自一份，不放共用資料夾）。

只記事件與身分證雜湊前 8 碼；絕不記身分證明文、姓名、密碼。
"""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime
from pathlib import Path

MAX_BYTES = 1024 * 1024


def pid_tag(person_id: str) -> str:
    return hashlib.sha256(person_id.strip().upper().encode("utf-8")).hexdigest()[:8]


class AuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def record(self, event: str, **fields) -> None:
        detail = " ".join(f"{key}={value}" for key, value in fields.items())
        line = f"{datetime.now().isoformat(timespec='seconds')}\t{event}\t{detail}\n"
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                    backup = self.path.with_suffix(self.path.suffix + ".1")
                    backup.unlink(missing_ok=True)
                    self.path.rename(backup)
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line)
            except OSError:
                pass   # 稽核寫不進去不應讓查詢失敗
