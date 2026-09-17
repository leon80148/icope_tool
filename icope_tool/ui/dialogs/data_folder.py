"""資料存放位置：首次使用、無法存取（NAS 離線）、變更位置。"""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QLineEdit, QRadioButton, QWidget

from icope_tool import APP_DISPLAY_NAME
from icope_tool.paths import default_data_dir
from icope_tool.store import DataStore, StoreError, _read_model
from icope_tool.models import Settings
from icope_tool.ui.dialogs.base import BaseDialog, field_label
from icope_tool.ui.widgets import Banner, button, hbox, label, vbox


def describe_folder(path: Path) -> tuple[str, str]:
    """回傳 (狀態, 說明)。狀態：existing / empty / missing / invalid。"""
    if not path.exists():
        return "missing", "資料夾不存在，會自動建立。"
    if not path.is_dir():
        return "invalid", "這不是資料夾。"
    if DataStore.looks_initialized(path):
        try:
            clinic = _read_model(path / DataStore.SETTINGS, Settings).clinic.name
        except StoreError:
            clinic = ""
        suffix = f"（診所：{clinic}）" if clinic else ""
        return "existing", f"這個資料夾已經有本程式的資料{suffix}，會直接使用。"
    if any(path.iterdir()):
        return "empty", "資料夾裡有其他檔案，但沒有本程式的資料；會在裡面建立新資料。"
    return "empty", "空的資料夾，會在裡面建立新資料。"


class CopyCancelled(Exception):
    """使用者取消複製；已複製的檔案已清除。"""


def data_files(source: Path) -> list[Path]:
    """要複製的檔案（相對路徑）：設定、資源、單張清單與 PDF、帳密。"""
    files = [Path(name) for name in (DataStore.SETTINGS, DataStore.RESOURCES, DataStore.MATERIALS)
             if (source / name).is_file()]
    for folder in ("materials", "auth"):
        if (source / folder).is_dir():
            files.extend(path.relative_to(source) for path in sorted((source / folder).rglob("*")) if path.is_file())
    return files


def copy_data_dir(source: Path, target: Path, report=None, cancel=None) -> None:
    """把目前資料複製到新資料夾。目標已有資料時不覆蓋。

    report(已完成數, 說明) 回報進度；cancel（threading.Event）在檔案之間檢查，取消時清掉這次複製的檔案。
    設定檔最後才複製：中途取消時新資料夾不會被當成已有資料。
    """
    if DataStore.looks_initialized(target):
        return
    files = data_files(source)
    files.sort(key=lambda rel: rel.name == DataStore.SETTINGS)
    copied: list[Path] = []

    def stop_if_cancelled() -> None:
        if cancel is not None and cancel.is_set():
            for path in reversed(copied):
                path.unlink(missing_ok=True)
            raise CopyCancelled()

    for index, rel in enumerate(files):
        stop_if_cancelled()
        if report is not None:
            report(index, f"正在複製第 {index + 1} / {len(files)} 個檔案：{rel.name}")
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, destination)
        copied.append(destination)
    stop_if_cancelled()          # 最後一個檔案複製期間按了取消：同樣清掉，不切換位置


