"""資料模型（pydantic）。存成 JSON 放在 data 資料夾；未知欄位忽略以利日後升級。"""
from __future__ import annotations

import unicodedata
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from icope_tool.domains import DOMAIN_IDS, sort_domain_ids

DomainId = Literal[
    "cognitive", "mobility", "nutrition", "vision",
    "hearing", "depression", "medication", "social",
]

SCHEMA_VERSION = 1


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def normalize_text(value: str) -> str:
    """比對用正規化：全形轉半形、去空白、小寫。"""
    return "".join(unicodedata.normalize("NFKC", value or "").split()).lower()


class ClinicInfo(BaseModel):
    name: str = ""
    phone: str = ""
    address: str = ""
    footer_note: str = ""


class HpdcsPrefs(BaseModel):
    official: bool = True        # EFA_{民國年}
    pilot: bool = True           # EFA_Pilot_{民國年}
    custom_plans: list[str] = Field(default_factory=list)   # 非空時覆寫自動推導


NOTE_MAX_CHARS = 200
NOTE_MAX_LINES = 6


def normalize_note(text: str) -> str:
    """各項衛教重點的正規化：統一換行、去掉首尾空白，不截字。載入、編輯器計數、比對、儲存與 PDF 都用這一個。"""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def note_problem(text: str) -> str:
    """沒問題回傳空字串。字數不含換行；行數是使用者自己按的換行（畫面自動折行不算）。"""
    text = normalize_note(text)
    chars = len(text.replace("\n", ""))
    lines = text.count("\n") + 1 if text else 0
    if chars > NOTE_MAX_CHARS:
        return f"超過 {NOTE_MAX_CHARS} 字（目前 {chars} 字）"
    if lines > NOTE_MAX_LINES:
        return f"超過 {NOTE_MAX_LINES} 行（目前 {lines} 行）"
    return ""


class SheetPrefs(BaseModel):
    """院所自己寫、印在轉介單上的文字。存在共用資料夾（同院電腦共用）；設定包不包含。"""
    domain_notes: dict[str, str] = Field(default_factory=dict)      # 項目 id → 衛教重點；空白不存

    @field_validator("domain_notes", mode="before")
    @classmethod
    def _tolerant_notes(cls, value):
        # 手改檔案的容錯：忽略未知項目與非文字值；超過上限的文字照樣保留（由 note_problem 回報，儲存與輸出時才擋）
        if not isinstance(value, dict):
            return {}
        notes = {d: normalize_note(value[d]) for d in DOMAIN_IDS if isinstance(value.get(d), str)}
        return {d: text for d, text in notes.items() if text}


class Settings(BaseModel):
    schema_version: int = SCHEMA_VERSION
    clinic: ClinicInfo = Field(default_factory=ClinicInfo)
    hpdcs: HpdcsPrefs = Field(default_factory=HpdcsPrefs)
    sheet: SheetPrefs = Field(default_factory=SheetPrefs)

    @field_validator("sheet", mode="before")
    @classmethod
    def _tolerant_sheet(cls, value):
        return value if isinstance(value, (dict, SheetPrefs)) else {}


class _Tagged(BaseModel):
    domains: list[DomainId] = Field(default_factory=list)

    @field_validator("domains")
    @classmethod
    def _canonical_domains(cls, value):
        return sort_domain_ids(value)


class Resource(_Tagged):
    id: str = Field(default_factory=new_id)
    name: str
    type: str = ""
    pinned: bool = False
    include_by_default: bool = False   # 院所預設：按「帶入院所預設」時加入適用項目（與 pinned 的「排在最上面」無關）
    contact: str = ""
    phones: list[str] = Field(default_factory=list)
    hours: str = ""
    address: str = ""
    website: str = ""
    note: str = ""
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _name_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("名稱不可空白")
        return value

    @field_validator("type", "contact", "hours", "address", "website", "note")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("phones")
    @classmethod
    def _clean_phones(cls, value: list[str]) -> list[str]:
        return [p.strip() for p in value if p and p.strip()]

    def dedupe_key(self) -> tuple[str, str]:
        return normalize_text(self.name), normalize_text(self.address)


class Material(_Tagged):
    id: str = Field(default_factory=new_id)
    name: str
    description: str = ""
    filename: str                 # data/materials/ 內的檔名
    sort_order: int = 0
    enabled: bool = True
    include_by_default: bool = False
    pages: int = 0
    size: int = 0
    sha256: str = ""

    @field_validator("name")
    @classmethod
    def _name_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("名稱不可空白")
        return value

    @field_validator("filename")
    @classmethod
    def _plain_pdf_name(cls, value: str) -> str:
        """這個檔名會被拿去開啟與刪除，而清單放在可能多台電腦共用的資料夾：只接受單張資料夾裡的單純 PDF 檔名。

        空字串是編輯視窗裡還沒有檔案的草稿。
        """
        if value and (any(ch in value for ch in '/\\:') or not value.lower().endswith(".pdf") or value.startswith(".")):
            raise ValueError("衛教單張的檔名不正確")
        return value


class ResourceFile(BaseModel):
    schema_version: int = SCHEMA_VERSION
    resources: list[Resource] = Field(default_factory=list)


class MaterialFile(BaseModel):
    schema_version: int = SCHEMA_VERSION
    materials: list[Material] = Field(default_factory=list)


class LocalConfig(BaseModel):
    """每台電腦自己的設定（不共用）。"""
    data_dir: str = ""
    reader_hint: str = ""
    last_assessor: str = ""
    recent_assessors: list[str] = Field(default_factory=list)
    hide_setup_checklist: bool = False


PackMode = Literal["merge", "replace"]
