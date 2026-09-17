"""設定包：把轉介資源庫與衛教單張打包成 zip，讓其他診所匯入共用。

內容：pack.json（資源、單張清單）＋ materials/<id>.pdf。
絕不包含：國健署帳密、登入 cookie、診所資訊（名稱／電話／地址屬各院自己的設定）、院所自己寫的各項衛教重點
（Settings.sheet：存在共用資料夾、同院電腦共用；合併與取代兩種匯入都不會動到它）。
資源與單張的「院所預設」旗標會跟著設定包走：合併時新增的紀錄帶著旗標、重複的保留本院設定；取代以設定包為準。
也接受單獨的 .json（只含資源），方便匯入 examples/ 內的範例資料。
"""
from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from icope_tool import __version__
from icope_tool.models import Material, PackMode, Resource, new_id
from icope_tool.store import DataStore, StoreError, atomic_write_bytes, inspect_pdf

PACK_FORMAT = "icope-tool-pack"
PACK_VERSION = 1
MAX_MATERIAL_BYTES = 60 * 1024 * 1024
# 設定包會在診所之間流傳，內容整份讀進記憶體：先看 zip 宣告的解壓後大小，超過就不讀（壓縮炸彈）
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_PACK_BYTES = 400 * 1024 * 1024
_MATERIAL_NAME = re.compile(r"^materials/[0-9a-f]{12}\.pdf$")


class PackError(Exception):
    """設定包無法讀取或套用（訊息可直接顯示）。"""


class PackManifest(BaseModel):
    format: str = PACK_FORMAT
    version: int = PACK_VERSION
    exported_at: str = ""
    app_version: str = ""
    resources: list[Resource] = Field(default_factory=list)
    materials: list[Material] = Field(default_factory=list)


@dataclass
class LoadedPack:
    path: Path
    manifest: PackManifest
    material_bytes: dict[str, bytes] = field(default_factory=dict)

    @property
    def resource_count(self) -> int:
        return len(self.manifest.resources)

    @property
    def material_count(self) -> int:
        return len(self.manifest.materials)


@dataclass
class ApplyResult:
    resources_added: int = 0
    resources_skipped: int = 0
    materials_added: int = 0
    materials_skipped: int = 0


