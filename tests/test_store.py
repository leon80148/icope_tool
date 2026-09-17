from __future__ import annotations

import json
from pathlib import Path

import pytest
from fpdf import FPDF

from icope_tool.audit import AuditLog, pid_tag
from icope_tool.models import Resource
from icope_tool.services.hpdcs.credentials import CredentialStore, mask_account
from icope_tool.store import DataFolderUnavailable, DataStore, LocalConfigStore, StoreError


def make_pdf(path: Path, pages: int = 1, text: str = "hello") -> Path:
    pdf = FPDF()
    for i in range(pages):
        pdf.add_page()
        pdf.set_font("helvetica", size=12)
        pdf.cell(text=f"{text} {i}")
    pdf.output(str(path))
    return path


@pytest.fixture
def store(tmp_path) -> DataStore:
    s = DataStore(tmp_path / "data")
    s.ensure_ready(create=True)
    return s


def test_ensure_ready_seeds_defaults(store):
    assert store.load_settings().clinic.name == ""
    resources = store.list_resources()
    assert len(resources) == 1 and resources[0].pinned
    assert store.list_materials() == []
    assert store.materials_dir.is_dir() and store.auth_dir.is_dir()


def test_ensure_ready_does_not_overwrite_existing(store):
    store.update_settings(lambda s: setattr(s.clinic, "name", "測試診所"))
    store.ensure_ready()
    assert store.load_settings().clinic.name == "測試診所"


def test_missing_folder_is_unavailable(tmp_path):
    with pytest.raises(DataFolderUnavailable):
        DataStore(tmp_path / "nope").ensure_ready(create=False)


def test_corrupt_json_raises_readable_error(store):
    (store.root / "resources.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(StoreError) as ei:
        store.list_resources()
    assert "resources.json" in str(ei.value)


def test_resource_crud_reads_fresh_file_each_time(tmp_path):
    root = tmp_path / "shared"
    machine_a = DataStore(root)
    machine_a.ensure_ready(create=True)
    machine_b = DataStore(root)

    added_by_a = machine_a.add_resource(Resource(name="A 機新增", domains=["vision"]))
    added_by_b = machine_b.add_resource(Resource(name="B 機新增", domains=["hearing"]))
    # A 編輯自己那筆時不會把 B 剛新增的蓋掉
    machine_a.update_resource(added_by_a.model_copy(update={"phones": ["05-1234567"]}))
    names = {r.name for r in machine_b.list_resources()}
    assert {"A 機新增", "B 機新增"} <= names
    assert machine_b.get_resource(added_by_a.id).phones == ["05-1234567"]

    assert machine_b.delete_resources([added_by_b.id]) == 1
    with pytest.raises(StoreError):
        machine_a.update_resource(added_by_b)


def test_patch_and_types(store):
    r1 = store.add_resource(Resource(name="甲", type="醫療院所"))
    r2 = store.add_resource(Resource(name="乙", type="巷弄長照站"))
    assert store.patch_resources([r1.id, r2.id], enabled=False) == 2
    assert all(not r.enabled for r in store.list_resources() if r.id in {r1.id, r2.id})
    assert store.resource_types() == ["巷弄長照站", "醫療院所", "門診追蹤"]


def test_import_resources_dedupes_by_name_and_address(store):
    items = [
        Resource(name="輔具中心", address="嘉義市東區彌陀路255號"),
        Resource(name="輔具中心 ", address="嘉義市東區彌陀路２５５號"),   # 全形數字、多空白 → 重複
        Resource(name="輔具中心", address="嘉義市玉康路160號"),
    ]
    result = store.import_resources(items)
    assert (result.added, result.skipped) == (2, 1)
    assert store.count_duplicate_resources(items) == 3


def test_resource_domains_are_canonicalized():
    r = Resource(name="x", domains=["social", "cognitive", "social"])
    assert r.domains == ["cognitive", "social"]
    with pytest.raises(ValueError):
        Resource(name="   ")


def test_material_add_move_delete(store, tmp_path):
    a = store.add_material_file(make_pdf(tmp_path / "營養.pdf", pages=2), domains=["nutrition"])
    b = store.add_material_file(make_pdf(tmp_path / "運動.pdf", text="exercise"))
    assert a.name == "營養" and a.pages == 2 and a.size > 0 and len(a.sha256) == 64
    assert store.material_file(a).exists()
    assert [m.id for m in store.list_materials()] == [a.id, b.id]

    assert store.move_material(b.id, -1) is True
    assert [m.id for m in store.list_materials()] == [b.id, a.id]
    assert store.move_material(b.id, -1) is False

    with pytest.raises(StoreError):
        store.add_material_file(tmp_path / "營養.pdf")   # 同內容重複

    assert store.delete_materials([a.id]) == 1
    assert not (store.materials_dir / a.filename).exists()


def test_material_rejects_non_pdf(store, tmp_path):
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"not a pdf at all")
    with pytest.raises(StoreError):
        store.add_material_file(fake)


