"""名稱只在一個地方定義：顯示名稱可以改，內部識別碼不能改。"""
from __future__ import annotations

from pathlib import Path

from icope_tool import APP_DISPLAY_NAME, APP_NAME

PACKAGE = Path(__file__).resolve().parent.parent / "icope_tool"


def test_display_name_and_internal_id():
    assert APP_DISPLAY_NAME == "ICOPE 小幫手"
    # 本機設定與紀錄放在 %LOCALAPPDATA%\<APP_NAME>，exe 也叫這個名字：改了的話，已安裝的電腦會找不到原本的設定
    assert APP_NAME == "IcopeTool"


def test_no_module_spells_the_name_out_by_hand():
    """畫面、歡迎視窗、PDF 的 Creator 都要用 APP_DISPLAY_NAME；寫死的話下次改名會漏掉。"""
    offenders = [str(path.relative_to(PACKAGE)) for path in PACKAGE.rglob("*.py")
                 if path.name != "__init__.py" and any(word in path.read_text(encoding="utf-8")
                                                        for word in ("篩檢轉介助手", "ICOPE 小幫手"))]
    assert offenders == []


def test_generated_pdf_names_the_app_as_its_creator(tmp_path):
    from pypdf import PdfReader

    from icope_tool.models import ClinicInfo, Resource
    from icope_tool.services.pdf.referral_sheet import ReferralJob, ReferralSection, build_referral_pdf
    job = ReferralJob(clinic=ClinicInfo(name="示範診所"), patient_name="測試長者",
                      sections=[ReferralSection("cognitive", [Resource(name="記憶門診", phones=["05-0000000"])])])
    result = build_referral_pdf(job, {}, tmp_path / "sheet.pdf")
    assert PdfReader(str(result.path)).metadata.creator == APP_DISPLAY_NAME


def test_welcome_dialog_shows_the_display_name(qtbot):
    from PySide6.QtWidgets import QLabel

    from icope_tool.ui.dialogs.data_folder import DataFolderDialog
    dialog = DataFolderDialog(None, "first_run")
    qtbot.addWidget(dialog)
    assert any(APP_DISPLAY_NAME in label.text() for label in dialog.findChildren(QLabel))
