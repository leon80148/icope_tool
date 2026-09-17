"""轉介與衛教單（A4）排版，以及與衛教單張 PDF 的合併。

字型內附 Noto Sans TC（SIL OFL 1.1）的靜態 Regular / Bold：可變字型在 fpdf2 每次實例化要 20 秒以上，
所以打包前先用 fontTools 產生靜態檔（見 tools/make_static_fonts.py）。
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import MethodReturnValue, WrapMode, XPos, YPos

from icope_tool import APP_DISPLAY_NAME
from icope_tool.domains import DOMAIN_BY_ID
from icope_tool.models import ClinicInfo, Material, Resource, normalize_note, note_problem

FONT_DIR = Path(__file__).resolve().parent / "fonts"
FONT = "NotoSansTC"

INK = (15, 23, 42)          # slate-900
SUBTLE = (71, 85, 105)      # slate-600
MUTED = (100, 116, 139)     # slate-500
RULE = (203, 213, 225)      # slate-300
PANEL = (248, 250, 252)     # slate-50
BRAND = (17, 94, 89)        # teal-800

DETAIL_PT = 13          # 資源電話、時間、地址
DETAIL_LINE = 7.0
NOTE_PT = 12.5          # 各項衛教重點
NOTE_LINE = 7.0
NOTE_INDENT = 3.0

TOP_MARGIN = 16.0
BOTTOM_MARGIN = 22.0
CONTINUED_HEADER = 10.0     # 續頁頁首占的高度（一行 5 mm＋間距 5 mm）；算「一整頁放得下多少」時要扣掉

PAGE_LEFT = 16.0
PAGE_RIGHT = 16.0
CONTENT_W = 210.0 - PAGE_LEFT - PAGE_RIGHT


class PdfBuildError(Exception):
    """產生 PDF 失敗（訊息可直接顯示）。"""


@dataclass
class ReferralSection:
    domain_id: str
    resources: list[Resource] = field(default_factory=list)


@dataclass
class ReferralJob:
    clinic: ClinicInfo
    sections: list[ReferralSection]
    materials: list[Material] = field(default_factory=list)
    patient_name: str = ""
    assessor: str = ""
    notes: str = ""
    assessed_on: date = field(default_factory=date.today)
    domain_notes: dict[str, str] = field(default_factory=dict)     # 項目 id → 院所寫的衛教重點（這份工作專用的複本）


def roc_date(value: date) -> str:
    return f"民國 {value.year - 1911} 年 {value.month} 月 {value.day} 日"


def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _tint(rgb: tuple[int, int, int], amount: float = 0.9) -> tuple[int, int, int]:
    r, g, b = rgb
    return (round(r + (255 - r) * amount), round(g + (255 - g) * amount), round(b + (255 - b) * amount))


def resource_detail_lines(resource: Resource) -> list[str]:
    parts = []
    if resource.phones:
        parts.append("電話 " + "、".join(resource.phones))
    if resource.hours:
        parts.append("時間 " + resource.hours)
    if resource.contact:
        parts.append("聯絡人 " + resource.contact)
    lines = ["　".join(parts)] if parts else []
    if resource.address:
        lines.append("地址 " + resource.address)
    if resource.website:
        lines.append("網站 " + resource.website)
    return lines


class _Sheet(FPDF):
    def __init__(self, job: ReferralJob):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.job = job
        self.set_margins(PAGE_LEFT, TOP_MARGIN, PAGE_RIGHT)
        self.set_auto_page_break(auto=True, margin=BOTTOM_MARGIN)
        try:
            self.add_font(FONT, "", str(FONT_DIR / "NotoSansTC-Regular.ttf"))
            self.add_font(FONT, "B", str(FONT_DIR / "NotoSansTC-Bold.ttf"))
        except (OSError, RuntimeError) as exc:
            raise PdfBuildError("找不到 PDF 中文字型檔，請重新安裝程式") from exc
        self.alias_nb_pages()
        self.set_title("長者功能評估轉介與衛教單")
        self.set_creator(APP_DISPLAY_NAME)

    # 續頁頁首
    def header(self):
        if self.page_no() == 1:
            return
        self.set_font(FONT, "", 8.5)
        self.set_text_color(*MUTED)
        title = f"{self.job.clinic.name}　長者功能評估轉介與衛教單（續）".strip()
        self.cell(CONTENT_W, 5, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(*RULE)
        self.line(PAGE_LEFT, self.get_y() + 1, PAGE_LEFT + CONTENT_W, self.get_y() + 1)
        self.ln(5)

    def footer(self):
        clinic = self.job.clinic
        self.set_y(-17)
        self.set_draw_color(*RULE)
        self.line(PAGE_LEFT, self.get_y(), PAGE_LEFT + CONTENT_W, self.get_y())
        self.ln(1.5)
        self.set_font(FONT, "", 8.5)
        self.set_text_color(*SUBTLE)
        contact = "　".join(p for p in (
            clinic.name,
            f"電話 {clinic.phone}" if clinic.phone else "",
            clinic.address,
        ) if p)
        self.cell(CONTENT_W - 30, 4.5, contact)
        self.cell(30, 4.5, f"第 {self.page_no()} / {{nb}} 頁", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if clinic.footer_note:
            self.set_text_color(*MUTED)
            self.cell(CONTENT_W, 4.5, clinic.footer_note)

    def remaining(self) -> float:
        return self.h - self.b_margin - self.get_y()

    def ensure_space(self, height: float) -> None:
        if self.remaining() < height:
            self.add_page()

    def usable_height(self) -> float:
        """續頁上內容可用的高度（扣掉續頁頁首）。一組內容比這個高，就不可能放進同一頁，硬要求只會多出空白頁。"""
        return self.h - TOP_MARGIN - CONTINUED_HEADER - BOTTOM_MARGIN

    def text_height(self, width: float, line_h: float, text: str) -> float:
        return float(self.multi_cell(width, line_h, text, dry_run=True, output=MethodReturnValue.HEIGHT,
                                     wrapmode=WrapMode.CHAR))


def _draw_title(pdf: _Sheet, job: ReferralJob) -> None:
    clinic_name = job.clinic.name or "（請到設定填寫診所名稱）"
    pdf.set_text_color(*BRAND)
    pdf.set_font(FONT, "B", 19)
    pdf.cell(CONTENT_W * 0.62, 10, clinic_name)
    pdf.set_font(FONT, "", 11)
    pdf.set_text_color(*SUBTLE)
    pdf.cell(CONTENT_W * 0.38, 10, roc_date(job.assessed_on), align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(*INK)
    pdf.set_font(FONT, "B", 15)
    pdf.cell(CONTENT_W, 9, "長者功能評估（ICOPE）轉介與衛教單", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    _draw_contact(pdf, job.clinic)
    pdf.set_draw_color(*BRAND)
    pdf.set_line_width(0.6)
    pdf.line(PAGE_LEFT, pdf.get_y() + 1.5, PAGE_LEFT + CONTENT_W, pdf.get_y() + 1.5)
    pdf.set_line_width(0.2)
    pdf.ln(5)


def _draw_contact(pdf: _Sheet, clinic: ClinicInfo) -> None:
    """長者最需要的「有問題找誰」：放在第 1 頁標題下方，電話用大字，不只擠在頁尾小字。"""
    if not (clinic.phone or clinic.address):
        return
    pdf.ln(0.5)
    pdf.set_font(FONT, "", 12.5)
    pdf.set_text_color(*INK)
    lead = "有問題請聯絡本院　"
    pdf.cell(pdf.get_string_width(lead), 8.5, lead)
    if clinic.phone:
        pdf.set_font(FONT, "B", 15.5)
        pdf.set_text_color(*BRAND)
        phone = f"電話 {clinic.phone}"
        pdf.cell(pdf.get_string_width(phone) + 6, 8.5, phone)
    if clinic.address:
        pdf.set_font(FONT, "", 12.5)
        pdf.set_text_color(*INK)
        address = f"地址 {clinic.address}"
        room = PAGE_LEFT + CONTENT_W - pdf.get_x()
        if pdf.get_string_width(address) > room:
            pdf.ln(8.5)
        pdf.multi_cell(0, 8.5, address, wrapmode=WrapMode.CHAR, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        pdf.ln(8.5)


def _draw_patient_panel(pdf: _Sheet, job: ReferralJob) -> None:
    fields = [(label, value) for label, value in (
        ("長者姓名", job.patient_name), ("評估人員", job.assessor),
    ) if value]
    if not fields:
        return
    pdf.set_fill_color(*PANEL)
    pdf.set_draw_color(*RULE)
    y = pdf.get_y()
    pdf.rect(PAGE_LEFT, y, CONTENT_W, 12, style="DF")
    pdf.set_xy(PAGE_LEFT + 4, y + 2.5)
    for label, value in fields:
        pdf.set_font(FONT, "", 11)
        pdf.set_text_color(*SUBTLE)
        pdf.cell(pdf.get_string_width(label) + 2.5, 7, label)
        pdf.set_font(FONT, "B", 13)
        pdf.set_text_color(*INK)
        pdf.cell(pdf.get_string_width(value) + 14, 7, value)
    pdf.set_y(y + 16)


def _draw_intro(pdf: _Sheet, job: ReferralJob) -> None:
    pdf.set_font(FONT, "", 12)
    pdf.set_text_color(*INK)
    if job.sections:
        text = ("這次長者功能評估中，下列項目建議進一步關注。請依各項目建議的服務資源聯繫安排；"
                "若有任何疑問，歡迎詢問本院醫護人員。")
    else:
        text = "這次為您準備了以下衛教資料，請帶回參考；若有任何疑問，歡迎詢問本院醫護人員。"
    pdf.multi_cell(CONTENT_W, 6.8, text, wrapmode=WrapMode.CHAR, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)


def _resource_block_height(pdf: _Sheet, resource: Resource) -> float:
    inner_w = CONTENT_W - 8
    pdf.set_font(FONT, "B", 14)
    height = pdf.text_height(inner_w, 7.6, resource.name + (f"　{resource.type}" if resource.type else ""))
    pdf.set_font(FONT, "", DETAIL_PT)
    for line in resource_detail_lines(resource):
        height += pdf.text_height(inner_w, DETAIL_LINE, line)
    if resource.note:
        height += pdf.text_height(inner_w, DETAIL_LINE, "備註 " + resource.note)
    return height + 4


def _draw_resource(pdf: _Sheet, resource: Resource, color: tuple[int, int, int]) -> None:
    inner_x = PAGE_LEFT + 8
    inner_w = CONTENT_W - 8
    height = _resource_block_height(pdf, resource)
    # 放得進一頁才整塊移到下一頁；比一頁還高（備註沒有長度上限）就從這裡接著印，否則前一頁會留下大片空白
    pdf.ensure_space(height if height <= pdf.usable_height() else 7.6 + DETAIL_LINE)
    top = pdf.get_y()
    pdf.set_fill_color(*color)
    pdf.rect(PAGE_LEFT + 3, top + 2.4, 2.4, 2.4, style="F")

    pdf.set_x(inner_x)
    pdf.set_font(FONT, "B", 14)
    pdf.set_text_color(*INK)
    name_w = pdf.get_string_width(resource.name)
    type_text = resource.type
    pdf.set_font(FONT, "", 11.5)
    type_w = pdf.get_string_width(type_text) + 6 if type_text else 0
    if type_text and name_w + type_w <= inner_w:
        pdf.set_font(FONT, "B", 14)
        pdf.cell(name_w + 3, 7.6, resource.name)
        pdf.set_font(FONT, "", 11.5)
        pdf.set_text_color(*SUBTLE)
        pdf.cell(type_w, 7.6, type_text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        pdf.set_font(FONT, "B", 14)
        pdf.multi_cell(inner_w, 7.6, resource.name, wrapmode=WrapMode.CHAR, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if type_text:
            pdf.set_x(inner_x)
            pdf.set_font(FONT, "", 11.5)
            pdf.set_text_color(*SUBTLE)
            pdf.cell(inner_w, 6.4, type_text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # 長者要照著打電話、找地址：明細用 13pt 深色字
    pdf.set_font(FONT, "", DETAIL_PT)
    pdf.set_text_color(*INK)
    for line in resource_detail_lines(resource):
        pdf.set_x(inner_x)
        pdf.multi_cell(inner_w, DETAIL_LINE, line, wrapmode=WrapMode.CHAR, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if resource.note:
        pdf.set_x(inner_x)
        pdf.multi_cell(inner_w, DETAIL_LINE, "備註 " + resource.note, wrapmode=WrapMode.CHAR,
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)


def _note_height(pdf: _Sheet, note: str) -> float:
    pdf.set_font(FONT, "", NOTE_PT)          # 和實際繪製用同一個字型、寬度、行高與換行方式
    return pdf.text_height(CONTENT_W - 2 * NOTE_INDENT, NOTE_LINE, note) + 3


def _draw_section(pdf: _Sheet, section: ReferralSection, note: str, printed: dict[str, tuple[str, int]]) -> None:
    domain = DOMAIN_BY_ID[section.domain_id]
    color = _hex_rgb(domain.color)
    if not section.resources:
        first_height = 12.0
    elif section.resources[0].id in printed:
        first_height = _reference_height(pdf, section.resources[0], *printed[section.resources[0].id])
    else:
        first_height = _resource_block_height(pdf, section.resources[0])
    together = 14 + (_note_height(pdf, note) if note else 0) + first_height
    # 標題、衛教重點與第一筆資源放得進一頁就排在一起。放不進去（資源備註沒有長度上限）就正常續頁，
    # 只保證標題和下一行同頁：硬要求整組同頁只會多出空白頁。
    pdf.ensure_space(together if together <= pdf.usable_height() else 14 + NOTE_LINE)

    y = pdf.get_y()
    pdf.set_fill_color(*_tint(color, 0.9))
    pdf.rect(PAGE_LEFT, y, CONTENT_W, 10.5, style="F")
    pdf.set_fill_color(*color)
    pdf.rect(PAGE_LEFT, y, 38, 10.5, style="F")
    pdf.set_xy(PAGE_LEFT, y)
    pdf.set_font(FONT, "B", 13.5)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(38, 10.5, domain.title if len(domain.title) <= 4 else domain.name, align="C")
    pdf.set_font(FONT, "", 11.5)
    pdf.set_text_color(*INK)
    pdf.set_x(PAGE_LEFT + 42)
    pdf.cell(CONTENT_W - 44, 10.5, domain.description, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)

    if note:
        pdf.set_font(FONT, "", NOTE_PT)      # 量高度時字型被換過：畫之前重新設定
        pdf.set_text_color(*INK)
        pdf.set_x(PAGE_LEFT + NOTE_INDENT)
        pdf.multi_cell(CONTENT_W - 2 * NOTE_INDENT, NOTE_LINE, note, wrapmode=WrapMode.CHAR,
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(3)

    if not section.resources:
        pdf.set_x(PAGE_LEFT + 8)
        pdf.set_font(FONT, "", 11.5)
        pdf.set_text_color(*SUBTLE)
        pdf.cell(CONTENT_W - 8, 7, "請與本院醫護人員討論後續安排。", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(3)
        return
    for resource in section.resources:
        first = printed.get(resource.id)
        if first:
            _draw_reference(pdf, resource, color, *first)
        else:
            _draw_resource(pdf, resource, color)
            printed[resource.id] = (domain.title, pdf.page_no())
    pdf.ln(1.5)


def _reference_detail(pdf: _Sheet, resource: Resource, first_domain: str, first_page: int) -> str:
    phones = "電話 " + "、".join(resource.phones) if resource.phones else ""
    where = "上方" if first_page == pdf.page_no() else f"第 {first_page} 頁"
    return "　".join(p for p in (phones, f"（地址與服務時間見{where}「{first_domain}」）") if p)


def _reference_height(pdf: _Sheet, resource: Resource, first_domain: str, first_page: int) -> float:
    inner_w = CONTENT_W - 8
    pdf.set_font(FONT, "B", 14)
    height = pdf.text_height(inner_w, 7.6, resource.name)
    pdf.set_font(FONT, "", DETAIL_PT)
    return height + pdf.text_height(inner_w, DETAIL_LINE, _reference_detail(pdf, resource, first_domain, first_page)) + 3


def _draw_reference(pdf: _Sheet, resource: Resource, color: tuple[int, int, int], first_domain: str,
                    first_page: int) -> None:
    """同一個資源已在前面的項目完整列出：這裡印名稱與電話，其餘資訊指到第一次出現的位置。"""
    inner_x = PAGE_LEFT + 8
    inner_w = CONTENT_W - 8
    pdf.ensure_space(_reference_height(pdf, resource, first_domain, first_page))
    detail = _reference_detail(pdf, resource, first_domain, first_page)   # 換頁後「上方」要改成頁碼：換頁判斷後再算一次
    top = pdf.get_y()
    pdf.set_fill_color(*color)
    pdf.rect(PAGE_LEFT + 3, top + 2.4, 2.4, 2.4, style="F")
    pdf.set_x(inner_x)
    pdf.set_font(FONT, "B", 14)
    pdf.set_text_color(*INK)
    pdf.multi_cell(inner_w, 7.6, resource.name, wrapmode=WrapMode.CHAR, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_x(inner_x)
    pdf.set_font(FONT, "", DETAIL_PT)
    pdf.set_text_color(*INK)
    pdf.multi_cell(inner_w, DETAIL_LINE, detail, wrapmode=WrapMode.CHAR, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)


def _draw_heading(pdf: _Sheet, text: str) -> None:
    pdf.set_font(FONT, "B", 14)
    pdf.set_text_color(*INK)
    pdf.cell(CONTENT_W, 9.5, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _draw_materials(pdf: _Sheet, materials: list[Material]) -> None:
    if not materials:
        return
    pdf.ensure_space(10 + 8 * min(len(materials), 3))
    pdf.ln(1)
    _draw_heading(pdf, f"隨附衛教資料（共 {len(materials)} 份，附在本單後面）")
    for index, material in enumerate(materials, start=1):
        pdf.ensure_space(8)
        pdf.set_x(PAGE_LEFT + 3)
        pdf.set_font(FONT, "", 12.5)
        pdf.set_text_color(*INK)
        line = f"{index}. {material.name}"
        pdf.cell(pdf.get_string_width(line) + 4, 8, line)
        pdf.set_font(FONT, "", 10.5)
        pdf.set_text_color(*MUTED)
        pdf.cell(20, 8, f"{material.pages} 頁", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)


def _draw_notes(pdf: _Sheet, notes: str) -> None:
    notes = notes.strip()
    if not notes:
        return
    pdf.set_font(FONT, "", 12)
    height = pdf.text_height(CONTENT_W - 8, 6.8, notes) + 7
    pdf.ensure_space(12 + height)
    pdf.ln(1)
    _draw_heading(pdf, "給您的叮嚀")
    y = pdf.get_y()
    pdf.set_fill_color(*PANEL)
    pdf.set_draw_color(*RULE)
    pdf.rect(PAGE_LEFT, y, CONTENT_W, height, style="DF")
    pdf.set_xy(PAGE_LEFT + 4, y + 3.5)
    pdf.set_font(FONT, "", 12)
    pdf.set_text_color(*INK)
    pdf.multi_cell(CONTENT_W - 8, 6.8, notes, wrapmode=WrapMode.CHAR, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_y(y + height + 2)


def render_referral_sheet(job: ReferralJob) -> bytes:
    notes = {s.domain_id: normalize_note(job.domain_notes.get(s.domain_id, "")) for s in job.sections}
    for domain_id, note in notes.items():        # 只檢查這份單子會印到的項目；沒勾的項目超限不影響這一份
        problem = note_problem(note)
        if problem:
            raise PdfBuildError(f"「{DOMAIN_BY_ID[domain_id].title}」的衛教重點{problem}，"
                                "請到「設定 › 診所與轉介單」修正後再列印")
    pdf = _Sheet(job)
    pdf.add_page()
    _draw_title(pdf, job)
    _draw_patient_panel(pdf, job)
    _draw_intro(pdf, job)
    printed: dict[str, tuple[str, int]] = {}
    for section in job.sections:
        _draw_section(pdf, section, notes[section.domain_id], printed)
    _draw_materials(pdf, job.materials)
    _draw_notes(pdf, job.notes)
    return bytes(pdf.output())


@dataclass
class BuildResult:
    path: Path
    total_pages: int
    sheet_pages: int


def build_referral_pdf(job: ReferralJob, material_files: dict[str, Path], output: Path) -> BuildResult:
    """轉介單 + 衛教單張合併成一個 PDF。material_files: material.id → 檔案路徑。"""
    from pypdf import PdfReader, PdfWriter
    from pypdf.errors import PdfReadError

    for material in job.materials:
        path = material_files.get(material.id)
        if path is None or not Path(path).exists():
            raise PdfBuildError(f"找不到衛教單張「{material.name}」的檔案，可能已被刪除，請回上一步取消勾選")

    sheet = render_referral_sheet(job)
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(sheet)))
    sheet_pages = len(writer.pages)
    for material in job.materials:
        try:
            writer.append(PdfReader(str(material_files[material.id])))
        except (PdfReadError, OSError, ValueError) as exc:
            raise PdfBuildError(f"衛教單張「{material.name}」的 PDF 無法讀取，請到設定重新加入這份檔案") from exc
    writer.add_metadata({"/Title": "長者功能評估轉介與衛教單", "/Creator": APP_DISPLAY_NAME})

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(output, "wb") as fh:
            writer.write(fh)
    except OSError as exc:
        raise PdfBuildError(f"無法寫入 PDF：{exc.strerror or exc}") from exc
    return BuildResult(output, len(writer.pages), sheet_pages)


# ---------------------------------------------------------------------------- 只印衛教單張
class MaterialChanged(PdfBuildError):
    """單張的檔案內容和這次工作的基準不一樣了（被換成新版，或在程式外被覆寫）。"""


@dataclass(frozen=True)
class LeafletSource:
    id: str
    name: str
    path: Path
    sha256: str = ""            # 登錄在清單裡的雜湊；空字串表示沒有登錄，改以這次讀到的內容為基準


@dataclass(frozen=True)
class MergedLeaflets:
    path: Path
    pages: int
    hashes: tuple[str, ...]     # 實際合併進去的內容雜湊（與來源同順序）：之後重試、外部開啟前用它確認來源沒變


def _read_leaflet(source: LeafletSource) -> bytes:
    try:
        return Path(source.path).read_bytes()
    except OSError as exc:
        raise PdfBuildError(f"找不到衛教單張「{source.name}」的檔案，可能已被刪除") from exc


def verify_material_sources(sources, hashes) -> None:
    """重新讀取來源並比對內容雜湊（背景執行）。檔案長度與修改時間相同不代表內容相同，所以一律比內容。"""
    import hashlib

    for source, expected in zip(sources, hashes, strict=True):
        if hashlib.sha256(_read_leaflet(source)).hexdigest() != expected:
            raise MaterialChanged(f"衛教單張「{source.name}」的檔案剛被更新")


def merge_material_pdfs(sources, output: Path) -> MergedLeaflets:
    """只合併勾選的單張：沒有轉介單首頁，也不含姓名。驗證過雜湊的那份 bytes 就是合併進去的內容。"""
    import hashlib

    from pypdf import PdfReader, PdfWriter
    from pypdf.errors import PdfReadError

    sources = list(sources)
    if not sources:
        raise PdfBuildError("沒有勾選任何衛教單張")
    output = Path(output)
    try:
        writer = PdfWriter()
        hashes = []
        for source in sources:
            data = _read_leaflet(source)
            digest = hashlib.sha256(data).hexdigest()
            if source.sha256 and digest != source.sha256:
                raise MaterialChanged(f"衛教單張「{source.name}」的檔案剛被更新")
            try:
                writer.append(PdfReader(io.BytesIO(data)))
            except (PdfReadError, OSError, ValueError) as exc:
                raise PdfBuildError(f"衛教單張「{source.name}」的 PDF 無法讀取，請到設定重新加入這份檔案") from exc
            hashes.append(digest)
        pages = len(writer.pages)
        if pages == 0:
            raise PdfBuildError("勾選的衛教單張沒有任何頁面")
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(output, "wb") as fh:
                writer.write(fh)
        except OSError as exc:
            raise PdfBuildError(f"無法寫入 PDF：{exc.strerror or exc}") from exc
        verify_material_sources(sources, hashes)       # 合併期間來源又被換掉：這份不能用
    except BaseException:
        output.unlink(missing_ok=True)                 # 失敗不留半成品
        raise
    return MergedLeaflets(output, pages, tuple(hashes))