def test_update_material_keeps_file_fields(store, tmp_path):
    m = store.add_material_file(make_pdf(tmp_path / "x.pdf"))
    tampered = m.model_copy(update={"name": "新名稱", "filename": "../../evil.pdf", "pages": 99})
    saved = store.update_material(tampered)
    assert saved.name == "新名稱" and saved.filename == m.filename and saved.pages == m.pages


def test_writes_are_atomic_and_leave_no_temp_files(store):
    for i in range(5):
        store.add_resource(Resource(name=f"r{i}"))
    leftovers = [p.name for p in store.root.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    json.loads((store.root / "resources.json").read_text(encoding="utf-8"))


def test_local_config_roundtrip(tmp_path):
    local = LocalConfigStore(tmp_path / "local")
    assert local.load().data_dir == ""
    local.update(data_dir="\\\\NAS\\icope", reader_hint="EZ100")
    assert local.load().reader_hint == "EZ100"


def test_credentials_store(tmp_path):
    creds = CredentialStore(tmp_path / "auth" / "hpdcs_credentials.json")
    assert creds.get() is None
    with pytest.raises(ValueError):
        creds.save("", "pw")
    creds.save(" clinic01 ", "pw")
    assert creds.get() == ("clinic01", "pw")
    assert creds.masked_account() == "cli***"
    creds.delete()
    assert creds.get() is None
    assert mask_account("ab") == "a***"


def test_audit_log_never_contains_plain_id(tmp_path):
    log = AuditLog(tmp_path / "logs" / "audit.log")
    log.record("icope_query", pid=pid_tag("A123456789"), verdict="done")
    text = (tmp_path / "logs" / "audit.log").read_text(encoding="utf-8")
    assert "A123456789" not in text
    assert pid_tag("A123456789") in text and len(pid_tag("a123456789")) == 8


def test_replace_material_file_keeps_identity_and_settings(store, tmp_path):
    material = store.add_material_file(make_pdf(tmp_path / "a.pdf", pages=1), name="營養單張", domains=["nutrition"])
    store.material_file(material).unlink()
    updated = store.replace_material_file(material.id, make_pdf(tmp_path / "b.pdf", pages=3, text="new"))
    assert (updated.id, updated.name, updated.domains, updated.pages) == (material.id, "營養單張", ["nutrition"], 3)
    assert store.material_file(updated).exists() and store.list_materials()[0].sha256 == updated.sha256
    other = store.add_material_file(make_pdf(tmp_path / "c.pdf", text="other"))
    with pytest.raises(StoreError):
        store.replace_material_file(material.id, store.material_file(other))


def test_default_flag_is_stored_per_record_and_old_files_load_without_it(store, tmp_path):
    (store.root / "resources.json").write_text(
        json.dumps({"schema_version": 1, "resources": [{"id": "aaaaaaaaaaaa", "name": "舊版資料"}]}), encoding="utf-8")
    old = store.list_resources()[0]
    assert old.include_by_default is False
    added = store.add_resource(Resource(name="記憶門診", domains=["cognitive"], include_by_default=True))
    assert store.get_resource(added.id).include_by_default is True
    assert store.patch_resources([added.id], include_by_default=False) == 1      # 批次按鈕：只改指定的那幾筆
    assert store.get_resource(added.id).include_by_default is False
    assert store.get_resource(old.id).name == "舊版資料"

    material = store.add_material_file(make_pdf(tmp_path / "a.pdf"), domains=["nutrition"], include_by_default=True)
    assert store.get_material(material.id).include_by_default is True
    replaced = store.replace_material_file(material.id, make_pdf(tmp_path / "b.pdf", pages=2, text="new"))
    assert replaced.include_by_default is True                                     # 換 PDF 不會掉旗標
    edited = store.update_material(replaced.model_copy(update={"include_by_default": False}))
    assert edited.include_by_default is False
    assert store.add_material_file(make_pdf(tmp_path / "c.pdf", text="other")).include_by_default is False


def test_missing_data_folder_is_reported_not_treated_as_empty(store, tmp_path):
    """程式碼審查 P1：資料夾不見時要丟錯，不能回傳空清單讓畫面把已選內容清掉。"""
    store.add_resource(Resource(name="記憶門診"))
    store.root.rename(tmp_path / "offline")
    with pytest.raises(DataFolderUnavailable):
        store.list_resources()


def test_replace_material_file_keeps_original_when_list_write_fails(store, tmp_path, monkeypatch):
    """程式碼審查 P2：清單寫入失敗時，原本的 PDF 與紀錄都不變，新檔不殘留。"""
    import icope_tool.store as store_module
    material = store.add_material_file(make_pdf(tmp_path / "a.pdf", pages=1, text="old"), name="營養單張")
    original_bytes = store.material_file(material).read_bytes()

    def broken(_path, _obj):
        raise store_module.StoreIOError("網路磁碟暫時斷線")

    monkeypatch.setattr(store_module, "_write_model", broken)
    with pytest.raises(StoreError):
        store.replace_material_file(material.id, make_pdf(tmp_path / "b.pdf", pages=3, text="new"))
    monkeypatch.undo()
    kept = store.list_materials()[0]
    assert (kept.filename, kept.pages, kept.sha256) == (material.filename, material.pages, material.sha256)
    assert store.material_file(kept).read_bytes() == original_bytes
    assert sorted(p.name for p in store.materials_dir.glob("*.pdf")) == [material.filename]


@pytest.mark.parametrize("filename", ["../outside.pdf", r"..\outside.pdf", "sub/inner.pdf", "C:/Windows/x.pdf",
                                      r"\\server\share\x.pdf", "payload.exe", "..", "x.pdf:stream"])
def test_a_tampered_material_list_cannot_point_outside_the_materials_folder(store, tmp_path, filename):
    """多台電腦共用資料夾時，清單裡的檔名會被拿去開啟與刪除：只接受單張資料夾裡的單純 PDF 檔名。

    一台被入侵的電腦若能把清單改成別的路徑，其他電腦按「預覽」就會開啟那個檔案、「取代」匯入時會刪掉它。
    """
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"not ours")
    material = store.add_material_file(make_pdf(tmp_path / "a.pdf"), name="營養單張")
    listing = store.root / store.MATERIALS
    doc = json.loads(listing.read_text(encoding="utf-8"))
    doc["materials"][0]["filename"] = filename
    listing.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(StoreError):
        store.list_materials()
    assert outside.read_bytes() == b"not ours" and store.material_file(material).exists()


def test_material_file_names_made_by_the_app_are_accepted(store, tmp_path):
    from icope_tool.models import Material
    material = store.add_material_file(make_pdf(tmp_path / "a.pdf"), name="營養單張")
    assert store.list_materials()[0].filename == material.filename
    assert Material(name="草稿", filename="").filename == ""          # 編輯視窗的草稿還沒有檔案