class DataFolderDialog(BaseDialog):
    """mode: first_run / unavailable / change"""

    def __init__(self, parent: QWidget | None, mode: str, current: Path | None = None):
        titles = {
            "first_run": (f"歡迎使用 {APP_DISPLAY_NAME}", "先決定資料要放在哪裡。之後可以在「設定 › 本機設定」更改。", "heart-pulse"),
            "unavailable": ("無法存取資料存放位置", "程式的設定、轉介資源與衛教單張都放在這個資料夾，目前無法存取。", "wifi-off"),
            "change": ("變更資料存放位置", "要讓多台電腦共用同一份資料，把每台電腦都指到同一個共用資料夾（例如 NAS）即可。", "folder-open"),
        }
        title, subtitle, icon_name = titles[mode]
        super().__init__(parent, title, subtitle, icon_name, width=600)
        self.mode = mode
        self.current = current
        self.chosen: Path | None = None
        self.copy_current = False

        if mode == "unavailable":
            self.body.addWidget(Banner("danger", str(current), "可能是網路磁碟（NAS）離線、共用資料夾權限改變，或 USB 隨身碟沒插上。"))
            self.body.addWidget(label("請先確認網路或磁碟，再按「重試」；或改用其他資料夾。", "subtle", wrap=True))
            self.add_button(button("結束程式", kind="secondary", on_click=self.reject))
            self.add_button(button("改用其他資料夾…", "folder-open", "secondary", on_click=self._browse_and_accept))
            retry = self.add_button(button("重試", "refresh-cw", "primary", on_click=self._retry))
            retry.setDefault(True)
            return

        if mode == "first_run":
            self.local = QRadioButton("只有這台電腦使用")
            self.local.setChecked(True)
            self.shared = QRadioButton("多台電腦共用（放在 NAS 或網路磁碟上的共用資料夾）")
            self.body.addLayout(vbox(self.local, label(f"資料放在：{default_data_dir()}", "caption"), 6,
                                     self.shared, spacing=4))
            self.local.toggled.connect(self._update)
        self.path = QLineEdit(str(current) if (current and mode == "change") else "")
        self.path.setPlaceholderText("選擇或輸入資料夾路徑，例如 \\\\NAS\\icope\\data")
        self.path.setAccessibleName("資料夾路徑")
        self.path.textChanged.connect(self._update)
        self.browse = button("選擇資料夾…", "folder-open", "secondary", on_click=self._browse)
        self.path_row = QWidget()
        self.path_row.setLayout(vbox(field_label("資料夾", buddy=self.path), hbox(self.path, self.browse, spacing=8), spacing=6))
        self.body.addWidget(self.path_row)
        self.info = Banner("info")
        self.body.addWidget(self.info)
        self.copy_box = None
        if mode == "change":
            from PySide6.QtWidgets import QCheckBox
            self.copy_box = QCheckBox("把目前的資料複製到新資料夾（新資料夾沒有資料時）")
            self.copy_box.setChecked(True)
            self.body.addWidget(self.copy_box)
        self.add_cancel("結束程式" if mode == "first_run" else "取消")
        self.ok = self.add_button(button("開始使用" if mode == "first_run" else "改用這個資料夾", "check",
                                         "primary", on_click=self._accept))
        self.ok.setDefault(True)
        self._update()

    def _selected_path(self) -> Path | None:
        if self.mode == "first_run" and self.local.isChecked():
            return default_data_dir()
        text = self.path.text().strip().strip('"')
        return Path(text) if text else None

    def _update(self) -> None:
        if self.mode == "first_run":
            self.path_row.setVisible(self.shared.isChecked())
        path = self._selected_path()
        if path is None:
            self.info.set_content("neutral", "請選擇資料夾")
            self.ok.setEnabled(False)
            if self.copy_box:
                self.copy_box.hide()
            return
        if self.mode == "change" and self.current and path.resolve() == self.current.resolve():
            self.info.set_content("neutral", "這就是目前使用的資料夾")
            self.ok.setEnabled(False)
            if self.copy_box:
                self.copy_box.hide()
            return
        try:
            state, text = describe_folder(path)
        except OSError as exc:
            state, text = "invalid", f"無法讀取：{exc.strerror or exc}"
        kind = {"existing": "success", "empty": "info", "missing": "info", "invalid": "danger"}[state]
        self.info.set_content(kind, text)
        self.ok.setEnabled(state != "invalid")
        if self.copy_box:
            self.copy_box.setVisible(state in ("empty", "missing"))

    def _browse(self) -> None:
        start = self.path.text() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "選擇資料存放位置", start)
        if folder:
            self.path.setText(str(Path(folder)))
            if self.mode == "first_run":
                self.shared.setChecked(True)

    def _browse_and_accept(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "選擇資料存放位置", str(Path.home()))
        if folder:
            self.chosen = Path(folder)
            self.accept()

    def _retry(self) -> None:
        self.chosen = self.current
        self.accept()

    def _accept(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        self.chosen = path
        self.copy_current = bool(self.copy_box and self.copy_box.isVisible() and self.copy_box.isChecked())
        self.accept()
