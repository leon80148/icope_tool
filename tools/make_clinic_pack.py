"""把院所自己整理的轉介資源清單（JSON）和一個資料夾的衛教 PDF，做成本工具可以匯入的設定包（zip）。

用法：python tools/make_clinic_pack.py <清單.json> <PDF 資料夾> <輸出.zip>

做法：在暫存資料夾用程式自己的 DataStore 加入資源與單張，再用程式的 export_pack 匯出，
所以格式、頁數、雜湊與檔名都和「設定 › 匯出／匯入」匯出的一模一樣；匯出後會讀回來驗證一次才交出檔案。
程式預設的「本院門診追蹤」會一起放進設定包：合併匯入時它和院所原本那一筆視為重複（不會變兩筆），
取代匯入時也不會因此少了門診追蹤。

清單格式（欄位名稱與程式的資料模型相同）。打錯的鍵、欄位或項目代碼、加了引號的 "true"／"false"、
名稱與地址都相同的兩筆資源，都會直接報錯、不產生檔案，不會默默丟掉或做出內容不對的設定包。
最外層底線開頭的鍵（例如 "_source"）當作給人看的註解：
{
  "resources": [{"name": "…", "type": "醫療院所", "domains": ["cognitive"], "phones": ["05-…"], "hours": "…",
                 "address": "…", "contact": "…", "website": "…", "note": "…", "pinned": true,
                 "include_by_default": false}],
  "materials": [{"file": "營養.pdf", "name": "…", "description": "…", "domains": ["nutrition"],
                 "include_by_default": false}]
}
domains 用項目代碼：cognitive 認知、mobility 行動、nutrition 營養、vision 視力、hearing 聽力、depression 憂鬱、
medication 用藥、social 社會。

院所的清單與 PDF 常含聯絡人姓名、手機與院所自製內容：請放在 git 忽略的 packs/ 底下，
不要放進 examples/（那個資料夾會跟著程式一起發佈給所有使用者）。
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from icope_tool.domains import DOMAIN_BY_ID, DOMAIN_IDS, domain_names  # noqa: E402
from icope_tool.models import Resource  # noqa: E402
from icope_tool.services.pack import PackError, export_pack, read_pack, validate_pack_materials  # noqa: E402
from icope_tool.store import DataStore, StoreError, atomic_write_bytes  # noqa: E402

RESOURCE_FIELDS = set(Resource.model_fields) - {"id"}
MATERIAL_FIELDS = {"file", "name", "description", "domains", "include_by_default"}
FLAGS = ("pinned", "include_by_default", "enabled")


class PackSourceError(Exception):
    """清單或 PDF 有問題（訊息直接給做設定包的人看）。"""


@dataclass
class Summary:
    resources: int
    materials: int
    pages: int
    uncovered: list[str]          # 院所自己列的資源沒有涵蓋到的項目（不算程式預設、適用全部項目的「本院門診追蹤」）


def _read_source(source: Path) -> tuple[list, list]:
    try:
        spec = json.loads(Path(source).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackSourceError(f"無法讀取清單 {source}：{exc}") from exc
    if not isinstance(spec, dict):
        raise PackSourceError('清單最外層要是 {"resources": […], "materials": […]}')
    unknown = sorted(key for key in spec if key not in ("resources", "materials") and not key.startswith("_"))
    if unknown:      # 鍵打錯（例如 resouces）時整份清單會被當成空的，所以不認得的鍵一律報錯；底線開頭的當作註解
        raise PackSourceError(f"清單最外層有不認得的鍵：{'、'.join(unknown)}（可用：resources、materials；底線開頭的鍵當作註解）")
    lists = []
    for key in ("resources", "materials"):
        value = spec.get(key, [])
        if not isinstance(value, list):
            raise PackSourceError(f"{key} 要寫成清單（用 [ ] 包起來）")
        lists.append(value)
    if not any(lists):
        raise PackSourceError("清單裡沒有任何轉介資源或衛教單張")
    return lists[0], lists[1]


def _check_entry(kind: str, index: int, entry: dict, allowed: set[str]) -> str:
    name = entry.get("name") if isinstance(entry, dict) else None
    if not isinstance(name, str) or not name.strip():      # null、0 這類空值：單張會被默默改用 PDF 的檔名當名稱
        raise PackSourceError(f"{kind}第 {index} 筆的 name 要是一段文字，不能空白：{entry!r}")
    name = name.strip()
    unknown = sorted(set(entry) - allowed)
    if unknown:
        raise PackSourceError(f"{kind}「{name}」有不認得的欄位：{'、'.join(unknown)}（可用：{'、'.join(sorted(allowed))}）")
    domains = entry.get("domains") or []
    if not isinstance(domains, list):
        raise PackSourceError(f'{kind}「{name}」的 domains 要寫成清單，例如 ["cognitive"]')
    wrong = [d for d in domains if d not in DOMAIN_IDS]
    if wrong:
        raise PackSourceError(f"{kind}「{name}」的項目代碼不正確：{'、'.join(map(str, wrong))}（可用：{'、'.join(DOMAIN_IDS)}）")
    if not domains:
        raise PackSourceError(f"{kind}「{name}」沒有填適用項目（domains），匯入後不會出現在任何項目的建議清單")
    for flag in FLAGS:       # "false" 這種字串在 Python 是真值：不擋下來，單張會被默默設成院所預設
        if flag in entry and not isinstance(entry[flag], bool):
            raise PackSourceError(f"{kind}「{name}」的 {flag} 只能寫 true 或 false（不加引號），不能是 {entry[flag]!r}")
    return name


def build_pack(source: Path, pdf_dir: Path, output: Path) -> Summary:
    output = Path(output)
    if output.suffix.lower() != ".zip":      # 程式用副檔名分辨 zip 設定包與 JSON 示範資料
        raise PackSourceError(f"輸出的檔名要以 .zip 結尾：{output}")
    resources, materials = _read_source(source)
    models: list[Resource] = []
    first_seen: dict[tuple[str, str], int] = {}
    for index, entry in enumerate(resources, 1):
        name = _check_entry("資源", index, entry, RESOURCE_FIELDS)
        try:
            model = Resource.model_validate(entry)
        except ValueError as exc:
            raise PackSourceError(f"資源「{name}」的欄位格式不正確：{exc}") from exc
        if model.dedupe_key() in first_seen:      # 合併匯入會略過、取代匯入卻會留下兩筆
            raise PackSourceError(f"資源第 {first_seen[model.dedupe_key()]} 筆和第 {index} 筆的名稱與地址都相同"
                                  f"（「{model.name}」），程式會把它們當成同一筆")
        first_seen[model.dedupe_key()] = index
        models.append(model)
    for index, entry in enumerate(materials, 1):
        name = _check_entry("單張", index, entry, MATERIAL_FIELDS)
        file_name = entry.get("file")
        if not isinstance(file_name, str) or not file_name.strip():
            raise PackSourceError(f"單張「{name}」沒有填 file（PDF 資料夾裡的檔名）")
        if not (Path(pdf_dir) / file_name).is_file():
            raise PackSourceError(f"單張「{name}」的檔案不存在：{Path(pdf_dir) / file_name}")

    with tempfile.TemporaryDirectory(prefix="icope-pack-") as work:
        store = DataStore(Path(work) / "data")
        store.ensure_ready(create=True)                  # 會放進程式預設的「本院門診追蹤」
        for seeded in store.list_resources():
            if seeded.dedupe_key() in first_seen:
                raise PackSourceError(f"資源第 {first_seen[seeded.dedupe_key()]} 筆「{seeded.name}」和程式預設的那一筆同名："
                                      "程式預設的會自動放進設定包，請把清單裡這一筆刪掉或改名")
        try:
            for model in models:
                store.add_resource(model)
            for entry in materials:
                store.add_material_file(Path(pdf_dir) / entry["file"], entry["name"], entry.get("description", ""),
                                        entry["domains"], entry.get("include_by_default", False))
            built = Path(work) / "pack.zip"
            export_pack(store, built)
            loaded = read_pack(built)                    # 讀回來驗證：交出去的檔案一定匯得進去
            validate_pack_materials(loaded, Path(work) / "check")
        except (StoreError, PackError, ValueError) as exc:
            raise PackSourceError(str(exc)) from exc
        try:
            atomic_write_bytes(output, built.read_bytes())      # 先寫暫存檔再換上去：寫到一半失敗，上一次做好的設定包不會壞掉
        except OSError as exc:
            raise PackSourceError(f"無法寫入 {output}：{exc.strerror or exc}（原本的檔案沒有動）") from exc
        uncovered = [d for d in DOMAIN_IDS if not any(d in model.domains for model in models)]
        return Summary(loaded.resource_count, loaded.material_count, sum(m.pages for m in loaded.manifest.materials),
                       uncovered)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    try:
        summary = build_pack(Path(argv[0]), Path(argv[1]), Path(argv[2]))
    except PackSourceError as exc:
        print(f"沒有產生設定包：{exc}")
        return 1
    loaded = read_pack(Path(argv[2]))
    print(f"完成：{argv[2]}（轉介資源 {summary.resources} 筆、衛教單張 {summary.materials} 份、共 {summary.pages} 頁）")
    for domain_id in DOMAIN_IDS:
        names = [r.name for r in loaded.manifest.resources if domain_id in r.domains]
        leaflets = [m.name for m in loaded.manifest.materials if domain_id in m.domains]
        print(f"  {DOMAIN_BY_ID[domain_id].title}：資源 {len(names)} 筆、單張 {len(leaflets)} 份")
    defaults = [r.name for r in loaded.manifest.resources if r.include_by_default] + \
               [m.name for m in loaded.manifest.materials if m.include_by_default]
    print("  院所預設：" + ("、".join(defaults) if defaults else "沒有（匯入後在程式裡選取，按「設為院所預設」）"))
    if summary.uncovered:
        print(f"  注意：清單裡沒有適用「{domain_names(summary.uncovered)}」的資源（這些項目只有程式預設的「本院門診追蹤」）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
