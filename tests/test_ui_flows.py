"""主要畫面流程（pytest-qt，offscreen，不連網、不碰讀卡機）。"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from PySide6.QtCore import Qt

from icope_tool.audit import AuditLog
from icope_tool.models import Resource
from icope_tool.services.hpdcs.client import CredentialError, IcopeResult, NetworkError, PlanResult
from icope_tool.store import DataStore, LocalConfigStore
from icope_tool.ui.context import AppContext, HistoryEntry
from tests.test_store import make_pdf


class FakeHpdcs:
    def __init__(self):
        self.calls = []
        self.next: object = None

    def status(self):
        return {"configured": True, "account_masked": "abc***", "logged_in": False, "auto_login_disabled": False,
                "last_login_at": None, "last_error": None, "ocr_available": True, "active_plans": []}

    def query_icope(self, pid, captcha_token=None, captcha_text=None, force=False, cancel=None):
        self.calls.append(pid)
        if isinstance(self.next, BaseException):
            raise self.next
        return self.next

    def invalidate(self):
        pass

    def clear_cache(self):
        pass

    def warm_up_ocr(self):
        return True

    def refresh_captcha(self, token):
        raise AssertionError("not used")

    def login(self, *a, **k):
        return None


@pytest.fixture(autouse=True)
def dialogs(monkeypatch):
    """無頭測試不能真的跳出對話框（會卡住整個測試）：記錄問了什麼，問句預設回答「取消」。"""
    from types import SimpleNamespace

    from PySide6.QtWidgets import QMessageBox

    record = SimpleNamespace(asked=[], answer=QMessageBox.StandardButton.Cancel,
                             notice_answer=QMessageBox.StandardButton.Ok)

    def question(_parent, title, _text, *args, **kwargs):
        record.asked.append(title)
        return record.answer

    def notice(_parent, title, _text, *args, **kwargs):
        record.asked.append(title)
        return record.notice_answer

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(notice))
    return record


@pytest.fixture
def env(tmp_path, qtbot):
    from icope_tool.ui.main_window import MainWindow
    from icope_tool.ui.theme import apply_theme
    from PySide6.QtWidgets import QApplication

    apply_theme(QApplication.instance())
    store = DataStore(tmp_path / "data")
    store.ensure_ready(create=True)
    store.update_settings(lambda s: setattr(s.clinic, "name", "測試診所"))
    store.add_resource(Resource(name="記憶門診", type="醫療院所", domains=["cognitive"], phones=["05-1"]))
    store.add_resource(Resource(name="巷弄長照站", type="巷弄長照站", domains=["cognitive", "mobility"]))
    store.add_material_file(make_pdf(tmp_path / "營養.pdf", pages=2), domains=["nutrition"])
    (store.auth_dir / "hpdcs_credentials.json").write_text('{"account":"abc1","password":"p"}', encoding="utf-8")
    fake = FakeHpdcs()
    ctx = AppContext(LocalConfigStore(tmp_path / "local"), store.root, AuditLog(tmp_path / "local" / "audit.log"),
                     hpdcs_factory=lambda _c: fake)
    window = MainWindow(ctx)
    qtbot.addWidget(window)
    window.resize(1366, 768)
    window.show()
    return window, ctx, store, fake


def test_sidebar_spells_the_display_name(env):
    from PySide6.QtWidgets import QLabel

    from icope_tool import APP_DISPLAY_NAME
    window, _ctx, _store, _fake = env
    shown = " ".join(window.findChild(QLabel, name).text() for name in ("SidebarTitle", "SidebarSubtitle"))
    assert shown == APP_DISPLAY_NAME


def test_query_button_follows_id_validation(env, qtbot):
    window, *_ = env
    page = window.pages["query"]
    page.id_input.setText("A12345")
    assert not page.query_button.isEnabled()
    page.id_input.setText("A123456788")
    assert not page.query_button.isEnabled()
    assert "檢查碼" in page.feedback.text()
    page.id_input.setText("a123456789")
    assert page.query_button.isEnabled()


def test_query_success_shows_verdict_and_history(env, qtbot):
    window, ctx, _store, fake = env
    page = window.pages["query"]
    fake.next = IcopeResult([PlanResult("EFA_115", "done", "今年可以繼續評估：X，該身分證已被登錄！")])
    page.id_input.setText("A123456789")
    page.start_query()
    qtbot.waitUntil(lambda: not page.querying, timeout=5000)
    assert fake.calls == ["A123456789"]
    assert page.result_stack.currentWidget() is page.result_view
    assert "已經做過" in page.verdict._title.text()
    assert ctx.history and ctx.history[0].person_id == "A123456789"
    assert "A123456789" not in ctx.audit.path.read_text(encoding="utf-8")


def test_query_errors_offer_the_right_next_step(env, qtbot):
    window, _ctx, _store, fake = env
    page = window.pages["query"]
    page.id_input.setText("A123456789")
    fake.next = CredentialError("國健署系統登入失敗（帳號密碼錯誤或帳號狀態異常），已暫停自動登入以免帳號被鎖")
    page.start_query()
    qtbot.waitUntil(lambda: not page.querying, timeout=5000)
    assert page.result_stack.currentWidget() is page.error_view
    assert "暫停自動登入" in page.error_banner._title.text()
    fake.next = NetworkError("連不上國健署系統，請確認這台電腦可以上網")
    page.start_query(force=True)
    qtbot.waitUntil(lambda: not page.querying, timeout=5000)
    assert "連不上" in page.error_banner._title.text()


def test_history_row_click_restores_result(env, qtbot):
    window, ctx, *_ = env
    page = window.pages["query"]
    result = IcopeResult([PlanResult("EFA_115", "can_assess", "今年可以繼續評估：O，可以繼續評估！")])
    ctx.add_history(HistoryEntry(datetime.now(), "B123456780", "林阿土", result, plans=ctx.active_plans()))
    row = page.history_list.itemAt(0).widget()
    qtbot.mouseClick(row, Qt.MouseButton.LeftButton)
    assert page.result_ident.text() == "B12****780"
    assert "可以進行評估" in page.verdict._title.text()


def test_referral_flow_builds_pdf(env, qtbot):
    window, ctx, store, _fake = env
    window.go("referral")
    page = window.pages["referral"]
    assert not page.next_button.isEnabled() or store.list_materials()
    page.domain_step.cards["cognitive"].setChecked(True)
    assert page.state.domains == ["cognitive"]
    page.next()
    qtbot.waitUntil(lambda: len(page.resource_step.rows) == 3, timeout=5000)  # 含預設「本院門診追蹤」
    row = next(r for r in page.resource_step.rows if r.resource.name == "記憶門診")
    row.checkbox.setChecked(True)
    assert page.state.is_selected(row.resource.id, "cognitive")
    assert "（1）" in page.resource_step.tabs[0].text()
    page.next()
    page.material_step.rows[0].set_checked(True)
    assert page.state.materials
    ctx.set_patient("A123456789", "王小明")
    page._bind_patient(keep_selections=True)
    page.next()
    assert page.state.patient_name == "王小明" and page.state.patient_pid == "A123456789"
    confirm = page.confirm_step
    qtbot.waitUntil(lambda: confirm._pdf_path is not None and not confirm._generating, timeout=20000)
    assert confirm._pdf_path.exists()
    assert "共 3 頁" in confirm.preview_status.text()


def test_domain_removal_clears_selection_and_summary(env, qtbot):
    window, *_ = env
    page = window.pages["referral"]
    page.domain_step.cards["cognitive"].setChecked(True)
    page.domain_step.cards["mobility"].setChecked(True)
    assert len(page.summary._counts) == 2
    page.domain_step.cards["mobility"].setChecked(False)
    assert list(page.summary._counts) == ["cognitive"]


def test_clinic_tab_saves(env, qtbot):
    window, _ctx, store, _fake = env
    window.go("settings", "clinic")
    tab = window.pages["settings"].pages["clinic"]
    assert not tab.save_button.isEnabled()
    tab.phone.setText("05-9999999")
    assert tab.save_button.isEnabled() and tab.has_unsaved_changes()
    tab.save()
    assert store.load_settings().clinic.phone == "05-9999999"
    assert not tab.has_unsaved_changes()
    tab.name.setText("")
    assert tab.save_button.isEnabled()          # 第五輪規格 4.2：有變更就能按；空白名稱按下去才說明，不再默默停用
    tab.save()
    assert not tab.name_error.isHidden() and store.load_settings().clinic.name == "測試診所"
    tab.load()          # 還原，避免關閉視窗時跳出「尚未儲存」確認框
    assert not tab.has_unsaved_changes()


def test_hpdcs_tab_requires_a_plan(env, qtbot):
    window, _ctx, store, _fake = env
    tab = window.pages["settings"].pages["hpdcs"]
    tab.official.setChecked(False)
    tab.pilot.setChecked(False)
    assert not tab.save_button.isEnabled() and "至少" in tab.plan_error.text()
    tab.pilot.setChecked(True)
    tab.save_plans()
    prefs = store.load_settings().hpdcs
    assert prefs.official is False and prefs.pilot is True
    tab.custom.setText("EFA_116, 壞/代碼")
    assert not tab.save_button.isEnabled()
    tab.load_plans()
    assert not tab.has_unsaved_changes()


def test_external_change_refreshes_pages(env, qtbot):
    window, ctx, store, _fake = env
    resources_tab = window.pages["settings"].pages["resources"]
    other_machine = DataStore(store.root)
    other_machine.add_resource(Resource(name="別台電腦新增的", domains=["vision"]))
    import os
    import time
    later = time.time() + 5
    os.utime(store.root / "resources.json", (later, later))
    assert ctx.check_external_changes() is True
    assert any(r.name == "別台電腦新增的" for r in resources_tab.resources)


def test_new_patient_never_silently_inherits_previous_selections(env, qtbot):
    window, ctx, *_ = env
    window.go("referral")
    page = window.pages["referral"]
    ctx.set_patient("A123456789", "王小明")              # 空白轉介單：直接綁定
    assert page.state.patient_pid == "A123456789" and page.patient_banner.isHidden()
    page.domain_step.cards["cognitive"].setChecked(True)
    ctx.set_patient("A123456789", "王小明")              # 同一位：不打擾
    assert page.patient_banner.isHidden()

    ctx.set_patient("B123456780", "林阿土")              # 另一位：先問
    assert not page.patient_banner.isHidden()
    assert page.state.patient_name == "王小明" and page.state.domains == ["cognitive"]
    page._bind_patient(keep_selections=False)
    assert page.state.patient_pid == "B123456780" and page.state.patient_name == "林阿土"
    assert page.state.domains == [] and page.patient_banner.isHidden()

    page.domain_step.cards["mobility"].setChecked(True)
    ctx.set_patient("C123456781", "")
    assert not page.patient_banner.isHidden()
    page._bind_patient(keep_selections=True)
    assert page.state.patient_pid == "C123456781" and page.state.domains == ["mobility"]


def test_result_is_bound_to_the_query_snapshot_not_later_ui_state(env, qtbot):
    """U02：查詢進行中點了另一筆紀錄，回傳結果仍記在原本那位長者名下。"""
    window, ctx, _store, fake = env
    page = window.pages["query"]
    import threading
    gate = threading.Event()
    result = IcopeResult([PlanResult("EFA_115", "can_assess", "今年可以繼續評估：O，可以繼續評估！")])

    def slow_query(pid, captcha_token=None, captcha_text=None, force=False, cancel=None):
        gate.wait(5)
        return result

    fake.query_icope = slow_query
    other = HistoryEntry(datetime.now(), "B123456780", "林阿土", result, plans=ctx.active_plans())
    ctx.add_history(other)
    page._pending_pid, page._pending_name = "A123456789", "王小明"
    page.id_input.setText("A123456789")
    page.start_query()
    assert page.querying
    page.show_entry(other)                      # 查詢途中看別人的結果
    gate.set()
    qtbot.waitUntil(lambda: not page.querying, timeout=5000)
    newest = ctx.history[0]
    assert newest.person_id == "A123456789" and newest.name == "王小明"


def test_cancel_query_discards_late_result(env, qtbot):
    window, ctx, _store, fake = env
    page = window.pages["query"]
    import threading
    gate = threading.Event()

    def slow_query(pid, captcha_token=None, captcha_text=None, force=False, cancel=None):
        gate.wait(5)
        return IcopeResult([PlanResult("EFA_115", "done", "今年可以繼續評估：X，該身分證已被登錄！")])

    fake.query_icope = slow_query
    page.id_input.setText("A123456789")
    page.start_query()
    page.cancel_query()
    assert not page.querying and "取消" in page.error_banner._title.text()
    gate.set()
    qtbot.wait(300)
    assert not ctx.history or ctx.history[0].result is None or ctx.history[0].person_id != "A123456789"
    assert page.result_stack.currentWidget() is page.error_view


# ---------------------------------------------------------------------------- 第二輪修正
def _select_cognitive_with_resource(page, qtbot):
    page.domain_step.cards["cognitive"].setChecked(True)
    page.go_to(1)
    qtbot.waitUntil(lambda: len(page.resource_step.rows) == 3, timeout=5000)
    row = next(r for r in page.resource_step.rows if r.resource.name == "記憶門診")
    row.checkbox.setChecked(True)
    return row.resource


def test_summary_lists_chosen_resource_names(env, qtbot):
    window, *_ = env
    window.go("referral")
    page = window.pages["referral"]
    _select_cognitive_with_resource(page, qtbot)
    assert "記憶門診" in page.summary._names["cognitive"].text()


def test_clear_all_asks_and_unchecked_domain_restores_resources(env, qtbot, dialogs):
    window, *_ = env
    window.go("referral")
    page = window.pages["referral"]
    resource = _select_cognitive_with_resource(page, qtbot)
    page.domain_step._clear_all()
    assert "取消全部異常項目？" in dialogs.asked
    assert page.state.domains == ["cognitive"]                  # 回答取消：什麼都沒動
    page.domain_step.cards["cognitive"].setChecked(False)
    page.domain_step.cards["cognitive"].setChecked(True)
    assert page.state.is_selected(resource.id, "cognitive")      # 勾回來會還原剛才選的資源


def test_step2_empty_state_opens_the_leaflet_dialog(env, qtbot):
    """第五輪規格 3.1：原本的「略過，只印衛教單張」改為開啟獨立的單張列印（不再是前往精靈下一步）。"""
    from PySide6.QtWidgets import QPushButton
    window, *_ = env
    window.go("referral")
    page = window.pages["referral"]
    page.go_to(1)
    entry = [b for b in page.resource_step.findChildren(QPushButton) if b.text() == "只印衛教單張…"]
    assert entry
    entry[0].click()
    assert page.current == 1 and page._leaflet_dialog is not None and page._leaflet_dialog.isVisible()
    page._leaflet_dialog.reject()
    page.next()                                                  # 含說明頁的做法照舊：沒勾項目也能往下選單張
    assert page.current == 2


def test_notes_over_limit_block_printing_without_cutting_text(env, qtbot):
    window, *_ = env
    page = window.pages["referral"]
    page.domain_step.cards["cognitive"].setChecked(True)
    page.go_to(3)
    confirm = page.confirm_step
    qtbot.waitUntil(lambda: not confirm._generating, timeout=20000)
    long_text = "請" * 410
    confirm.notes.setPlainText(long_text)
    assert confirm.notes.toPlainText() == long_text
    assert not confirm.print_button.isEnabled() and "超過 10 字" in confirm.notes_count.text()
    confirm.notes.setPlainText("請" * 400)
    assert confirm.print_button.isEnabled() and confirm.print_button.toolTip().endswith("（Ctrl+P）")


def test_edits_during_pdf_generation_are_never_printed_stale(env, qtbot):
    from pypdf import PdfReader
    window, *_ = env
    page = window.pages["referral"]
    page.domain_step.cards["cognitive"].setChecked(True)
    confirm = page.confirm_step
    printed = []
    confirm._with_pdf(lambda path: printed.append((path, confirm._version)))
    assert confirm._generating
    confirm.notes.setPlainText("產生途中補上的叮嚀")           # 背景還在產生舊內容
    qtbot.waitUntil(lambda: bool(printed), timeout=30000)
    path, version = printed[0]
    assert len(printed) == 1 and version == confirm._version and confirm.is_current
    assert "產生途中補上的叮嚀" in PdfReader(str(path)).pages[0].extract_text()


def test_reset_discards_pdf_still_generating_for_previous_patient(env, qtbot):
    window, *_ = env
    page = window.pages["referral"]
    page.domain_step.cards["cognitive"].setChecked(True)
    page.go_to(3)
    confirm = page.confirm_step
    assert confirm._generating
    page.reset()
    qtbot.waitUntil(lambda: not confirm._generating, timeout=20000)
    qtbot.wait(50)
    assert confirm._pdf_path is None and confirm.preview_stack.currentWidget() is confirm.preview_empty


def test_closing_with_unprinted_referral_asks_first(env, qtbot, dialogs):
    window, *_ = env
    page = window.pages["referral"]
    page.domain_step.cards["cognitive"].setChecked(True)
    window.close()
    assert "轉介單還沒印出" in dialogs.asked and window.isVisible()
    page.confirm_step._done("save", "已儲存 PDF", "x")          # 印過或存過就不再問
    dialogs.asked.clear()
    window.close()
    assert "轉介單還沒印出" not in dialogs.asked and not window.isVisible()


def test_print_shortcut_generates_latest_then_prints(env, qtbot, monkeypatch):
    window, *_ = env
    window.go("referral")
    page = window.pages["referral"]
    page.print_now()
    assert page.current == 0                                     # 還沒勾任何東西：只提示
    page.domain_step.cards["cognitive"].setChecked(True)
    printed = []
    monkeypatch.setattr(page.confirm_step, "_print", lambda path: printed.append(path))
    page.print_now()
    qtbot.waitUntil(lambda: bool(printed), timeout=30000)
    assert page.current == 3 and printed[0].exists() and len(printed) == 1


def test_switching_data_folder_keeps_the_referral_draft(env, qtbot, tmp_path):
    from icope_tool.ui.dialogs.data_folder import copy_data_dir
    window, ctx, store, _fake = env
    page = window.pages["referral"]
    resource = _select_cognitive_with_resource(page, qtbot)
    page.confirm_step.notes.setPlainText("帶藥袋")
    copy_data_dir(store.root, tmp_path / "copy")
    assert ctx.switch_data_dir(tmp_path / "copy") is True
    assert page.state.domains == ["cognitive"] and page.state.is_selected(resource.id, "cognitive")
    assert page.state.notes == "帶藥袋"
    empty = tmp_path / "empty"
    DataStore(empty).ensure_ready(create=True)
    ctx.switch_data_dir(empty)
    assert page.state.domains == ["cognitive"] and not page.state.resources.get("cognitive")


def test_clinic_edit_conflict_is_shown_not_silently_ignored(env, qtbot):
    import os
    import time
    window, ctx, store, _fake = env
    tab = window.pages["settings"].pages["clinic"]
    tab.phone.setText("05-1234567")
    DataStore(store.root).update_settings(lambda s: setattr(s.clinic, "name", "別台改的名稱"))
    later = time.time() + 5
    os.utime(store.root / "settings.json", (later, later))
    assert ctx.check_external_changes()
    assert not tab.conflict.isHidden() and tab.phone.text() == "05-1234567"
    tab.load()
    assert tab.conflict.isHidden() and tab.name.text() == "別台改的名稱"


def test_folder_change_with_unsaved_settings_asks_first(env, qtbot, dialogs):
    window, *_ = env
    settings = window.pages["settings"]
    settings.pages["clinic"].phone.setText("05-7654321")
    settings.pages["local"].change_folder()                     # 回答取消：不會開啟選資料夾視窗
    assert "有尚未儲存的設定" in dialogs.asked
    settings.pages["clinic"].load()


def test_local_setting_write_failure_is_reported(env, qtbot, monkeypatch):
    from icope_tool.store import StoreError
    window, ctx, *_ = env
    tab = window.pages["settings"].pages["local"]

    def broken(_config):
        raise StoreError("磁碟已滿")

    monkeypatch.setattr(ctx.local_store, "save", broken)
    assert ctx.update_local(reader_hint="X") is False
    tab._reader_changed()
    assert not tab.save_warning.isHidden() and "下次開啟會還原" in tab.save_warning._text.text()


def test_table_shortcuts_are_scoped_to_the_table(env):
    from PySide6.QtGui import QShortcut
    window, *_ = env
    for key in ("resources", "materials"):
        table = window.pages["settings"].pages[key].table
        shortcuts = table.findChildren(QShortcut)
        assert shortcuts and all(s.context() == Qt.ShortcutContext.WidgetShortcut for s in shortcuts)


def test_form_labels_name_their_inputs_and_errors_clear(qtbot):
    from icope_tool.ui.dialogs.editors import MaterialDialog, ResourceDialog
    resource = ResourceDialog(None, [])
    qtbot.addWidget(resource)
    assert resource.contact.accessibleName() == "聯絡人" and resource.website.accessibleName() == "網站"
    assert resource.name.accessibleDescription() == "必填"
    material = MaterialDialog(None, None, "a.pdf", 1)
    qtbot.addWidget(material)
    assert material.description.accessibleName() == "說明"
    material.name.setText("")
    material._save()
    assert not material.name_error.isHidden()
    material.name.setText("營養")
    assert material.name_error.isHidden()


def test_pack_replace_wording_matches_what_is_replaced(env):
    from icope_tool.services.pack import read_pack
    from icope_tool.ui.dialogs.importing import PackImportDialog
    from icope_tool.ui.settings.resources_tab import example_pack_path
    window, _ctx, store, _fake = env
    dialog = PackImportDialog(window, store, read_pack(example_pack_path()))
    assert "衛教單張保留不動" in dialog.replace.text()
    assert "衛教單張" not in dialog.confirm.text()


def test_excel_import_parses_in_background(env, qtbot, tmp_path):
    from icope_tool.ui.dialogs.importing import ExcelImportDialog
    from tests.test_services import _inventory_xlsx
    window, _ctx, store, _fake = env
    dialog = ExcelImportDialog(window, store, _inventory_xlsx(tmp_path / "inv.xlsx"))
    assert not dialog.import_button.isEnabled()                  # 讀取中
    qtbot.waitUntil(dialog.import_button.isEnabled, timeout=10000)
    assert dialog.sheet.currentText() == "嘉義市1003" and len(dialog.new_items) == 3
    dialog.sheet.setCurrentText("舊資料")                          # 換工作表：重新在背景讀取
    qtbot.waitUntil(lambda: "無法匯入" in dialog.stats._title.text(), timeout=10000)


def test_batch_pdf_add_runs_in_background(env, qtbot, tmp_path):
    window, _ctx, _store, _fake = env
    tab = window.pages["settings"].pages["materials"]
    files = [make_pdf(tmp_path / f"單張{i}.pdf", pages=1, text=f"doc{i}") for i in range(3)]
    tab.add_paths(files)
    qtbot.waitUntil(lambda: len(tab.materials) == 4, timeout=20000)


def test_every_input_has_an_accessible_name(env, qtbot, tmp_path):
    """螢幕報讀：主視窗各步驟與所有對話框的輸入元件都要念得出名稱（掃描，不靠逐檔檢查）。"""
    from PySide6.QtWidgets import QCheckBox, QComboBox, QLineEdit, QPlainTextEdit, QRadioButton

    from icope_tool.services.hpdcs.client import CaptchaManualRequired
    from icope_tool.services.pack import read_pack
    from icope_tool.ui.dialogs.captcha import CaptchaDialog
    from icope_tool.ui.dialogs.credentials import CredentialsDialog
    from icope_tool.ui.dialogs.data_folder import DataFolderDialog
    from icope_tool.ui.dialogs.editors import MaterialDialog, ResourceDialog
    from icope_tool.ui.dialogs.importing import ExcelImportDialog, PackImportDialog
    from icope_tool.ui.dialogs.leaflet_print import LeafletPrintDialog
    from icope_tool.ui.dialogs.resource_picker import ResourcePickerDialog
    from icope_tool.ui.settings.resources_tab import example_pack_path
    from tests.test_services import _inventory_xlsx

    window, ctx, store, fake = env
    page = window.pages["referral"]
    page.domain_step.cards["cognitive"].setChecked(True)
    page.go_to(1)
    qtbot.waitUntil(lambda: len(page.resource_step.rows) == 3, timeout=5000)
    page.go_to(2)
    page.go_to(3)
    leaflets = LeafletPrintDialog(window, ctx)
    assert leaflets.rows                                         # 真的建立了有內容的對話框，才算掃到
    tops = [
        window,
        leaflets,
        CaptchaDialog(window, CaptchaManualRequired(b"GIF89a", "image/gif", "tok"), fake.refresh_captcha),
        CredentialsDialog(window, ctx),
        ResourcePickerDialog(window, store.list_resources(), "cognitive", []),
        ResourceDialog(window, ["醫療院所"], None),
        MaterialDialog(window, None, "a.pdf", 1),
        ExcelImportDialog(window, store, _inventory_xlsx(tmp_path / "inv.xlsx")),
        PackImportDialog(window, store, read_pack(example_pack_path())),
        DataFolderDialog(window, "first_run"),
        DataFolderDialog(window, "change", store.root),
    ]
    missing, scanned = [], 0
    for top in tops:
        for cls in (QLineEdit, QComboBox, QPlainTextEdit, QCheckBox, QRadioButton):
            for widget in top.findChildren(cls):
                if isinstance(widget, QLineEdit) and isinstance(widget.parent(), QComboBox):
                    continue            # 下拉選單內建的輸入框，名稱在下拉選單本身
                scanned += 1
                text = "" if isinstance(widget, (QLineEdit, QComboBox, QPlainTextEdit)) else widget.text()
                if not (widget.accessibleName() or text).strip():
                    missing.append(f"{type(top).__name__} › {cls.__name__} {widget.objectName() or '(未命名)'}")
    assert scanned >= 60, scanned
    assert not missing, "\n".join(missing)


# ---------------------------------------------------------------------------- 第三輪：第二輪共識清單的驗證
@pytest.mark.parametrize("keep_selections", [False, True])
def test_parked_resources_never_follow_a_new_patient(env, qtbot, keep_selections):
    """U01：未填姓名 → 勾項目與資源 → 取消項目（資源暫存）→ 帶入另一位 → 無論選哪個分支都不繼承暫存資源。"""
    window, ctx, *_ = env
    window.go("referral")
    page = window.pages["referral"]
    resource = _select_cognitive_with_resource(page, qtbot)
    page.domain_step.cards["cognitive"].setChecked(False)
    assert page.state.domains == [] and page.state.has_selections()
    assert page.summary.reset_button.isEnabled()                 # 只剩暫存資源也能清除重來
    ctx.set_patient("B123456780", "林阿土", "query")
    assert not page.patient_banner.isHidden()                    # 先問，不默默換人
    page._bind_patient(keep_selections=keep_selections)
    page.domain_step.cards["cognitive"].setChecked(True)
    assert not page.state.is_selected(resource.id, "cognitive")
    assert page.state.patient_pid == "B123456780"


def test_parked_only_draft_is_protected_on_close(env, qtbot, dialogs):
    window, *_ = env
    window.go("referral")
    page = window.pages["referral"]
    _select_cognitive_with_resource(page, qtbot)
    page.domain_step.cards["cognitive"].setChecked(False)
    window.close()
    assert "轉介單還沒印出" in dialogs.asked and window.isVisible()


def _pdf_document(tmp_path, pages):
    from PySide6.QtPdf import QPdfDocument
    document = QPdfDocument()
    document.load(str(make_pdf(tmp_path / f"doc{pages}.pdf", pages=pages)))
    return document


def _pdf_printer(tmp_path, cls=None):
    from PySide6.QtPrintSupport import QPrinter
    printer = (cls or QPrinter)(QPrinter.PrinterMode.ScreenResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(tmp_path / "printed.pdf"))
    return printer


def test_printer_page_failure_raises_instead_of_reporting_success(qtbot, tmp_path):
    """U41：newPage() 失敗。"""
    from PySide6.QtPrintSupport import QPrinter

    from icope_tool.ui.printing import PrintError, send_to_printer

    class PageFails(QPrinter):
        def newPage(self):
            return False

    document = _pdf_document(tmp_path, 2)
    with pytest.raises(PrintError):
        send_to_printer(None, document, _pdf_printer(tmp_path, PageFails), 1, 2)
    document.close()


def test_printer_end_failure_raises_instead_of_reporting_success(qtbot, tmp_path, monkeypatch):
    """U41：painter.end() 失敗。"""
    import icope_tool.ui.printing as printing

    class EndFails(printing.QPainter):
        def end(self):
            super().end()
            return False

    monkeypatch.setattr(printing, "QPainter", EndFails)
    document = _pdf_document(tmp_path, 1)
    with pytest.raises(printing.PrintError):
        printing.send_to_printer(None, document, _pdf_printer(tmp_path), 1, 1)
    document.close()


def _failing_print_banner(env, qtbot, monkeypatch, outcomes):
    import icope_tool.ui.referral.confirm as confirm_module
    window, *_ = env
    page = window.pages["referral"]
    page.domain_step.cards["cognitive"].setChecked(True)

    def fake_print(_parent, _path):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(confirm_module, "print_pdf", fake_print)
    page.print_now()
    confirm = page.confirm_step
    qtbot.waitUntil(lambda: not confirm.result_banner.isHidden(), timeout=30000)
    assert "列印失敗" in confirm.result_banner._title.text()
    assert page.has_unfinished_work()                            # 沒印出就不能算完成
    return page, confirm


def _banner_button(banner, text):
    from PySide6.QtWidgets import QPushButton
    return next(b for b in banner.findChildren(QPushButton) if b.text() == text and not b.isHidden())


def test_print_failure_offers_working_retry(env, qtbot, monkeypatch):
    import icope_tool.ui.referral.confirm as confirm_module
    page, confirm = _failing_print_banner(env, qtbot, monkeypatch,
                                          [confirm_module.PrintError("第 2 頁送不出去"), True])
    _banner_button(confirm.result_banner, "重試列印").click()
    qtbot.waitUntil(lambda: "已送出列印" in confirm.result_banner._title.text(), timeout=30000)
    assert not page.has_unfinished_work()


def test_print_failure_offers_working_save_as(env, qtbot, monkeypatch, tmp_path):
    import icope_tool.ui.referral.confirm as confirm_module
    page, confirm = _failing_print_banner(env, qtbot, monkeypatch, [confirm_module.PrintError("印表機離線")])
    target = tmp_path / "saved.pdf"
    monkeypatch.setattr(confirm_module.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(target), "PDF 檔案 (*.pdf)")))
    _banner_button(confirm.result_banner, "另存 PDF…").click()
    qtbot.waitUntil(lambda: target.exists(), timeout=30000)
    assert "已儲存 PDF" in confirm.result_banner._title.text() and not page.has_unfinished_work()


def test_output_requested_before_notes_exceed_limit_is_not_printed(env, qtbot):
    """U14：要求輸出後、PDF 產生途中把叮嚀改成超過字數。"""
    window, *_ = env
    page = window.pages["referral"]
    page.domain_step.cards["cognitive"].setChecked(True)
    confirm = page.confirm_step
    printed = []
    confirm._with_pdf(printed.append)
    confirm.notes.setPlainText("請" * 410)
    qtbot.waitUntil(lambda: not confirm._generating and not confirm.result_banner.isHidden(), timeout=30000)
    qtbot.wait(200)
    assert printed == [] and "叮嚀超過" in confirm.result_banner._text.text()
    assert page.stepper.buttons[3].detail_label.text() == "叮嚀超過字數"      # U42：打字當下就更新
    confirm.notes.setPlainText("回診請帶藥袋")
    confirm._with_pdf(printed.append)
    qtbot.waitUntil(lambda: bool(printed), timeout=30000)
    assert len(printed) == 1


def test_output_requested_before_clearing_everything_is_not_printed(env, qtbot):
    """U14：要求輸出後、PDF 產生途中取消全部可輸出的內容。"""
    window, *_ = env
    page = window.pages["referral"]
    page.domain_step.cards["cognitive"].setChecked(True)
    confirm = page.confirm_step
    printed = []
    confirm._with_pdf(printed.append)
    page.domain_step.cards["cognitive"].setChecked(False)
    qtbot.waitUntil(lambda: not confirm._generating and not confirm.result_banner.isHidden(), timeout=30000)
    qtbot.wait(200)
    assert printed == [] and "沒有勾選" in confirm.result_banner._text.text()
    page.domain_step.cards["cognitive"].setChecked(True)
    confirm._with_pdf(printed.append)
    qtbot.waitUntil(lambda: bool(printed), timeout=30000)
    assert len(printed) == 1


@pytest.mark.parametrize("old_job_fails", [False, True])
def test_print_request_for_new_draft_survives_old_pdf_job(env, qtbot, monkeypatch, old_job_fails):
    """U38：舊長者的 PDF 還在產生時清除重來，新草稿按 Ctrl+P；舊工作成功或失敗都不能吞掉新要求。"""
    from pypdf import PdfReader

    import icope_tool.ui.referral.confirm as confirm_module
    window, *_ = env
    page = window.pages["referral"]
    confirm = page.confirm_step
    real_build = confirm_module.build_referral_pdf
    calls = []

    def build(job, files, output):
        calls.append(output)
        if old_job_fails and len(calls) == 1:
            raise confirm_module.PdfBuildError("模擬舊工作失敗")
        return real_build(job, files, output)

    monkeypatch.setattr(confirm_module, "build_referral_pdf", build)
    page.domain_step.cards["cognitive"].setChecked(True)
    confirm._generate(None)
    assert confirm._generating
    page.reset()
    page.domain_step.cards["mobility"].setChecked(True)
    printed = []
    monkeypatch.setattr(confirm, "_print", lambda path: printed.append(path))
    page.print_now()
    qtbot.waitUntil(lambda: bool(printed), timeout=30000)
    qtbot.wait(300)
    assert len(printed) == 1
    text = PdfReader(str(printed[0])).pages[0].extract_text()
    assert "行動能力" in text and "認知功能" not in text


def test_stale_history_entry_is_requeried_not_shown(env, qtbot):
    """U09b：昨天的紀錄即使還掛在清單上，點開也要重新查詢。"""
    window, ctx, _store, fake = env
    page = window.pages["query"]
    old = HistoryEntry(datetime.now() - timedelta(days=1), "A123456789", "王小明",
                       IcopeResult([PlanResult("EFA_115", "done", "今年可以繼續評估：X，該身分證已被登錄！")]),
                       plans=ctx.active_plans())
    ctx.add_history(old)
    fake.next = IcopeResult([PlanResult("EFA_115", "can_assess", "今年可以繼續評估：O，可以繼續評估！")])
    page._open_history(old)
    qtbot.waitUntil(lambda: not page.querying and bool(fake.calls), timeout=5000)
    assert fake.calls == ["A123456789"] and ctx.history[0].when.date() == datetime.now().date()
    fake.calls.clear()
    page.show_entry(old)
    qtbot.waitUntil(lambda: not page.querying and bool(fake.calls), timeout=5000)
    assert fake.calls == ["A123456789"]


def test_result_on_screen_expires_when_plans_change(env, qtbot):
    window, ctx, store, _fake = env
    page = window.pages["query"]
    entry = HistoryEntry(datetime.now(), "A123456789", "王小明",
                         IcopeResult([PlanResult("EFA_115", "can_assess", "O")]), plans=ctx.active_plans())
    ctx.add_history(entry)
    page.show_entry(entry)
    store.update_settings(lambda s: setattr(s.hpdcs, "pilot", not s.hpdcs.pilot))
    ctx.mark_saved("settings")
    assert page.result_stack.currentWidget() is page.error_view
    assert "過期" in page.error_banner._title.text()
    assert page.id_input.text() == ""                                # U51：不動輸入框


def test_failed_requery_keeps_todays_successful_result(env, qtbot):
    """U44：重新查詢失敗時，今天成功的結果仍在紀錄裡，失敗畫面也講清楚。"""
    window, ctx, _store, fake = env
    page = window.pages["query"]
    ok = HistoryEntry(datetime.now(), "A123456789", "王小明",
                      IcopeResult([PlanResult("EFA_115", "can_assess", "O")]), plans=ctx.active_plans())
    ctx.add_history(ok)
    fake.next = NetworkError("連線逾時")
    page.id_input.setText("A123456789")
    page.start_query(force=True)
    qtbot.waitUntil(lambda: not page.querying, timeout=5000)
    assert ctx.history[0] is ok and ok.result is not None
    assert "仍保留" in page.error_banner._text.text()


def test_late_card_read_does_not_overwrite_manual_query(env, qtbot, monkeypatch):
    """U50：等插卡時改成手動輸入查詢，之後才回來的讀卡結果不能覆寫輸入、長者與紀錄。"""
    import threading

    import icope_tool.ui.query_page as query_module
    if query_module.nhi_card is None:
        pytest.skip("這台電腦無法載入讀卡元件")
    window, ctx, _store, fake = env
    page = window.pages["query"]
    gate = threading.Event()

    def slow_read(wait_s, reader_hint="", cancel=None):
        gate.wait(5)
        return {"pid": "B123456780", "name": "林阿土"}

    monkeypatch.setattr(query_module.nhi_card, "read_basic", slow_read)
    fake.next = IcopeResult([PlanResult("EFA_115", "can_assess", "O")])
    page.toggle_card_read()
    page.id_input.setText("A123456789")
    page.start_query()
    qtbot.waitUntil(lambda: not page.querying, timeout=5000)
    gate.set()
    qtbot.waitUntil(lambda: not page._reading_card, timeout=5000)
    qtbot.wait(200)
    assert page.id_input.text() == "A123456789" and fake.calls == ["A123456789"]
    assert ctx.current_patient is None or ctx.current_patient.pid != "B123456780"
    assert all(h.person_id != "B123456780" for h in ctx.history)


def _birth_for_age(age: int, month_day: str = "1231") -> str:
    """今年度會滿 age 歲的民國出生日期（預設 12/31 生日：年度算法不看月日）。"""
    return f"{datetime.now().year - 1911 - age:03d}{month_day}"


def _read_card(page, qtbot, monkeypatch, fields: dict) -> None:
    """假的讀卡機：按「讀健保卡」，等它讀完並接著查完。"""
    import icope_tool.ui.query_page as query_module
    if query_module.nhi_card is None:
        pytest.skip("這台電腦無法載入讀卡元件")
    monkeypatch.setattr(query_module.nhi_card, "read_basic", lambda wait_s, reader_hint="", cancel=None: dict(fields))
    page.toggle_card_read()
    qtbot.waitUntil(lambda: not page._reading_card and not page.querying and page._shown is not None
                    and page._shown.person_id == fields["pid"], timeout=5000)


def test_card_read_shows_the_calendar_year_age_without_changing_the_verdict(env, qtbot, monkeypatch):
    window, ctx, _store, fake = env
    page = window.pages["query"]
    fake.next = IcopeResult([PlanResult("EFA_115", "can_assess", "O")])
    birth = _birth_for_age(65)
    _read_card(page, qtbot, monkeypatch, {"pid": "A123456789", "name": "王小明", "birth_roc": birth})
    assert "今年度滿 65 歲" in page.age_note.text() and page.age_note.property("role") == "success"
    assert page.age_note.isVisibleTo(page.result_view)
    assert "可以進行評估" in page.verdict._title.text()                 # 國健署的判定照舊
    assert birth not in ctx.audit.path.read_text(encoding="utf-8")       # 出生日期只在記憶體，不進稽核


def test_under_65_is_a_reminder_not_a_block(env, qtbot, monkeypatch):
    window, _ctx, _store, fake = env
    page = window.pages["query"]
    fake.next = IcopeResult([PlanResult("EFA_115", "can_assess", "O")])
    _read_card(page, qtbot, monkeypatch, {"pid": "A123456789", "name": "王小明", "birth_roc": _birth_for_age(60)})
    assert page.age_note.property("role") == "warning"
    assert "未滿 65 歲" in page.age_note.text() and "原住民 55 歲以上可評估" in page.age_note.text()
    assert "可以進行評估" in page.verdict._title.text()
    go = next(b for b in page.result_view.findChildren(type(page.card_button)) if b.text().strip() == "製作轉介衛教單")
    assert go.isEnabled()                                                 # 只是提醒：原住民長者 55 歲就能做


def test_age_from_one_card_never_sticks_to_another_id(env, qtbot, monkeypatch):
    """出生日期跟著讀卡當下的身分證走：之後手動查別人不能沿用，回頭看紀錄或重新查詢時要還在。"""
    window, ctx, _store, fake = env
    page = window.pages["query"]
    fake.next = IcopeResult([PlanResult("EFA_115", "can_assess", "O")])
    _read_card(page, qtbot, monkeypatch, {"pid": "A123456789", "name": "王小明", "birth_roc": _birth_for_age(60)})
    assert page.age_note.property("role") == "warning"

    page.id_input.setText("B123456780")                                   # 沒讀卡，手動查另一位
    page.start_query()
    qtbot.waitUntil(lambda: not page.querying and page._shown.person_id == "B123456780", timeout=5000)
    assert "未讀健保卡，無法核對年齡" in page.age_note.text() and page.age_note.property("role") == "caption"

    first = next(h for h in ctx.history if h.person_id == "A123456789")
    page._open_history(first)                                             # 點右邊的紀錄回頭看
    assert page.age_note.property("role") == "warning" and "今年度滿 60 歲" in page.age_note.text()

    page._requery(first)                                                  # 重新查詢同一位：年齡還在
    qtbot.waitUntil(lambda: not page.querying and page._shown.person_id == "A123456789"
                    and page._shown is not first, timeout=5000)
    assert "今年度滿 60 歲" in page.age_note.text()


def test_age_survives_a_captcha_retry(env, qtbot):
    from threading import Event

    from icope_tool.ui.query_page import QueryRequest
    window, ctx, _store, fake = env
    page = window.pages["query"]
    fake.next = IcopeResult([PlanResult("EFA_115", "can_assess", "O")])
    request = QueryRequest(1, "A123456789", "王小明", ctx.active_plans(), False, Event(), _birth_for_age(70))
    page._resume_with_captcha(request, "token", "12345")
    qtbot.waitUntil(lambda: not page.querying and page._shown is not None, timeout=5000)
    assert "今年度滿 70 歲" in page.age_note.text()


def test_resource_write_failure_can_be_retried_without_retyping(env, qtbot, dialogs, monkeypatch):
    """U31：寫入失敗時選「重試」，用同一份內容再寫一次。"""
    from PySide6.QtWidgets import QMessageBox

    from icope_tool.store import StoreIOError
    window, ctx, store, _fake = env
    tab = window.pages["settings"].pages["resources"]
    original = ctx.store.add_resource
    attempts = []

    def flaky(resource):
        attempts.append(resource.name)
        if len(attempts) == 1:
            raise StoreIOError("共用資料夾暫時無法寫入")
        return original(resource)

    monkeypatch.setattr(ctx.store, "add_resource", flaky)
    dialogs.notice_answer = QMessageBox.StandardButton.Retry
    new = Resource(name="新的社區關懷據點", domains=["social"])
    assert tab._save(lambda: ctx.store.add_resource(new), "已新增")
    assert attempts == ["新的社區關懷據點", "新的社區關懷據點"]
    assert any(r.name == "新的社區關懷據點" for r in store.list_resources())


def test_tall_dialogs_fit_the_screen(env, qtbot):
    """U39：資源對話框在矮螢幕上不超出可用高度，內容捲動、儲存鈕看得到。"""
    from icope_tool.ui.dialogs.editors import ResourceDialog
    window, *_ = env
    dialog = ResourceDialog(window, ["醫療院所"])
    dialog.show()
    qtbot.waitExposed(dialog)
    available = dialog.screen().availableGeometry().height()
    assert dialog.height() <= max(360, available - 60)
    assert dialog.minimumSizeHint().height() < 520
    assert dialog.save_button.isVisible()
    dialog.close()


def test_picker_space_toggles_row_from_any_column(env, qtbot):
    """U43：資源挑選表格游標在任何一欄，按空白鍵都能勾選整列。"""
    import warnings

    from PySide6.QtWidgets import QApplication

    from icope_tool.ui.dialogs.resource_picker import ResourcePickerDialog
    window, _ctx, store, _fake = env
    dialog = ResourcePickerDialog(window, store.list_resources(), "hearing", [])
    dialog.show()
    qtbot.waitExposed(dialog)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        QApplication.setActiveWindow(dialog)
    dialog.table.setFocus()
    dialog.table.setCurrentIndex(dialog.proxy.index(0, 2))
    qtbot.keyClick(dialog.table, Qt.Key.Key_Space)
    assert len(dialog._checked_ids()) == 1
    dialog.close()


def test_history_rows_are_not_rebuilt_while_nothing_changed(env, qtbot):
    """U53：每分鐘的檢查不能重建紀錄列（會奪走鍵盤焦點）；跨日或計畫變更才重建。"""
    window, ctx, _store, _fake = env
    page = window.pages["query"]
    ctx.add_history(HistoryEntry(datetime.now(), "A123456789", "王小明",
                                 IcopeResult([PlanResult("EFA_115", "can_assess", "O")]), plans=ctx.active_plans()))
    row = page.history_list.itemAt(0).widget()
    page._revalidate()
    assert page.history_list.itemAt(0).widget() is row
    page._history_day = page._history_day - timedelta(days=1)       # 模擬跨日
    page._revalidate()
    assert page.history_list.itemAt(0).widget() is not row


def test_duplicate_material_is_explained_without_retry(env, qtbot, dialogs, monkeypatch):
    """U54：重複的 PDF 重試也沒用：直接說明，不問重試。"""
    from PySide6.QtWidgets import QMessageBox

    from icope_tool.store import StoreError, StoreIOError
    from icope_tool.ui.widgets import write_with_retry
    window, *_ = env
    dialogs.notice_answer = QMessageBox.StandardButton.Retry
    attempts = []

    def duplicate():
        attempts.append(1)
        raise StoreError("「營養.pdf」已經加入過")

    assert write_with_retry(window, "無法加入這個 PDF", duplicate) == (False, None)
    assert attempts == [1] and dialogs.asked == ["無法加入這個 PDF"]
    flaky = []

    def transient():
        flaky.append(1)
        if len(flaky) == 1:
            raise StoreIOError("網路磁碟暫時斷線")
        return "ok"

    assert write_with_retry(window, "無法儲存", transient) == (True, "ok") and len(flaky) == 2


def test_expired_result_keeps_the_id_being_typed(env, qtbot):
    """U51：畫面是 A 的結果、輸入框已打好 B；A 過期時不能把 B 換回 A，重新查詢只查 A。"""
    window, ctx, store, fake = env
    page = window.pages["query"]
    entry = HistoryEntry(datetime.now(), "A123456789", "王小明",
                         IcopeResult([PlanResult("EFA_115", "can_assess", "O")]), plans=ctx.active_plans())
    ctx.add_history(entry)
    page.show_entry(entry)
    page.id_input.setText("B123456780")
    store.update_settings(lambda s: setattr(s.hpdcs, "pilot", not s.hpdcs.pilot))
    ctx.mark_saved("settings")
    assert page.id_input.text() == "B123456780"
    fake.next = IcopeResult([PlanResult("EFA_115", "can_assess", "O")])
    _banner_button(page.error_banner, "重新查詢 A12****789").click()
    qtbot.waitUntil(lambda: not page.querying and bool(fake.calls), timeout=5000)
    assert fake.calls == ["A123456789"]


def test_history_focus_stays_on_the_same_person_after_rebuild(env, qtbot):
    """U52：跨日時必須重建紀錄列，鍵盤焦點回到同一位長者那一列。"""
    import warnings

    from PySide6.QtWidgets import QApplication
    window, ctx, _store, _fake = env
    page = window.pages["query"]
    result = IcopeResult([PlanResult("EFA_115", "can_assess", "O")])
    for pid in ("A123456789", "B123456780"):
        ctx.add_history(HistoryEntry(datetime.now(), pid, "", result, plans=ctx.active_plans()))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        QApplication.setActiveWindow(window)
    qtbot.wait(50)                                                     # 讓新加入的列顯示出來
    second = page.history_list.itemAt(1).widget()
    second.setFocus()
    assert second.hasFocus()
    page._history_day = page._history_day - timedelta(days=1)
    page._revalidate()
    rebuilt = page.history_list.itemAt(1).widget()
    assert rebuilt is not second and rebuilt.hasFocus() and rebuilt.entry.person_id == second.entry.person_id


def test_explicitly_sized_dialogs_are_not_shrunk(env, qtbot):
    """U53 回歸防護：使用說明、資源挑選這類自訂尺寸的對話框，不能被依內容收回的邏輯縮小。"""
    from icope_tool.ui.dialogs.resource_picker import ResourcePickerDialog
    from icope_tool.ui.help_dialog import HelpDialog
    window, _ctx, store, _fake = env
    # 使用說明加了圖片後改為 980×720（圖要夠大字才看得清楚）；放不下的螢幕一樣以可用高度為上限
    for dialog, wanted in ((HelpDialog(window), 720),
                           (ResourcePickerDialog(window, store.list_resources(), "hearing", []), 640)):
        dialog.show()
        qtbot.waitExposed(dialog)
        qtbot.wait(50)
        limit = max(360, dialog.screen().availableGeometry().height() - 60)
        assert dialog.height() == min(wanted, limit)
        dialog.close()


def test_data_folder_outage_keeps_the_referral_draft(env, qtbot, tmp_path):
    """程式碼審查 P1：共用資料夾斷線時提示可重試，已選的資源與單張不被當成刪除而清掉。"""
    import os
    import time
    window, ctx, store, _fake = env
    page = window.pages["referral"]
    resource = _select_cognitive_with_resource(page, qtbot)
    page.state.set_material(store.list_materials()[0].id, True)
    offline = tmp_path / "data-offline"
    store.root.rename(offline)
    ctx.check_external_changes()
    assert not page.load_error.isHidden()
    assert page.state.is_selected(resource.id, "cognitive") and page.state.materials
    offline.rename(store.root)
    later = time.time() + 5
    for name in ("settings.json", "resources.json", "materials.json"):
        os.utime(store.root / name, (later, later))
    ctx.check_external_changes()
    assert page.load_error.isHidden()
    assert page.state.is_selected(resource.id, "cognitive") and page.state.materials


def test_local_save_does_not_swallow_another_machines_change(env, qtbot):
    """程式碼審查 P2：本機儲存診所資訊時，其他電腦剛新增的資源下一次檢查仍要出現。"""
    import os
    import time
    window, ctx, store, _fake = env
    DataStore(store.root).add_resource(Resource(name="別台電腦剛新增的", domains=["social"]))
    later = time.time() + 5
    os.utime(store.root / "resources.json", (later, later))
    clinic = window.pages["settings"].pages["clinic"]
    clinic.phone.setText("05-1112222")
    clinic.save()
    assert ctx.check_external_changes()
    resources_tab = window.pages["settings"].pages["resources"]
    assert any(r.name == "別台電腦剛新增的" for r in resources_tab.resources)


def test_output_marks_the_printed_version_not_newer_content(env, qtbot, monkeypatch):
    """程式碼審查 P2：送印期間內容被外部更新，完成時不能把較新、未印出的內容標成已完成。"""
    import icope_tool.ui.referral.confirm as confirm_module
    window, *_ = env
    page = window.pages["referral"]
    page.domain_step.cards["cognitive"].setChecked(True)
    confirm = page.confirm_step

    def print_while_data_changes(_parent, _path):
        confirm.mark_stale()                           # 送印過程中其他電腦更新了資源
        return True

    monkeypatch.setattr(confirm_module, "print_pdf", print_while_data_changes)
    page.print_now()
    qtbot.waitUntil(lambda: "已送出列印" in confirm.result_banner._title.text() or not confirm.result_banner.isHidden(),
                    timeout=30000)
    assert page.has_unfinished_work()


# ---------------------------------------------------------------------------- 第五輪（產品設計）：院所預設
def _mark_defaults(store, ctx, resources=("記憶門診", "巷弄長照站"), leaflet=True):
    """env 的資料：記憶門診（認知）、巷弄長照站（認知＋行動）、單張「營養」（營養）。"""
    ids = [r.id for r in store.list_resources() if r.name in resources]
    store.patch_resources(ids, include_by_default=True)
    if leaflet:
        store.patch_materials([m.id for m in store.list_materials()], include_by_default=True)
    ctx.mark_saved("resources", "materials")


def test_defaults_block_tells_the_truth_in_every_state(env, qtbot):
    window, ctx, store, _fake = env
    window.go("referral")
    page = window.pages["referral"]
    summary = page.summary
    assert "可在設定" in summary.defaults_caption.text()                  # 全院還沒有任何院所預設：中性提示
    assert summary.defaults_button.isHidden() and not summary.defaults_link.isHidden()

    _mark_defaults(store, ctx)
    assert "勾選異常項目後" in summary.defaults_caption.text()            # 有預設、還沒勾項目
    assert summary.defaults_button.isHidden() and summary.defaults_link.isHidden()

    page.domain_step.cards["hearing"].setChecked(True)                    # 只勾了沒有預設的項目：不能說已帶入
    assert "聽力目前沒有院所預設" in summary.defaults_caption.text()
    assert "已帶入" not in summary.defaults_caption.text() and summary.defaults_button.isHidden()

    page.domain_step.cards["cognitive"].setChecked(True)
    assert "本次帶入：認知" in summary.defaults_caption.text() and not summary.defaults_button.isHidden()
    summary.defaults_button.click()
    chosen = {r.name for r in store.list_resources() if page.state.is_selected(r.id, "cognitive")}
    assert chosen == {"記憶門診", "巷弄長照站"}
    assert "已帶入 2 個轉介資源" in page.toast.text()
    assert "已帶入過院所預設：認知" in summary.defaults_caption.text() and summary.defaults_button.isHidden()

    page.domain_step.cards["nutrition"].setChecked(True)                  # 三段同時存在
    caption = summary.defaults_caption.text()
    assert all(part in caption for part in ("本次帶入：營養", "已帶入過院所預設：認知", "聽力目前沒有院所預設"))
    summary.defaults_button.click()
    assert page.state.materials and "已帶入 1 份衛教單張" in page.toast.text()
    assert page.state.default_materials == {"nutrition": page.state.materials}


def test_flag_only_change_refreshes_the_defaults_block(env, qtbot):
    window, ctx, store, _fake = env
    window.go("referral")
    page = window.pages["referral"]
    page.domain_step.cards["cognitive"].setChecked(True)
    assert page.summary.defaults_button.isHidden()
    _mark_defaults(store, ctx, leaflet=False)                             # 項目與單張的選取都沒變，只改了旗標
    assert "本次帶入：認知" in page.summary.defaults_caption.text() and not page.summary.defaults_button.isHidden()


def test_everything_already_chosen_still_counts_as_applied(env, qtbot):
    window, ctx, store, _fake = env
    window.go("referral")
    page = window.pages["referral"]
    _mark_defaults(store, ctx, resources=("記憶門診",), leaflet=False)
    _select_cognitive_with_resource(page, qtbot)                          # 護理師自己先選好了
    page.apply_defaults()
    assert "沒有新增內容" in page.toast.text()
    assert page.state.defaults_applied == {"cognitive"} and page.summary.defaults_button.isHidden()


def test_nothing_pending_means_no_change_and_no_stale_pdf(env, qtbot):
    window, ctx, store, _fake = env
    page = window.pages["referral"]
    _mark_defaults(store, ctx)
    page.domain_step.cards["hearing"].setChecked(True)
    version = page.confirm_step._version
    page.apply_defaults()
    assert page.confirm_step._version == version and page.state.defaults_applied == set()


def test_half_loaded_lists_are_never_used_for_defaults(env, qtbot, monkeypatch):
    from icope_tool.store import StoreIOError
    window, ctx, store, _fake = env
    window.go("referral")
    page = window.pages["referral"]
    _mark_defaults(store, ctx)
    page.domain_step.cards["cognitive"].setChecked(True)
    before = [r.id for r in page.resources]
    store.add_resource(Resource(name="別台新增的預設", domains=["cognitive"], include_by_default=True))

    def broken():
        raise StoreIOError("網路磁碟暫時斷線")

    monkeypatch.setattr(ctx.store, "list_materials", broken)
    page.reload_data()                                                    # 資源讀到新的、單張讀取失敗
    assert not page.load_error.isHidden()
    assert [r.id for r in page.resources] == before                       # 不混用新舊清單
    assert not page.summary.defaults_button.isEnabled()
    page.apply_defaults()
    assert page.state.defaults_applied == set() and page.state.domains == ["cognitive"]


def test_applying_defaults_on_the_leaflet_step_keeps_ownership(env, qtbot):
    window, ctx, store, _fake = env
    window.go("referral")
    page = window.pages["referral"]
    _mark_defaults(store, ctx)
    page.domain_step.cards["nutrition"].setChecked(True)
    page.go_to(2)
    page.summary.defaults_button.click()
    row = page.material_step.rows[0]
    assert row.checkbox.isChecked()                                       # 畫面跟上
    assert page.state.default_materials == {"nutrition": [row.material.id]}   # 同步畫面不算人工勾選
    page.material_step.suggest_button.click()                             # 明確按「勾選所有建議的單張」→ 算人工
    assert page.state.default_materials == {} and page.state.materials == [row.material.id]


@pytest.mark.parametrize("keep_selections", [False, True])
def test_patient_switch_with_applied_defaults(env, qtbot, keep_selections):
    window, ctx, store, _fake = env
    window.go("referral")
    page = window.pages["referral"]
    _mark_defaults(store, ctx)
    page.domain_step.cards["nutrition"].setChecked(True)
    page.domain_step.cards["cognitive"].setChecked(True)
    page.apply_defaults()
    page.domain_step.cards["cognitive"].setChecked(False)                 # 認知的資源進 parked
    ctx.set_patient("B123456780", "林阿土", "query")
    assert not page.patient_banner.isHidden()
    page._bind_patient(keep_selections=keep_selections)
    assert page.state.parked == {}
    if keep_selections:
        assert page.state.defaults_applied == {"nutrition"} and page.state.materials
        page.domain_step.cards["nutrition"].setChecked(False)             # 保留下來的預設單張仍跟著項目走
        assert page.state.materials == []
    else:
        assert not page.state.has_selections() and page.state.materials == []
        assert page.state.defaults_applied == set() and page.state.default_materials == {}
    page.domain_step.cards["cognitive"].setChecked(True)
    assert page.state.resources["cognitive"] == []                        # 不繼承上一位的內容
    assert "本次帶入：認知" in page.summary.defaults_caption.text()


def test_defaults_applied_while_pdf_is_generating_are_printed_once_and_current(env, qtbot, monkeypatch):
    from pypdf import PdfReader
    window, ctx, store, _fake = env
    window.go("referral")
    page = window.pages["referral"]
    _mark_defaults(store, ctx, leaflet=False)
    page.domain_step.cards["cognitive"].setChecked(True)
    printed = []
    monkeypatch.setattr(page.confirm_step, "_print", lambda path: printed.append(path))
    page.print_now()                                                      # 背景正在產生「沒有資源」的舊內容
    assert page.confirm_step._generating
    page.go_to(0)
    page.apply_defaults()
    qtbot.waitUntil(lambda: bool(printed), timeout=30000)
    qtbot.wait(200)
    assert len(printed) == 1 and page.confirm_step.is_current
    assert "記憶門診" in "".join(p.extract_text() for p in PdfReader(str(printed[0])).pages)


def test_defaults_applied_during_a_preview_never_open_the_print_dialog(env, qtbot, monkeypatch):
    window, ctx, store, _fake = env
    window.go("referral")
    page = window.pages["referral"]
    _mark_defaults(store, ctx, leaflet=False)
    page.domain_step.cards["cognitive"].setChecked(True)
    printed = []
    monkeypatch.setattr(page.confirm_step, "_print", lambda path: printed.append(path))
    page.go_to(3)                                                         # 只是預覽
    assert page.confirm_step._generating
    page.apply_defaults()
    qtbot.waitUntil(lambda: page.confirm_step.is_current and not page.confirm_step._generating, timeout=30000)
    assert printed == []


def test_editors_expose_the_default_flag(env, qtbot):
    from icope_tool.ui.dialogs.editors import MaterialDialog, ResourceDialog
    window, _ctx, store, _fake = env
    dialog = ResourceDialog(window, [], None)
    dialog.name.setText("新的預設資源")
    dialog.include_default.setChecked(True)
    assert not dialog.default_hint.isHidden()                             # 沒勾適用項目：預設不會生效
    dialog.domains.boxes["vision"].setChecked(True)
    assert dialog.default_hint.isHidden()
    dialog._save()
    assert dialog.resource.include_by_default is True
    existing = next(r for r in store.list_resources() if r.name == "記憶門診")
    assert not ResourceDialog(window, [], existing).include_default.isChecked()
    material = MaterialDialog(window, None, "新單張.pdf", 1)                # 規格 1.2：兩個對話框都要有這個提示
    material.include_default.setChecked(True)
    assert not material.default_hint.isHidden()                           # 沒勾建議搭配的項目：院所預設不會生效
    material.domains.boxes["mobility"].setChecked(True)
    assert material.default_hint.isHidden()
    material = MaterialDialog(window, store.list_materials()[0])
    material.include_default.setChecked(True)
    material._save()
    assert material.material.include_by_default is True


def test_single_pdf_add_keeps_the_default_flag(env, qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from icope_tool.ui.dialogs.editors import MaterialDialog
    window, _ctx, store, _fake = env
    tab = window.pages["settings"].pages["materials"]

    def accept_as_default(dialog):
        dialog.include_default.setChecked(True)
        dialog.domains.boxes["mobility"].setChecked(True)
        dialog._save()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(MaterialDialog, "exec", accept_as_default)
    tab.add_paths([make_pdf(tmp_path / "防跌.pdf", text="fall")])
    added = next(m for m in store.list_materials() if m.name == "防跌")
    assert added.include_by_default is True and added.domains == ["mobility"]


def test_default_buttons_update_only_the_selected_records(env, qtbot):
    window, _ctx, store, _fake = env
    resources_tab = window.pages["settings"].pages["resources"]
    target = next(r for r in store.list_resources() if r.name == "記憶門診")
    resources_tab._select_ids({target.id})
    assert resources_tab.default_button.text() == "設為院所預設"
    resources_tab.toggle_default()
    assert {r.name: r.include_by_default for r in store.list_resources()} == {
        "本院門診追蹤": False, "記憶門診": True, "巷弄長照站": False}
    assert resources_tab.default_button.text() == "取消院所預設"
    headers = [resources_tab.model.horizontalHeaderItem(i).text() for i in range(resources_tab.model.columnCount())]
    assert "預設" in headers

    materials_tab = window.pages["settings"].pages["materials"]
    materials_tab.table.selectRow(0)
    materials_tab.toggle_default()
    assert store.list_materials()[0].include_by_default is True
    assert materials_tab.default_button.text() == "取消院所預設"


# ---------------------------------------------------------------------------- 第五輪：確認捷徑
def test_confirm_shortcut_only_navigates(env, qtbot, monkeypatch):
    window, *_ = env
    window.go("referral")
    page = window.pages["referral"]
    printed = []
    monkeypatch.setattr(page.confirm_step, "_print", lambda path: printed.append(path))
    page.domain_step.cards["cognitive"].setChecked(True)
    before = (list(page.state.domains), dict(page.state.resources), list(page.state.materials))
    page.confirm_shortcut.click()
    assert page.current == 3
    qtbot.waitUntil(lambda: page.confirm_step.is_current and not page.confirm_step._generating, timeout=30000)
    assert printed == []                                                  # 只前往確認頁，不會自己送印
    assert (list(page.state.domains), dict(page.state.resources), list(page.state.materials)) == before


def test_navigation_actions_remain_stable(env, qtbot):
    window, _ctx, store, _fake = env
    window.go("referral")
    page = window.pages["referral"]
    bar = page.layout().itemAt(page.layout().count() - 1).layout()       # 最底下那一列
    order = [bar.itemAt(i).widget() for i in range(bar.count()) if bar.itemAt(i).widget() is not None]
    assert order == [page.back_button, page.next_button, page.confirm_shortcut]

    def looks_right(shortcut_enabled: bool) -> None:
        assert page.next_button.property("kind") == "primary"
        assert page.confirm_shortcut.property("kind") == "secondary"
        assert not page.confirm_shortcut.isHidden() and page.confirm_shortcut.isEnabled() is shortcut_enabled

    looks_right(False)                                                    # 什麼都沒選：捷徑不能按
    assert page.next_button.isEnabled()                                   # 但仍可照原路去選單張（含說明頁的文件）
    page.domain_step.cards["hearing"].setChecked(True)                    # 有項目、沒有資源：也是完整決定
    looks_right(True)
    page.go_to(1)
    looks_right(True)
    assert page.next_button.text() == "下一步：選衛教單張" and page.back_button.isEnabled()
    page.domain_step.cards["hearing"].setChecked(False)
    page.state.set_material(store.list_materials()[0].id, True)           # 只有單張
    page._on_changed()
    looks_right(True)
    page.go_to(2)
    assert page.confirm_shortcut.isHidden() and page.next_button.text() == "下一步：確認與列印"
    page.go_to(3)
    assert page.confirm_shortcut.isHidden() and page.next_button.isHidden()


# ---------------------------------------------------------------------------- 第五輪：只印衛教單張
def _add_fall_leaflet(store, ctx, tmp_path):
    added = store.add_material_file(make_pdf(tmp_path / "防跌.pdf", pages=1, text="fall"), domains=["mobility"])
    ctx.mark_saved("materials")
    return added


def _open_leaflets(page, qtbot, monkeypatch, printer=None, opener=None):
    """從頁首按鈕開啟對話框（非阻塞），列印與外部開啟都換成替身。回傳 (dialog, 送印過的檔案)。"""
    import icope_tool.ui.dialogs.leaflet_print as leaflet_module
    printed = []

    def fake_printer(_parent, path):
        printed.append(path)
        return True

    monkeypatch.setattr(leaflet_module, "print_pdf", printer or fake_printer)
    monkeypatch.setattr(leaflet_module, "open_in_viewer", opener or (lambda _path: True))
    scratch = page.ctx.store.root.parent / "leaflet-out"                  # 不寫進使用者真正的暫存資料夾
    monkeypatch.setattr(leaflet_module, "temp_output_dir", lambda: scratch)
    page.leaflet_button.click()
    dialog = page._leaflet_dialog
    qtbot.waitUntil(dialog.isVisible, timeout=5000)
    return dialog, printed


def _leaflet_action(dialog, text):
    from PySide6.QtWidgets import QPushButton
    return next((b for b in dialog.error_banner.findChildren(QPushButton) if b.text() == text and not b.isHidden()), None)


def _gate(monkeypatch, name):
    """讓背景的合併或來源驗證停在半路，測試決定什麼時候放行。"""
    import threading

    import icope_tool.ui.dialogs.leaflet_print as leaflet_module
    gate, calls = threading.Event(), []
    real = getattr(leaflet_module, name)

    def slow(*args):
        calls.append(args)
        gate.wait(15)
        return real(*args)

    monkeypatch.setattr(leaflet_module, name, slow)
    return gate, calls


def _failing_printer(attempts):
    from icope_tool.ui.printing import PrintError

    def printer(_parent, path):
        attempts.append(path)
        if len(attempts) == 1:
            raise PrintError("印表機離線")
        return True

    return printer


def test_leaflet_dialog_prints_the_ticked_pdfs_without_a_cover(env, qtbot, tmp_path, monkeypatch):
    from pypdf import PdfReader
    window, ctx, store, _fake = env
    _add_fall_leaflet(store, ctx, tmp_path)
    window.go("referral")
    page = window.pages["referral"]
    printed = []

    def printer(_parent, path):                                          # 送印當下讀內容（印完暫存檔就會清掉）
        printed.append((path, [p.extract_text() for p in PdfReader(str(path)).pages]))
        return True

    dialog, _unused = _open_leaflets(page, qtbot, monkeypatch, printer=printer)   # 第 1 次點擊：開啟
    assert [row.material.name for row in dialog.rows] == ["營養", "防跌"]
    assert not dialog.print_button.isEnabled()                           # 沒勾不能印
    dialog.print_selected()
    assert printed == [] and dialog._request == 0
    for row in dialog.rows:                                               # N 次點擊
        qtbot.mouseClick(row, Qt.MouseButton.LeftButton)
    assert "已選 2 份，共 3 頁" in dialog.count_label.text()
    qtbot.mouseClick(dialog.print_button, Qt.MouseButton.LeftButton)      # 最後 1 次：列印
    qtbot.waitUntil(lambda: bool(printed), timeout=30000)
    path, texts = printed[0]
    assert len(texts) == 3 and "hello" in texts[0] and "fall" in texts[2]     # 依清單順序、沒有首頁
    assert not any("測試診所" in text for text in texts)
    qtbot.waitUntil(lambda: page._leaflet_dialog is None, timeout=5000)
    assert "所選文件共 2 份、3 頁" in page.toast.text() and not path.exists()   # 印完暫存檔就清掉
    assert "leaflet_print" in ctx.audit.path.read_text(encoding="utf-8")


def test_leaflet_printing_does_not_require_query_credentials(env, qtbot, monkeypatch):
    window, ctx, store, _fake = env
    (store.auth_dir / "hpdcs_credentials.json").unlink()
    store.update_settings(lambda s: setattr(s.clinic, "name", ""))
    ctx.mark_saved("settings", "hpdcs")
    window.go("referral")
    page = window.pages["referral"]
    dialog, printed = _open_leaflets(page, qtbot, monkeypatch)
    dialog.rows[0].checkbox.setChecked(True)
    dialog.print_selected()
    qtbot.waitUntil(lambda: bool(printed), timeout=30000)


def test_leaflet_dialog_never_changes_the_referral_draft(env, qtbot, tmp_path, monkeypatch):
    window, ctx, store, _fake = env
    window.go("referral")
    page = window.pages["referral"]
    ctx.set_patient("A123456789", "王小明")
    _select_cognitive_with_resource(page, qtbot)
    page.domain_step.cards["mobility"].setChecked(True)
    page.state.add_to_domain(["x"], "mobility")
    page.domain_step.cards["mobility"].setChecked(False)                  # parked
    page.state.set_material(store.list_materials()[0].id, True)
    page._on_changed()
    confirm = page.confirm_step

    def snapshot():
        state = page.state
        return (list(state.domains), {k: list(v) for k, v in state.resources.items()}, list(state.materials),
                {k: list(v) for k, v in state.parked.items()}, state.patient_pid, state.patient_name,
                confirm._version, confirm._done_version, page.has_unfinished_work(), page.current)

    before = snapshot()
    attempts = []
    dialog, _printed = _open_leaflets(page, qtbot, monkeypatch, printer=_failing_printer(attempts))
    dialog.rows[0].checkbox.setChecked(False)                             # 對話框有自己的勾選，不是草稿的
    dialog.rows[0].checkbox.setChecked(True)
    dialog.print_selected()
    qtbot.waitUntil(lambda: len(attempts) == 1 and not dialog._busy, timeout=30000)     # 送印失敗
    assert snapshot() == before
    _leaflet_action(dialog, "重試列印").click()
    qtbot.waitUntil(lambda: page._leaflet_dialog is None, timeout=30000)                  # 送印成功
    assert snapshot() == before
    dialog, _printed = _open_leaflets(page, qtbot, monkeypatch)
    dialog.reject()                                                       # 取消
    assert snapshot() == before


def test_leaflet_dialog_discards_late_results(env, qtbot, monkeypatch):
    from shiboken6 import isValid
    window, _ctx, _store, _fake = env
    window.go("referral")
    page = window.pages["referral"]
    gate, merges = _gate(monkeypatch, "merge_material_pdfs")
    dialog, printed = _open_leaflets(page, qtbot, monkeypatch)
    dialog.rows[0].checkbox.setChecked(True)
    dialog.print_selected()
    dialog.print_selected()                                               # 連按：不會有第二個工作
    assert dialog._busy and not dialog.rows[0].isEnabled()
    qtbot.waitUntil(lambda: len(merges) == 1, timeout=5000)
    output = merges[0][1]
    dialog.reject()                                                       # Esc／關閉：這次請求作廢
    assert page._leaflet_dialog is None and isValid(dialog)               # 工作還沒回來：先留著接收回呼

    second, second_printed = _open_leaflets(page, qtbot, monkeypatch)     # 立刻開新的
    second.rows[0].checkbox.setChecked(True)
    gate.set()
    qtbot.waitUntil(lambda: not isValid(dialog), timeout=30000)           # 回呼清理完才銷毀
    assert printed == [] and not output.exists()
    assert second.isVisible() and second.rows[0].checkbox.isChecked() and second_printed == []
    second.reject()
    qtbot.waitUntil(lambda: not isValid(second), timeout=5000)            # 沒有工作的對話框關了就銷毀


def test_leaflet_print_failure_cancel_and_merge_failure(env, qtbot, tmp_path, monkeypatch):
    window, ctx, store, _fake = env
    extra = _add_fall_leaflet(store, ctx, tmp_path)
    window.go("referral")
    page = window.pages["referral"]
    attempts = []
    dialog, _printed = _open_leaflets(page, qtbot, monkeypatch, printer=_failing_printer(attempts))
    dialog.rows[0].checkbox.setChecked(True)
    dialog.print_selected()
    qtbot.waitUntil(lambda: len(attempts) == 1 and not dialog._busy, timeout=30000)
    assert "列印失敗" in dialog.error_banner._title.text()
    assert _leaflet_action(dialog, "重試列印") and _leaflet_action(dialog, "用 PDF 閱讀器開啟")
    assert dialog.isVisible() and dialog.rows[0].checkbox.isChecked() and dialog.summary is None
    _leaflet_action(dialog, "重試列印").click()
    qtbot.waitUntil(lambda: page._leaflet_dialog is None, timeout=30000)
    assert len(attempts) == 2 and attempts[0] == attempts[1]             # 重試用的是同一份驗證過的合併檔

    import icope_tool.ui.dialogs.leaflet_print as leaflet_module
    dialog, _printed = _open_leaflets(page, qtbot, monkeypatch, printer=lambda _p, _path: False)
    dialog.rows[0].checkbox.setChecked(True)
    dialog.print_selected()
    qtbot.waitUntil(lambda: dialog._request == 1 and not dialog._busy, timeout=30000)
    assert dialog.isVisible() and dialog.summary is None                 # 系統列印視窗按取消：不算完成
    assert dialog.error_banner.isHidden()

    store.material_file(extra).unlink()                                   # 來源檔不見：合併失敗
    outputs = []
    real_merge = leaflet_module.merge_material_pdfs
    monkeypatch.setattr(leaflet_module, "merge_material_pdfs",
                        lambda sources, output: outputs.append(output) or real_merge(sources, output))
    dialog.rows[1].checkbox.setChecked(True)
    dialog.print_selected()
    qtbot.waitUntil(lambda: not dialog.error_banner.isHidden() and not dialog._busy, timeout=30000)
    assert "防跌" in dialog.error_banner._text.text()
    assert _leaflet_action(dialog, "重試") and _leaflet_action(dialog, "用 PDF 閱讀器開啟") is None
    assert len(outputs) == 1 and not outputs[0].exists()                  # 這次工作的半成品已清掉
    dialog.reject()


@pytest.mark.parametrize("change", ["swap", "disable", "delete", "offline"])
@pytest.mark.parametrize("entry", ["重試列印", "用 PDF 閱讀器開啟"])
def test_recovery_outputs_revalidate_their_sources(env, qtbot, tmp_path, monkeypatch, change, entry):
    import os

    from icope_tool.store import StoreIOError
    window, ctx, store, _fake = env
    other = _add_fall_leaflet(store, ctx, tmp_path)
    window.go("referral")
    page = window.pages["referral"]
    attempts, opened = [], []
    dialog, _printed = _open_leaflets(page, qtbot, monkeypatch, printer=_failing_printer(attempts),
                                      opener=lambda path: opened.append(path) or True)
    dialog.rows[0].checkbox.setChecked(True)
    dialog.print_selected()
    qtbot.waitUntil(lambda: len(attempts) == 1 and not dialog._busy, timeout=30000)
    store.patch_materials([other.id], name="沒勾的單張改了名")           # 沒勾選的來源變動：不影響
    target = store.list_materials()[0]
    path = store.material_file(target)
    if change == "swap":                                                  # 同一個檔名、清單沒動，內容被換掉
        stat = path.stat()
        path.write_bytes(make_pdf(tmp_path / "swapped.pdf", pages=2, text="swapped").read_bytes())
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    elif change == "disable":
        store.patch_materials([target.id], enabled=False)
    elif change == "delete":
        store.delete_materials([target.id])
    else:
        def broken():
            raise StoreIOError("網路磁碟暫時斷線")
        monkeypatch.setattr(ctx.store, "list_materials", broken)
    _leaflet_action(dialog, entry).click()
    qtbot.waitUntil(lambda: not dialog._busy, timeout=30000)
    qtbot.wait(100)
    assert len(attempts) == 1 and opened == []                            # 舊的合併檔不再拿來輸出
    assert dialog.isVisible() and dialog.summary is None
    assert _leaflet_action(dialog, "重試列印") is None and _leaflet_action(dialog, "用 PDF 閱讀器開啟") is None
    dialog.reject()


@pytest.mark.parametrize("entry", ["重試列印", "用 PDF 閱讀器開啟"])
def test_leaflet_disabled_while_sources_are_being_verified_is_not_output(env, qtbot, monkeypatch, entry):
    """原生 Codex 審查 P2：背景驗證進行中別台電腦停用了勾選的單張——內容雜湊仍會通過，回來後要再核對一次清單。"""
    window, ctx, store, _fake = env
    window.go("referral")
    page = window.pages["referral"]
    attempts, opened = [], []
    dialog, _printed = _open_leaflets(page, qtbot, monkeypatch, printer=_failing_printer(attempts),
                                      opener=lambda path: opened.append(path) or True)
    dialog.rows[0].checkbox.setChecked(True)
    dialog.print_selected()
    qtbot.waitUntil(lambda: len(attempts) == 1 and not dialog._busy, timeout=30000)
    gate, verifications = _gate(monkeypatch, "verify_material_sources")
    _leaflet_action(dialog, entry).click()
    qtbot.waitUntil(lambda: len(verifications) == 1, timeout=5000)
    store.patch_materials([store.list_materials()[0].id], enabled=False)   # 驗證還在跑，清單變了
    ctx.mark_saved("materials")                                             # 忙碌中：只會延後刷新
    gate.set()
    qtbot.waitUntil(lambda: not dialog._busy, timeout=30000)
    qtbot.wait(100)
    assert len(attempts) == 1 and opened == []                              # 已停用的單張不會被印出或開啟
    assert _leaflet_action(dialog, "重試列印") is None and dialog.summary is None
    assert dialog.rows == []                                                # 補做的刷新：清單已沒有這份單張
    dialog.reject()


def test_recovery_actions_keep_working_after_cancelling_a_retried_print(env, qtbot, monkeypatch):
    """原生 Codex 審查 P2：重試時在系統列印視窗按取消，畫面上的「重試列印／用 PDF 閱讀器開啟」不能變成按了沒反應。"""
    from icope_tool.ui.printing import PrintError
    window, *_ = env
    window.go("referral")
    page = window.pages["referral"]
    attempts = []

    def printer(_parent, path):
        attempts.append(path)
        if len(attempts) == 1:
            raise PrintError("印表機離線")
        return len(attempts) >= 3                                          # 第 2 次：使用者在系統列印視窗按了取消

    dialog, _printed = _open_leaflets(page, qtbot, monkeypatch, printer=printer)
    dialog.rows[0].checkbox.setChecked(True)
    dialog.print_selected()
    qtbot.waitUntil(lambda: len(attempts) == 1 and not dialog._busy, timeout=30000)
    _leaflet_action(dialog, "重試列印").click()
    qtbot.waitUntil(lambda: len(attempts) == 2 and not dialog._busy, timeout=30000)
    assert dialog.isVisible() and dialog.summary is None
    retry = _leaflet_action(dialog, "重試列印")
    assert retry is not None and _leaflet_action(dialog, "用 PDF 閱讀器開啟") is not None
    retry.click()                                                          # 看得到的按鈕就要有作用
    qtbot.waitUntil(lambda: page._leaflet_dialog is None, timeout=30000)
    assert len(attempts) == 3 and attempts[1] == attempts[2]


def test_unticked_leaflet_changes_do_not_block_a_retry(env, qtbot, tmp_path, monkeypatch):
    window, ctx, store, _fake = env
    other = _add_fall_leaflet(store, ctx, tmp_path)
    window.go("referral")
    page = window.pages["referral"]
    attempts = []
    dialog, _printed = _open_leaflets(page, qtbot, monkeypatch, printer=_failing_printer(attempts))
    dialog.rows[0].checkbox.setChecked(True)
    dialog.print_selected()
    qtbot.waitUntil(lambda: len(attempts) == 1 and not dialog._busy, timeout=30000)
    store.patch_materials([other.id], enabled=False)
    _leaflet_action(dialog, "重試列印").click()
    qtbot.waitUntil(lambda: len(attempts) == 2, timeout=30000)


def test_data_updates_during_printing_cannot_reenter_the_dialog(env, qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    window, ctx, store, _fake = env
    window.go("referral")
    page = window.pages["referral"]
    seen = {}

    def printer_with_events(_parent, path):
        rows_before = list(dialog.rows)
        _add_fall_leaflet(store, ctx, tmp_path)                           # 系統列印視窗開著時，別台電腦新增了單張
        dialog.print_selected()                                           # 排隊的點擊事件
        QApplication.processEvents()
        seen.update(busy=dialog._busy, same_rows=dialog.rows == rows_before, file=path.exists(),
                    requests=dialog._request)
        return False                                                      # 使用者最後按了取消

    dialog, _printed = _open_leaflets(page, qtbot, monkeypatch, printer=printer_with_events)
    dialog.rows[0].checkbox.setChecked(True)
    dialog.print_selected()
    qtbot.waitUntil(lambda: bool(seen) and not dialog._busy, timeout=30000)
    assert seen == {"busy": True, "same_rows": True, "file": True, "requests": 1}
    assert [row.material.name for row in dialog.rows] == ["營養", "防跌"]     # 回到閒置後補上這次更新
    assert dialog.rows[0].checkbox.isChecked()
    dialog.reject()


def test_external_viewer_takes_over_the_file(env, qtbot, monkeypatch):
    window, _ctx, _store, _fake = env
    window.go("referral")
    page = window.pages["referral"]
    attempts, opened = [], []
    answers = [False, True]
    dialog, _printed = _open_leaflets(page, qtbot, monkeypatch, printer=_failing_printer(attempts),
                                      opener=lambda path: opened.append(path) or answers[len(opened) - 1])
    dialog.rows[0].checkbox.setChecked(True)
    dialog.print_selected()
    qtbot.waitUntil(lambda: len(attempts) == 1 and not dialog._busy, timeout=30000)
    _leaflet_action(dialog, "用 PDF 閱讀器開啟").click()
    qtbot.waitUntil(lambda: len(opened) == 1 and not dialog._busy, timeout=30000)
    assert "找不到可以開啟 PDF 的程式" in dialog.error_banner._title.text()      # 開啟失敗：不算完成
    assert dialog.summary is None and opened[0].exists()
    _leaflet_action(dialog, "用 PDF 閱讀器開啟").click()
    qtbot.waitUntil(lambda: len(opened) == 2 and not dialog._busy, timeout=30000)
    handed_over = opened[1]
    dialog.rows[0].checkbox.setChecked(False)                             # 改勾選、關閉都不能刪掉閱讀器正在用的檔案
    dialog.rows[0].checkbox.setChecked(True)
    dialog.reject()
    assert handed_over.exists()


def test_cancel_works_while_sources_are_being_verified(env, qtbot, monkeypatch):
    window, _ctx, _store, _fake = env
    window.go("referral")
    page = window.pages["referral"]
    attempts = []
    dialog, _printed = _open_leaflets(page, qtbot, monkeypatch, printer=_failing_printer(attempts))
    dialog.rows[0].checkbox.setChecked(True)
    dialog.print_selected()
    qtbot.waitUntil(lambda: len(attempts) == 1 and not dialog._busy, timeout=30000)
    gate, verifications = _gate(monkeypatch, "verify_material_sources")    # 共用資料夾很慢：驗證卡住
    _leaflet_action(dialog, "重試列印").click()
    qtbot.waitUntil(lambda: len(verifications) == 1, timeout=5000)
    assert dialog._busy and dialog.cancel_button.isEnabled()
    qtbot.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)     # 等待期間畫面仍能處理取消
    assert not dialog.isVisible() and page._leaflet_dialog is None
    gate.set()
    qtbot.wait(300)
    assert len(attempts) == 1                                             # 晚到的驗證結果不會送印


# ---------------------------------------------------------------------------- 第五輪：各項衛教重點
def _notes_tab(window):
    window.go("settings", "clinic")
    return window.pages["settings"].pages["clinic"]


def _pick_domain(tab, domain_id):
    tab.note_domain.setCurrentIndex(tab.note_domain.findData(domain_id))


def test_note_typed_last_is_saved_without_switching_domains(env, qtbot):
    window, _ctx, store, _fake = env
    tab = _notes_tab(window)
    DataStore(store.root).update_settings(lambda s: setattr(s.hpdcs, "pilot", False))   # 別台電腦改了別的設定區塊
    assert not tab.save_button.isEnabled()
    tab.note_text.setPlainText("  多動腦、多互動\r\n每天和家人聊聊今天的事  ")            # 打完直接存，沒有換項目
    assert tab.save_button.isEnabled() and tab.has_unsaved_changes()
    tab.save()
    saved = store.load_settings()
    assert saved.sheet.domain_notes == {"cognitive": "多動腦、多互動\n每天和家人聊聊今天的事"}
    assert saved.hpdcs.pilot is False and saved.clinic.name == "測試診所"              # 只改自己那一塊
    assert not tab.has_unsaved_changes() and not tab.save_button.isEnabled()          # 正規化後不會又變成未儲存
    assert "已填" in tab.note_domain.currentText()


def test_note_drafts_survive_switching_and_revert_restores_saved_text(env, qtbot):
    window, _ctx, store, _fake = env
    store.update_settings(lambda s: s.sheet.domain_notes.update({"hearing": "已經存好的聽力重點"}))
    tab = _notes_tab(window)
    tab.load()
    tab.note_text.setPlainText("認知草稿")
    _pick_domain(tab, "hearing")
    assert tab.note_text.toPlainText() == "已經存好的聽力重點"
    tab.note_text.setPlainText("聽力草稿")
    _pick_domain(tab, "cognitive")
    assert tab.note_text.toPlainText() == "認知草稿"                                   # 切換項目不會弄丟，也不會寫錯項目
    _pick_domain(tab, "hearing")
    assert tab.note_text.toPlainText() == "聽力草稿"
    tab.revert_button.click()                                                         # 復原＝明確捨棄，回到已儲存的內容
    assert tab.note_text.toPlainText() == "已經存好的聽力重點" and not tab.has_unsaved_changes()
    _pick_domain(tab, "cognitive")
    assert tab.note_text.toPlainText() == ""


@pytest.mark.parametrize("text, ok", [("字" * 200, True), ("字" * 201, False), ("行\n" * 5 + "行", True),
                                      ("行\n" * 6 + "行", False), ("字" * 100 + "\r\n" + "字" * 100 + "  ", True)],
                         ids=["200-chars", "201-chars", "6-lines", "7-lines", "crlf-and-edge-spaces"])
def test_note_limits_are_judged_the_same_way_by_editor_and_model(env, qtbot, text, ok):
    from icope_tool.models import normalize_note, note_problem
    window, _ctx, store, _fake = env
    tab = _notes_tab(window)
    tab.note_text.setPlainText(text)
    assert (note_problem(text) == "") is ok
    assert (tab.note_count.property("role") == "caption") is ok
    tab.save()
    assert bool(store.load_settings().sheet.domain_notes) is ok
    assert tab.has_unsaved_changes() is not ok                                        # 超限：沒有存、草稿還在
    # 存成功：畫面換成實際存下來的內容；沒存成功：使用者打的字原封不動（換行是 Qt 自己統一成 \n 的）
    assert tab.note_text.toPlainText() == (normalize_note(text) if ok else text.replace("\r\n", "\n"))
    tab.load()


def test_over_limit_note_in_another_domain_is_brought_into_view(env, qtbot):
    window, *_ = env
    tab = _notes_tab(window)
    _pick_domain(tab, "social")
    tab.note_text.setPlainText("字" * 230)
    _pick_domain(tab, "cognitive")
    tab.note_text.setPlainText("這一項沒問題")
    tab.save()
    assert tab.note_domain.currentData() == "social" and "超過 200 字" in tab.note_count.text()
    tab.load()


def test_empty_clinic_name_explains_itself_instead_of_disabling_save(env, qtbot):
    """第五輪規格 4.2：有變更就能按儲存；診所名稱空白時標出欄位並說明，不再默默停用按鈕。"""
    window, _ctx, store, _fake = env
    tab = _notes_tab(window)
    tab.note_text.setPlainText("只想改衛教重點")
    tab.name.setText("")
    assert tab.save_button.isEnabled()
    tab.save()
    assert not tab.name_error.isHidden() and store.load_settings().sheet.domain_notes == {}
    assert tab.note_text.toPlainText() == "只想改衛教重點"
    tab.load()


def test_note_drafts_survive_conflict_cancel_and_write_failure(env, qtbot, dialogs, monkeypatch):
    from icope_tool.store import StoreIOError
    window, ctx, store, _fake = env
    tab = _notes_tab(window)
    tab.note_text.setPlainText("我的草稿")
    DataStore(store.root).update_settings(lambda s: s.sheet.domain_notes.update({"cognitive": "別台電腦寫的"}))
    tab.save()                                                                        # 問要不要覆蓋 → 預設回答取消
    assert "診所資訊已被其他電腦修改" in dialogs.asked
    assert store.load_settings().sheet.domain_notes == {"cognitive": "別台電腦寫的"}
    assert tab.note_text.toPlainText() == "我的草稿" and tab.has_unsaved_changes()

    def broken(_mutate):
        raise StoreIOError("網路磁碟暫時斷線")

    monkeypatch.setattr(ctx.store, "update_settings", broken)
    dialogs.answer = QMessageBox_Yes()
    tab.save()
    assert not tab.error.isHidden() and tab.note_text.toPlainText() == "我的草稿" and tab.has_unsaved_changes()
    monkeypatch.undo()
    tab.load()


def QMessageBox_Yes():
    from PySide6.QtWidgets import QMessageBox
    return QMessageBox.StandardButton.Yes


def test_saved_note_reaches_the_sheet_and_outdates_the_current_pdf(env, qtbot):
    from pypdf import PdfReader
    window, _ctx, _store, _fake = env
    page = window.pages["referral"]
    page.domain_step.cards["cognitive"].setChecked(True)
    page.go_to(3)
    confirm = page.confirm_step
    qtbot.waitUntil(lambda: confirm.is_current and not confirm._generating, timeout=30000)
    tab = _notes_tab(window)
    tab.note_text.setPlainText("多動腦、多互動")
    tab.save()
    assert not confirm.is_current                                                     # 舊 PDF 過期，不會印出沒有衛教重點的版本
    window.go("referral")
    confirm.ensure_preview()
    qtbot.waitUntil(lambda: confirm.is_current and not confirm._generating, timeout=30000)
    assert "多動腦、多互動" in "".join(p.extract_text() for p in PdfReader(str(confirm._pdf_path)).pages)


def test_save_bar_stays_outside_the_scrolling_settings(env, qtbot):
    window, *_ = env
    tab = _notes_tab(window)
    scroll_children = tab.scroll_view.widget().findChildren(type(tab.save_button))
    assert tab.save_button not in scroll_children and tab.revert_button not in scroll_children
    assert tab.note_text in tab.scroll_view.widget().findChildren(type(tab.note_text))


# ---------------------------------------------------------------------------- 第五輪：依任務說明設定、日常路徑
def test_setup_checklist_says_what_each_item_is_for_without_a_total_score(env, qtbot):
    window, _ctx, store, _fake = env
    checklist = window.pages["query"].checklist
    (store.auth_dir / "hpdcs_credentials.json").unlink()
    store.delete_materials([m.id for m in store.list_materials()])
    assert checklist.refresh() is True                                                # 還有沒做的：顯示
    assert checklist.title_label.text() == "依需要完成設定"                            # 不再用「完成 X/4」暗示四項都必做
    captions = {key: status.text() for key, (_mark, status, _go) in checklist.tiles.items()}
    assert captions["clinic"] == "已完成" and captions["resources"] == "已完成"        # 各項仍有自己的狀態
    assert "只有篩檢查詢需要" in captions["hpdcs"]                                     # 不用查詢的院所看得出可以跳過
    assert "單獨列印" in captions["materials"]


def test_the_frozen_daily_path_is_six_clicks_to_the_printer(env, qtbot, monkeypatch):
    """第五輪凍結的點擊數：三項異常＋院所預設 → 3 次勾選＋帶入＋確認與列印＋列印＝6 次，到達（假的）印表機且內容正確。"""
    import icope_tool.ui.referral.confirm as confirm_module
    from pypdf import PdfReader
    window, ctx, store, _fake = env
    store.add_resource(Resource(name="營養諮詢門診", domains=["nutrition"], include_by_default=True))
    _mark_defaults(store, ctx)
    ctx.set_patient("A123456789", "王小明")
    window.go("referral")
    page = window.pages["referral"]
    printed = []

    def printer(_parent, path):
        printed.append("".join(p.extract_text() for p in PdfReader(str(path)).pages))
        return True

    monkeypatch.setattr(confirm_module, "print_pdf", printer)
    clicks = [page.domain_step.cards["cognitive"], page.domain_step.cards["mobility"],
              page.domain_step.cards["nutrition"], page.summary.defaults_button, page.confirm_shortcut,
              page.confirm_step.print_button]
    for widget in clicks:
        qtbot.waitUntil(lambda w=widget: w.isVisible() and w.isEnabled(), timeout=30000)
        qtbot.mouseClick(widget, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: bool(printed), timeout=30000)
    assert len(clicks) == 6 and len(printed) == 1
    for expected in ("王小明", "記憶門診", "巷弄長照站", "營養諮詢門診", "隨附衛教資料"):
        assert expected in printed[0]


def test_old_outputs_of_both_kinds_are_cleaned_after_a_day(tmp_path):
    import os
    import time

    from icope_tool.ui.referral.confirm import cleanup_old_outputs
    old = time.time() - 25 * 3600
    names = ["ICOPE轉介單_舊.pdf", "ICOPE衛教單張_舊.pdf", "別的程式的檔案.pdf"]
    for name in names:
        (tmp_path / name).write_bytes(b"x")
        os.utime(tmp_path / name, (old, old))
    (tmp_path / "ICOPE衛教單張_新.pdf").write_bytes(b"x")
    cleanup_old_outputs(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ICOPE衛教單張_新.pdf", "別的程式的檔案.pdf"]
