"""data 資料夾的讀寫。

多台電腦可指到同一個共用資料夾，因此：
- 所有變更都是「單筆操作」：先重讀檔案、套用這一筆、原子寫回（tmp + os.replace）。
  UI 永遠不整批覆寫清單，避免 A 機編輯時蓋掉 B 機剛新增的資料。
- Windows 上另一程序短暫開檔讀取時 os.replace 會 PermissionError，重試幾次。
- 不做跨機鎖：寫入只發生在管理操作，衝突機率極低，最後寫入者為準。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from icope_tool.models import (
    LocalConfig, Material, MaterialFile, Resource, ResourceFile, Settings, new_id,
)

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R")

_RETRIES = 6


class StoreError(Exception):
    """資料讀寫失敗（訊息可直接顯示給使用者）。"""


class DataFolderUnavailable(StoreError):
    """資料夾不存在或無法寫入（例如 NAS 離線）。"""


class StoreIOError(StoreError):
    """讀寫檔案時的暫時性錯誤（網路磁碟斷線、檔案被占用），稍後重試可能成功。"""


def _retry(fn: Callable[[], R]) -> R:
    for attempt in range(_RETRIES):
        try:
            return fn()
        except PermissionError:
            if attempt == _RETRIES - 1:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _retry(lambda: os.replace(tmp_name, path))
    finally:
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_pdf(path: Path) -> int:
    """回傳頁數；不是 PDF、壞檔或有密碼保護則丟 StoreError。"""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise StoreError("這個 PDF 有密碼保護，無法合併列印")
            except StoreError:
                raise
            except Exception as exc:
                raise StoreError("這個 PDF 有密碼保護，無法合併列印") from exc
        pages = len(reader.pages)
    except StoreError:
        raise
    except (PdfReadError, OSError, ValueError) as exc:
        raise StoreError(f"無法讀取 PDF：{path.name}（檔案可能已損毀或不是 PDF）") from exc
    if pages == 0:
        raise StoreError(f"PDF 沒有任何頁面：{path.name}")
    return pages


@dataclass
class ImportResult:
    added: int = 0
    skipped: int = 0


def _read_model(path: Path, model: type[T]) -> T:
    if not path.exists():
        if not path.parent.exists():
            # 整個資料夾不見（網路磁碟斷線、USB 拔掉）：不能當成「沒有資料」，否則畫面會把已選的內容清掉
            raise DataFolderUnavailable(f"找不到資料夾：{path.parent}")
        return model()
    try:
        raw = _retry(lambda: path.read_bytes())
    except OSError as exc:
        raise StoreIOError(f"無法讀取 {path.name}：{exc.strerror or exc}") from exc
    try:
        return model.model_validate(json.loads(raw.decode("utf-8")))
    except (ValueError, ValidationError) as exc:
        raise StoreError(
            f"{path.name} 內容格式錯誤，無法讀取。請從備份還原，或聯絡管理者檢查這個檔案。"
        ) from exc


def _write_model(path: Path, obj: BaseModel) -> None:
    payload = json.dumps(obj.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    try:
        atomic_write_bytes(path, payload.encode("utf-8"))
    except OSError as exc:
        raise StoreIOError(f"無法寫入 {path.name}：{exc.strerror or exc}") from exc


class LocalConfigStore:
    """本機 config.json（不共用）。"""

    def __init__(self, local_dir: Path):
        self.local_dir = Path(local_dir)
        self.path = self.local_dir / "config.json"

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> LocalConfig:
        try:
            return _read_model(self.path, LocalConfig)
        except StoreError:
            return LocalConfig()

    def save(self, config: LocalConfig) -> None:
        _write_model(self.path, config)

    def update(self, **changes) -> LocalConfig:
        config = self.load().model_copy(update=changes)
        self.save(config)
        return config


class DataStore:
    SETTINGS = "settings.json"
    RESOURCES = "resources.json"
    MATERIALS = "materials.json"

    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ 基本
    @property
    def auth_dir(self) -> Path:
        return self.root / "auth"

    @property
    def materials_dir(self) -> Path:
        return self.root / "materials"

    @staticmethod
    def looks_initialized(root: Path) -> bool:
        return (Path(root) / DataStore.SETTINGS).exists()

    def check_available(self) -> None:
        if not self.root.exists():
            raise DataFolderUnavailable(f"找不到資料夾：{self.root}")
        probe = self.root / f".write-test-{new_id()}"
        try:
            probe.write_bytes(b"ok")
            probe.unlink()
        except OSError as exc:
            raise DataFolderUnavailable(f"資料夾無法寫入：{self.root}（{exc.strerror or exc}）") from exc

    def ensure_ready(self, create: bool = False) -> None:
        """確認資料夾可用、建立子資料夾、首次使用時寫入預設資料。"""
        with self._lock:
            if create:
                try:
                    self.root.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    raise DataFolderUnavailable(
                        f"無法建立資料夾：{self.root}（{exc.strerror or exc}）"
                    ) from exc
            self.check_available()
            self.materials_dir.mkdir(exist_ok=True)
            self.auth_dir.mkdir(exist_ok=True)
            if not (self.root / self.SETTINGS).exists():
                _write_model(self.root / self.SETTINGS, Settings())
            if not (self.root / self.RESOURCES).exists():
                _write_model(self.root / self.RESOURCES, ResourceFile(resources=[
                    Resource(
                        name="本院門診追蹤", type="門診追蹤", pinned=True,
                        domains=["cognitive", "mobility", "nutrition", "vision",
                                 "hearing", "depression", "medication", "social"],
                        note="請於門診時間回診，由醫師追蹤評估結果",
                    )
                ]))
            if not (self.root / self.MATERIALS).exists():
                _write_model(self.root / self.MATERIALS, MaterialFile())

    def signature(self) -> tuple[float, ...]:
        """三個 JSON 的修改時間；用來判斷其他電腦是否改過資料。"""
        stamps = []
        for name in (self.SETTINGS, self.RESOURCES, self.MATERIALS):
            try:
                stamps.append((self.root / name).stat().st_mtime)
            except OSError:
                stamps.append(0.0)
        return tuple(stamps)

    # ------------------------------------------------------------------ 設定
    def load_settings(self) -> Settings:
        return _read_model(self.root / self.SETTINGS, Settings)

    def update_settings(self, mutate: Callable[[Settings], None]) -> Settings:
        with self._lock:
            settings = self.load_settings()
            mutate(settings)
            settings = Settings.model_validate(settings.model_dump())
            _write_model(self.root / self.SETTINGS, settings)
            return settings

    # ------------------------------------------------------------------ 轉介資源
    def _load_resources(self) -> ResourceFile:
        return _read_model(self.root / self.RESOURCES, ResourceFile)

    def list_resources(self) -> list[Resource]:
        return self._load_resources().resources

    def get_resource(self, resource_id: str) -> Resource | None:
        return next((r for r in self.list_resources() if r.id == resource_id), None)

    def add_resource(self, resource: Resource) -> Resource:
        with self._lock:
            doc = self._load_resources()
            if any(r.id == resource.id for r in doc.resources):
                resource = resource.model_copy(update={"id": new_id()})
            doc.resources.append(resource)
            _write_model(self.root / self.RESOURCES, doc)
            return resource

    def update_resource(self, resource: Resource) -> Resource:
        with self._lock:
            doc = self._load_resources()
            for index, existing in enumerate(doc.resources):
                if existing.id == resource.id:
                    doc.resources[index] = resource
                    _write_model(self.root / self.RESOURCES, doc)
                    return resource
            raise StoreError("這筆資源已被刪除（可能是其他電腦刪的），請重新整理後再試")

    def patch_resources(self, ids: Iterable[str], **changes) -> int:
        wanted = set(ids)
        with self._lock:
            doc = self._load_resources()
            count = 0
            for index, existing in enumerate(doc.resources):
                if existing.id in wanted:
                    doc.resources[index] = Resource.model_validate(
                        {**existing.model_dump(), **changes}
                    )
                    count += 1
            if count:
                _write_model(self.root / self.RESOURCES, doc)
            return count

    def delete_resources(self, ids: Iterable[str]) -> int:
        wanted = set(ids)
        with self._lock:
            doc = self._load_resources()
            before = len(doc.resources)
            doc.resources = [r for r in doc.resources if r.id not in wanted]
            removed = before - len(doc.resources)
            if removed:
                _write_model(self.root / self.RESOURCES, doc)
            return removed

    def import_resources(self, items: Iterable[Resource]) -> ImportResult:
        """加入資源；名稱＋地址相同者視為重複略過。"""
        with self._lock:
            doc = self._load_resources()
            keys = {r.dedupe_key() for r in doc.resources}
            ids = {r.id for r in doc.resources}
            result = ImportResult()
            for item in items:
                key = item.dedupe_key()
                if key in keys:
                    result.skipped += 1
                    continue
                if item.id in ids:
                    item = item.model_copy(update={"id": new_id()})
                doc.resources.append(item)
                keys.add(key)
                ids.add(item.id)
                result.added += 1
            if result.added:
                _write_model(self.root / self.RESOURCES, doc)
            return result

    def count_duplicate_resources(self, items: Iterable[Resource]) -> int:
        keys = {r.dedupe_key() for r in self.list_resources()}
        return sum(1 for item in items if item.dedupe_key() in keys)

    def replace_resources(self, items: Iterable[Resource]) -> None:
        with self._lock:
            _write_model(self.root / self.RESOURCES, ResourceFile(resources=list(items)))

    def resource_types(self) -> list[str]:
        seen: dict[str, None] = {}
        for resource in self.list_resources():
            if resource.type:
                seen.setdefault(resource.type, None)
        return sorted(seen)

    # ------------------------------------------------------------------ 衛教單張
    def _load_materials(self) -> MaterialFile:
        return _read_model(self.root / self.MATERIALS, MaterialFile)

    def list_materials(self) -> list[Material]:
        return sorted(self._load_materials().materials, key=lambda m: (m.sort_order, m.name))

    def get_material(self, material_id: str) -> Material | None:
        return next((m for m in self.list_materials() if m.id == material_id), None)

    def material_file(self, material: Material) -> Path:
        return self.materials_dir / material.filename

    def find_material_by_sha(self, sha256: str) -> Material | None:
        return next((m for m in self.list_materials() if m.sha256 == sha256), None)

    def add_material_file(self, source: Path, name: str | None = None, description: str = "",
                          domains: Iterable[str] = (), include_by_default: bool = False) -> Material:
        """複製 PDF 進 materials/ 並登錄。同內容（sha256）已存在則丟 StoreError。"""
        source = Path(source)
        pages = inspect_pdf(source)
        digest = sha256_file(source)
        with self._lock:
            duplicate = self.find_material_by_sha(digest)
            if duplicate:
                raise StoreError(f"「{source.name}」已經加入過（單張名稱：{duplicate.name}）")
            material_id = new_id()
            filename = f"{material_id}.pdf"
            target = self.materials_dir / filename
            self.materials_dir.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copyfile(source, target)
            except OSError as exc:
                raise StoreIOError(f"無法複製檔案：{exc.strerror or exc}") from exc
            doc = self._load_materials()
            next_order = max((m.sort_order for m in doc.materials), default=-1) + 1
            material = Material(
                id=material_id, name=name or source.stem, description=description,
                filename=filename, domains=list(domains), sort_order=next_order,
                include_by_default=include_by_default,
                pages=pages, size=source.stat().st_size, sha256=digest,
            )
            doc.materials.append(material)
            try:
                _write_model(self.root / self.MATERIALS, doc)
            except StoreError:
                target.unlink(missing_ok=True)
                raise
            return material

    def replace_material_file(self, material_id: str, source: Path) -> Material:
        """重新指定單張的 PDF（原檔遺失或換新版）；名稱、說明、適用項目與順序都保留。"""
        source = Path(source)
        pages = inspect_pdf(source)
        digest = sha256_file(source)
        with self._lock:
            doc = self._load_materials()
            index = next((i for i, m in enumerate(doc.materials) if m.id == material_id), None)
            if index is None:
                raise StoreError("這份單張已被刪除（可能是其他電腦刪的），請重新整理後再試")
            duplicate = next((m for m in doc.materials if m.sha256 == digest and m.id != material_id), None)
            if duplicate:
                raise StoreError(f"「{source.name}」已經是另一份單張（{duplicate.name}）")
            existing = doc.materials[index]
            new_name = f"{material_id}-{digest[:12]}.pdf"
            if new_name == existing.filename:
                new_name = f"{material_id}-{digest[:12]}-{new_id()[:6]}.pdf"
            target = self.materials_dir / new_name
            try:
                atomic_write_bytes(target, source.read_bytes())
            except OSError as exc:
                raise StoreIOError(f"無法複製檔案：{exc.strerror or exc}") from exc
            updated = existing.model_copy(update={"filename": new_name, "pages": pages,
                                                  "size": source.stat().st_size, "sha256": digest})
            doc.materials[index] = updated
            try:
                _write_model(self.root / self.MATERIALS, doc)
            except StoreError:
                target.unlink(missing_ok=True)      # 清單沒有寫成：原本的檔案與紀錄都保持不變
                raise
            try:
                (self.materials_dir / existing.filename).unlink(missing_ok=True)
            except OSError:
                pass                                 # 舊檔被占用時留著，不影響新紀錄
            return updated

    def update_material(self, material: Material) -> Material:
        with self._lock:
            doc = self._load_materials()
            for index, existing in enumerate(doc.materials):
                if existing.id == material.id:
                    # 檔案相關欄位不允許從 UI 改
                    material = material.model_copy(update={
                        "filename": existing.filename, "pages": existing.pages,
                        "size": existing.size, "sha256": existing.sha256,
                    })
                    doc.materials[index] = material
                    _write_model(self.root / self.MATERIALS, doc)
                    return material
            raise StoreError("這份單張已被刪除（可能是其他電腦刪的），請重新整理後再試")

    def patch_materials(self, ids: Iterable[str], **changes) -> int:
        wanted = set(ids)
        with self._lock:
            doc = self._load_materials()
            count = 0
            for index, existing in enumerate(doc.materials):
                if existing.id in wanted:
                    doc.materials[index] = Material.model_validate({**existing.model_dump(), **changes})
                    count += 1
            if count:
                _write_model(self.root / self.MATERIALS, doc)
            return count

    def delete_materials(self, ids: Iterable[str]) -> int:
        wanted = set(ids)
        with self._lock:
            doc = self._load_materials()
            removed = [m for m in doc.materials if m.id in wanted]
            if not removed:
                return 0
            doc.materials = [m for m in doc.materials if m.id not in wanted]
            _write_model(self.root / self.MATERIALS, doc)
            for material in removed:
                try:
                    (self.materials_dir / material.filename).unlink(missing_ok=True)
                except OSError:
                    pass   # 檔案被占用時留著，不影響清單
            return len(removed)

    def move_material(self, material_id: str, delta: int) -> bool:
        """上移（delta=-1）或下移（delta=+1）。回傳是否有移動。"""
        with self._lock:
            doc = self._load_materials()
            ordered = sorted(doc.materials, key=lambda m: (m.sort_order, m.name))
            index = next((i for i, m in enumerate(ordered) if m.id == material_id), None)
            if index is None:
                return False
            target = index + delta
            if not 0 <= target < len(ordered):
                return False
            ordered[index], ordered[target] = ordered[target], ordered[index]
            doc.materials = [m.model_copy(update={"sort_order": i}) for i, m in enumerate(ordered)]
            _write_model(self.root / self.MATERIALS, doc)
            return True

    def replace_materials(self, materials: list[Material], files: dict[str, bytes]) -> None:
        """整批取代（設定包「取代」模式）。files: material.id → PDF bytes。"""
        with self._lock:
            old = self._load_materials().materials
            new_docs = []
            for order, material in enumerate(materials):
                filename = f"{material.id}.pdf"
                atomic_write_bytes(self.materials_dir / filename, files[material.id])
                new_docs.append(material.model_copy(update={"filename": filename, "sort_order": order}))
            _write_model(self.root / self.MATERIALS, MaterialFile(materials=new_docs))
            keep = {m.filename for m in new_docs}
            for material in old:
                if material.filename not in keep:
                    try:
                        (self.materials_dir / material.filename).unlink(missing_ok=True)
                    except OSError:
                        pass