def export_pack(store: DataStore, dest: Path, include_resources: bool = True,
                include_materials: bool = True) -> tuple[int, int]:
    resources = store.list_resources() if include_resources else []
    materials = store.list_materials() if include_materials else []
    manifest = PackManifest(
        exported_at=datetime.now().isoformat(timespec="seconds"), app_version=__version__,
        resources=resources,
        materials=[m.model_copy(update={"filename": f"{m.id}.pdf"}) for m in materials],
    )
    dest = Path(dest)
    tmp = dest.with_name(dest.name + ".part")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("pack.json", manifest.model_dump_json(indent=2))
            for material in materials:
                source = store.material_file(material)
                if not source.exists():
                    raise PackError(f"找不到衛教單張「{material.name}」的檔案，請先到「設定 › 衛教單張」刪除或重新加入")
                archive.write(source, f"materials/{material.id}.pdf")
        tmp.replace(dest)
    except OSError as exc:
        raise PackError(f"無法寫入設定包：{exc.strerror or exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)
    return len(resources), len(materials)


def read_pack(path: Path) -> LoadedPack:
    path = Path(path)
    if path.suffix.lower() == ".json":
        return _read_json_pack(path)
    try:
        with zipfile.ZipFile(path) as archive:
            try:
                manifest_info = archive.getinfo("pack.json")
            except KeyError as exc:
                raise PackError("這個 zip 不是本工具匯出的設定包（缺少 pack.json）") from exc
            if manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise PackError("設定包的清單過大，無法匯入")
            manifest = _parse_manifest(archive.read(manifest_info))
            files: dict[str, bytes] = {}
            total = 0
            for material in manifest.materials:
                name = f"materials/{material.id}.pdf"
                if not _MATERIAL_NAME.match(name):
                    raise PackError("設定包內容不正確（單張編號格式錯誤）")
                try:
                    info = archive.getinfo(name)
                except KeyError as exc:
                    raise PackError(f"設定包缺少單張「{material.name}」的 PDF") from exc
                if info.file_size > MAX_MATERIAL_BYTES:
                    raise PackError(f"單張「{material.name}」檔案過大，無法匯入")
                total += info.file_size
                if total > MAX_PACK_BYTES:
                    raise PackError("設定包裡的衛教單張合計過大，無法匯入")
                files[material.id] = archive.read(info)
            return LoadedPack(path, manifest, files)
    except zipfile.BadZipFile as exc:
        raise PackError("無法開啟設定包：檔案不是 zip 或已損毀") from exc
    except OSError as exc:
        raise PackError(f"無法讀取設定包：{exc.strerror or exc}") from exc


def _read_json_pack(path: Path) -> LoadedPack:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PackError(f"無法讀取檔案：{exc.strerror or exc}") from exc
    manifest = _parse_manifest(raw)
    if manifest.materials:
        raise PackError("單獨的 .json 不能包含衛教單張，請改用 zip 設定包")
    return LoadedPack(path, manifest)


def _parse_manifest(raw: bytes) -> PackManifest:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PackError("設定包內容格式錯誤，無法讀取") from exc
    if not isinstance(data, dict) or data.get("format") != PACK_FORMAT:
        raise PackError("這個檔案不是本工具的設定包")
    if int(data.get("version", 0)) > PACK_VERSION:
        raise PackError("這個設定包來自較新版的程式，請先更新本程式再匯入")
    try:
        return PackManifest.model_validate(data)
    except ValidationError as exc:
        raise PackError("設定包內容有欄位格式錯誤，無法匯入") from exc


def apply_pack(store: DataStore, pack: LoadedPack, mode: PackMode) -> ApplyResult:
    result = ApplyResult()
    resources = [r.model_copy(update={"id": new_id()}) for r in pack.manifest.resources]

    if mode == "replace":
        store.replace_resources(resources)
        result.resources_added = len(resources)
        if pack.manifest.materials:
            materials = [m.model_copy(update={"id": new_id()}) for m in pack.manifest.materials]
            files = {new.id: pack.material_bytes[old.id]
                     for old, new in zip(pack.manifest.materials, materials)}
            store.replace_materials(materials, files)
            result.materials_added = len(materials)
        return result

    imported = store.import_resources(resources)
    result.resources_added, result.resources_skipped = imported.added, imported.skipped
    for material in pack.manifest.materials:
        data = pack.material_bytes[material.id]
        tmp = store.materials_dir / f".import-{new_id()}.pdf"
        try:
            atomic_write_bytes(tmp, data)
            try:
                store.add_material_file(tmp, name=material.name, description=material.description,
                                        domains=material.domains, include_by_default=material.include_by_default)
                result.materials_added += 1
            except StoreError as exc:
                if "已經加入過" in str(exc):
                    result.materials_skipped += 1
                else:
                    raise PackError(f"單張「{material.name}」無法匯入：{exc}") from exc
        finally:
            tmp.unlink(missing_ok=True)
    return result


def validate_pack_materials(pack: LoadedPack, scratch_dir: Path) -> None:
    """匯入前先確認每份 PDF 都讀得到（避免取代模式做到一半才失敗）。"""
    scratch_dir.mkdir(parents=True, exist_ok=True)
    for material in pack.manifest.materials:
        probe = scratch_dir / f".probe-{new_id()}.pdf"
        try:
            probe.write_bytes(pack.material_bytes[material.id])
            inspect_pdf(probe)
        except StoreError as exc:
            raise PackError(f"單張「{material.name}」的 PDF 無法讀取：{exc}") from exc
        finally:
            probe.unlink(missing_ok=True)
