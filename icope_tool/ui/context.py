"""AppContext：整個視窗共用的狀態與服務。

- store / credentials / hpdcs client 都綁定目前的 data 資料夾；切換資料夾時整組重建
- 其他電腦改了共用資料夾時，定期比對檔案修改時間並發出變更 signal 讓各頁重新載入
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from icope_tool.audit import AuditLog
from icope_tool.models import HpdcsPrefs, LocalConfig
from icope_tool.services.hpdcs.client import HpdcsClient, IcopeResult
from icope_tool.services.hpdcs.credentials import CredentialStore
from icope_tool.services.hpdcs.plans import plan_codes
from icope_tool.store import DataStore, LocalConfigStore, StoreError


@dataclass
class PatientInfo:
    """目前處理中的長者（讀卡或從查詢結果帶到轉介單）。只存在記憶體。"""
    pid: str
    name: str = ""
    source: str = "card"          # card / query
    at: datetime = field(default_factory=datetime.now)


HISTORY_LIMIT = 20


@dataclass
class HistoryEntry:
    when: datetime
    person_id: str              # 只存在記憶體，關閉程式即消失
    name: str = ""
    result: IcopeResult | None = None
    error: str = ""
    plans: tuple[str, ...] = ()
    birth_roc: str = ""         # 讀卡時的出生日期（民國 YYYMMDD），用來核對年齡；手動輸入是空的。同樣只存在記憶體


def default_hpdcs_factory(ctx: "AppContext") -> HpdcsClient:
    return HpdcsClient(
        credentials_provider=ctx.credentials.get,
        plans_provider=ctx.active_plans,
        cookie_path=ctx.store.auth_dir / "hpdcs_cookies.json",
    )


class AppContext(QObject):
    settings_changed = Signal()
    resources_changed = Signal()
    materials_changed = Signal()
    hpdcs_changed = Signal()
    data_dir_changed = Signal()
    history_changed = Signal()
    patient_changed = Signal()
    navigate = Signal(str, str)          # (頁面, 子分頁)

    def __init__(self, local_store: LocalConfigStore, data_dir: Path, audit: AuditLog,
                 hpdcs_factory: Callable[["AppContext"], HpdcsClient] = default_hpdcs_factory,
                 today_fn: Callable[[], date] = date.today):
        super().__init__()
        self.local_store = local_store
        self.audit = audit
        self._hpdcs_factory = hpdcs_factory
        self._today = today_fn
        self.local: LocalConfig = local_store.load()
        self.current_patient: PatientInfo | None = None
        self.history: list[HistoryEntry] = []
        self.store: DataStore
        self.credentials: CredentialStore
        self.hpdcs: HpdcsClient
        self._signature: tuple[float, ...] = ()
        self._bind(Path(data_dir))

    # ------------------------------------------------------------------ 資料夾
    def _bind(self, data_dir: Path) -> None:
        self.store = DataStore(data_dir)
        self.credentials = CredentialStore(self.store.auth_dir / "hpdcs_credentials.json")
        self.hpdcs = self._hpdcs_factory(self)
        self._signature = self.store.signature()

    def switch_data_dir(self, data_dir: Path) -> bool:
        """改用另一個資料夾；回傳本機設定是否寫入成功（失敗時這次有效，下次開啟會回到舊位置）。"""
        self._bind(Path(data_dir))
        saved = self.update_local(data_dir=str(data_dir))
        self.data_dir_changed.emit()
        self.settings_changed.emit()
        self.resources_changed.emit()
        self.materials_changed.emit()
        self.hpdcs_changed.emit()
        return saved

    def check_external_changes(self) -> bool:
        signature = self.store.signature()
        if signature == self._signature:
            return False
        before = self._signature
        self._signature = signature
        names = ("settings", "resources", "materials")
        changed = {names[i] for i in range(3) if not before or before[i] != signature[i]}
        if "settings" in changed:
            self.settings_changed.emit()
            self.hpdcs_changed.emit()
        if "resources" in changed:
            self.resources_changed.emit()
        if "materials" in changed:
            self.materials_changed.emit()
        return True

    def mark_saved(self, *what: str) -> None:
        """本機剛寫入資料：只更新自己寫的檔案的簽章（避免被當成外部變更），並通知各頁。

        其他檔案的簽章維持原值：若其他電腦剛好也改了，下一次檢查仍會發現。
        """
        current = self.store.signature()
        names = ("settings", "resources", "materials")
        if len(self._signature) == len(current) == len(names):
            self._signature = tuple(current[i] if names[i] in what else self._signature[i] for i in range(3))
        else:
            self._signature = current
        if "settings" in what:
            self.settings_changed.emit()
        if "resources" in what:
            self.resources_changed.emit()
        if "materials" in what:
            self.materials_changed.emit()
        if "hpdcs" in what:
            self.hpdcs_changed.emit()

    def data_dir_available(self) -> bool:
        try:
            self.store.check_available()
            return True
        except StoreError:
            return False

    # ------------------------------------------------------------------ 本機設定
    def update_local(self, **changes) -> bool:
        """更新本機設定；寫入失敗回傳 False（畫面上仍保留這次的變更，但下次開啟會還原）。"""
        self.local = self.local.model_copy(update=changes)
        try:
            self.local_store.save(self.local)
            return True
        except (StoreError, OSError):
            return False

    def entry_is_current(self, entry: "HistoryEntry") -> bool:
        """查詢紀錄是否仍代表「今年、目前計畫」的結果（跨日或改了計畫就要重查）。"""
        return entry.when.date() == self._today() and tuple(entry.plans) == self.active_plans()

    # ------------------------------------------------------------------ 國健署
    def hpdcs_prefs(self) -> HpdcsPrefs:
        try:
            return self.store.load_settings().hpdcs
        except StoreError:
            return HpdcsPrefs()

    def today(self) -> date:
        """注入的時鐘：計畫年度、查詢紀錄是否過期、畫面上的年度標籤都用它，測試才能把日期撥到明年。"""
        return self._today()

    def active_plans(self) -> tuple[str, ...]:
        return plan_codes(self.hpdcs_prefs(), self._today())

    # ------------------------------------------------------------------ 讀卡與查詢紀錄
    def set_patient(self, pid: str, name: str = "", source: str = "card") -> None:
        self.current_patient = PatientInfo(pid=pid, name=name, source=source)
        self.patient_changed.emit()

    def add_history(self, entry: HistoryEntry) -> None:
        self.history = [h for h in self.history if h.person_id != entry.person_id]
        self.history.insert(0, entry)
        del self.history[HISTORY_LIMIT:]
        self.history_changed.emit()

    def clear_history(self) -> None:
        self.history.clear()
        self.history_changed.emit()
