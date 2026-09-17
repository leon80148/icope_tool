"""hpdcs 帳密保存於 data/auth/hpdcs_credentials.json。

安全取捨（沿用參考專案的結論）：檔案為明文 JSON。在同一台電腦上加密時金鑰也得放在同處，
只是障眼法；Windows DPAPI 又無法讓共用資料夾的其他電腦解密。真正的安全邊界是
「資料夾的存取權限」，INSTALL.md 會要求共用資料夾只開放給櫃檯／護理站帳號。
密碼絕不出現在畫面、日誌或設定包中。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from icope_tool.store import atomic_write_bytes


def mask_account(account: str) -> str:
    """遮罩帳號供顯示：前 3 碼 + ***（過短則首碼 + ***）。"""
    if not account:
        return ""
    if len(account) <= 3:
        return account[0] + "***"
    return account[:3] + "***"


class CredentialStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def get(self) -> tuple[str, str] | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        account = str(data.get("account") or "").strip()
        password = str(data.get("password") or "")
        if account and password:
            return account, password
        return None

    def is_configured(self) -> bool:
        return self.get() is not None

    def masked_account(self) -> str:
        creds = self.get()
        return mask_account(creds[0]) if creds else ""

    def save(self, account: str, password: str) -> None:
        account = (account or "").strip()
        if not account or not password:
            raise ValueError("帳號與密碼都要填寫")
        payload = json.dumps({"account": account, "password": password}, ensure_ascii=False)
        atomic_write_bytes(self.path, payload.encode("utf-8"))
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
