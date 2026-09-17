"""資源與單張的新增／編輯對話框。"""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QGridLayout, QLineEdit, QPlainTextEdit, QWidget

from icope_tool.domains import DOMAINS
from icope_tool.models import Material, Resource
from icope_tool.services.importer import split_phones
from icope_tool.ui.dialogs.base import BaseDialog, field_label
from icope_tool.ui.widgets import button, format_size, hbox, label, set_invalid

DEFAULT_LABEL = "院所預設（製作轉介單時按「帶入院所預設」，會自動加入）"


class DomainPicker(QWidget):
    def __init__(self, selected: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        self.boxes: dict[str, QCheckBox] = {}
        for index, domain in enumerate(DOMAINS):
            box = QCheckBox(domain.title)
            box.setChecked(domain.id in selected)
            box.setToolTip(domain.description)
            self.boxes[domain.id] = box
            grid.addWidget(box, index // 4, index % 4)
        grid.addLayout(hbox(button("全選", kind="ghost", size="sm", on_click=lambda: self._set_all(True)),
                            button("全不選", kind="ghost", size="sm", on_click=lambda: self._set_all(False)),
                            None, spacing=4), 2, 0, 1, 4)

    def _set_all(self, value: bool) -> None:
        for box in self.boxes.values():
            box.setChecked(value)

    def selected(self) -> list[str]:
        return [domain_id for domain_id, box in self.boxes.items() if box.isChecked()]


class ResourceDialog(BaseDialog):
    def __init__(self, parent: QWidget | None, types: list[str], resource: Resource | None = None):
        editing = resource is not None
        super().__init__(parent, "編輯轉介資源" if editing else "新增轉介資源",
                         "轉介單上會印出名稱、電話、時間、地址等資訊；適用項目決定它出現在哪些異常項目的建議清單。",
                         "square-pen" if editing else "plus", width=720)
        self.original = resource
        self.resource: Resource | None = None
        r = resource or Resource(name="—")

        self.name = QLineEdit(r.name if editing else "")
        self.name.setPlaceholderText("例如：嘉義基督教醫院 記憶門診")
        self.name.setAccessibleName("名稱")
        self.type = QComboBox()
        self.type.setEditable(True)
        self.type.addItems(types)
        self.type.setCurrentText(r.type if editing else "")
        self.type.lineEdit().setPlaceholderText("例如：醫療院所、巷弄長照站")
        self.type.setAccessibleName("類型")
        self.contact = QLineEdit(r.contact)
        self.contact.setPlaceholderText("選填")
        self.phones = QLineEdit("、".join(r.phones))
        self.phones.setPlaceholderText("多支電話用頓號分隔")
        self.phones.setAccessibleDescription("多支電話用頓號分隔")
        self.hours = QLineEdit(r.hours)
        self.hours.setPlaceholderText("例如：週一至週五 8:30–17:00")
        self.address = QLineEdit(r.address)
        self.website = QLineEdit(r.website)
        self.website.setPlaceholderText("https://")
        self.note = QPlainTextEdit(r.note)
        self.note.setFixedHeight(64)
        self.note.setPlaceholderText("例如：需先電話預約")
        self.domains = DomainPicker(r.domains if editing else [])
        self.pinned = QCheckBox("常用（選轉介時排在最上面）")
        self.pinned.setChecked(r.pinned if editing else False)
        self.include_default = QCheckBox(DEFAULT_LABEL)
        self.include_default.setChecked(r.include_by_default if editing else False)
        self.default_hint = label("還沒有勾選適用項目，院所預設不會生效。", "warning")
        self.enabled = QCheckBox("啟用（停用後不會出現在選單，但資料保留）")
        self.enabled.setChecked(r.enabled if editing else True)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        grid.addWidget(field_label("名稱", required=True, buddy=self.name), 0, 0, 1, 2)
        grid.addWidget(self.name, 1, 0, 1, 2)
        self.name_error = label("", "error")
        self.name_error.hide()
        grid.addWidget(self.name_error, 2, 0, 1, 2)
        grid.addWidget(field_label("類型", buddy=self.type), 3, 0)
        grid.addWidget(field_label("聯絡人", buddy=self.contact), 3, 1)
        grid.addWidget(self.type, 4, 0)
        grid.addWidget(self.contact, 4, 1)
        grid.addWidget(field_label("電話", buddy=self.phones), 5, 0)
        grid.addWidget(field_label("服務時間", buddy=self.hours), 5, 1)
        grid.addWidget(self.phones, 6, 0)
        grid.addWidget(self.hours, 6, 1)
        grid.addWidget(field_label("地址", buddy=self.address), 7, 0, 1, 2)
        grid.addWidget(self.address, 8, 0, 1, 2)
        grid.addWidget(field_label("網站", buddy=self.website), 9, 0)
        grid.addWidget(field_label("備註", buddy=self.note), 9, 1)
        grid.addWidget(self.website, 10, 0)
        grid.addWidget(self.note, 10, 1)
        self.body.addLayout(grid)
        self.body.addWidget(field_label("適用項目"))
        self.body.addWidget(self.domains)
        self.body.addLayout(hbox(self.pinned, 16, self.enabled, None))
        self.body.addWidget(self.include_default)
        self.body.addWidget(self.default_hint)

        self.add_cancel()
        self.save_button = self.add_button(button("儲存", "save", "primary", on_click=self._save))
        self.save_button.setDefault(True)
        self.name.textChanged.connect(lambda _t: (set_invalid(self.name, False), self.name_error.hide()))
        self.include_default.toggled.connect(self._update_default_hint)
        for box in self.domains.boxes.values():
            box.toggled.connect(self._update_default_hint)
        self._update_default_hint()
        self.name.setFocus()

    def _update_default_hint(self, *_args) -> None:
        self.default_hint.setVisible(self.include_default.isChecked() and not self.domains.selected())

    def _save(self) -> None:
        name = self.name.text().strip()
        if not name:
            set_invalid(self.name, True)
            self.name_error.setText("請輸入名稱")
            self.name_error.show()
            self.name.setFocus()
            return
        base = self.original.model_dump() if self.original else {}
        base.update(
            name=name, type=self.type.currentText().strip(), contact=self.contact.text(),
            phones=split_phones(self.phones.text()), hours=self.hours.text(), address=self.address.text(),
            website=self.website.text(), note=self.note.toPlainText().strip(), domains=self.domains.selected(),
            pinned=self.pinned.isChecked(), enabled=self.enabled.isChecked(),
            include_by_default=self.include_default.isChecked(),
        )
        self.resource = Resource.model_validate(base)
        self.accept()


class MaterialDialog(BaseDialog):
    def __init__(self, parent: QWidget | None, material: Material | None = None, file_name: str = "",
                 pages: int = 0):
        editing = material is not None and bool(material.filename)
        super().__init__(parent, "編輯衛教單張" if editing else "加入衛教單張",
                         "名稱會印在轉介單的隨附資料清單；選單張時會標示建議搭配的項目。",
                         "book-open", width=620)
        m = material or Material(name=file_name or "單張", filename="")
        self.original = m
        self.material: Material | None = None
        info = f"檔案：{file_name}　{pages} 頁" if file_name else f"{m.pages} 頁　{format_size(m.size)}"
        self.body.addWidget(label(info, "caption"))
        self.name = QLineEdit(m.name)
        self.name.setAccessibleName("單張名稱")
        self.description = QLineEdit(m.description)
        self.description.setPlaceholderText("選填，例如：銀髮族一日飲食建議")
        self.domains = DomainPicker(m.domains)
        self.enabled = QCheckBox("啟用（停用後不會出現在選單）")
        self.enabled.setChecked(m.enabled)
        self.include_default = QCheckBox(DEFAULT_LABEL)
        self.include_default.setChecked(m.include_by_default)
        self.default_hint = label("還沒有勾選建議搭配的項目，院所預設不會生效。", "warning")
        self.body.addWidget(field_label("名稱", required=True, buddy=self.name))
        self.body.addWidget(self.name)
        self.name_error = label("", "error")
        self.name_error.hide()
        self.body.addWidget(self.name_error)
        self.body.addWidget(field_label("說明", buddy=self.description))
        self.body.addWidget(self.description)
        self.body.addWidget(field_label("建議搭配的異常項目"))
        self.body.addWidget(self.domains)
        self.body.addWidget(self.enabled)
        self.body.addWidget(self.include_default)
        self.body.addWidget(self.default_hint)
        self.add_cancel()
        self.save_button = self.add_button(button("儲存" if editing else "加入", "save", "primary", on_click=self._save))
        self.save_button.setDefault(True)
        self.name.textChanged.connect(lambda _t: (set_invalid(self.name, False), self.name_error.hide()))
        self.include_default.toggled.connect(self._update_default_hint)
        for box in self.domains.boxes.values():
            box.toggled.connect(self._update_default_hint)
        self._update_default_hint()
        self.name.selectAll()
        self.name.setFocus()

    def _update_default_hint(self, *_args) -> None:
        self.default_hint.setVisible(self.include_default.isChecked() and not self.domains.selected())

    def _save(self) -> None:
        name = self.name.text().strip()
        if not name:
            set_invalid(self.name, True)
            self.name_error.setText("請輸入名稱")
            self.name_error.show()
            self.name.setFocus()
            return
        self.material = self.original.model_copy(update={
            "name": name, "description": self.description.text().strip(),
            "domains": self.domains.selected(), "enabled": self.enabled.isChecked(),
            "include_by_default": self.include_default.isChecked(),
        })
        self.accept()
