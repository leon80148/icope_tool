"""轉介衛教單的選取狀態（不依賴 Qt，可單獨測試）。

選資源的規則沿用參考專案：在某個項目勾選一個資源時，若它也適用其他「已選的」項目，會一併套用；
取消勾選同樣一併取消。從資源庫搜尋加入的（未標記適用該項目）只加在目前項目。

院所預設（include_by_default）只在使用者明確按「帶入院所預設」時加入，而且一次只處理還沒帶入過的項目：
勾項目本身不會加入任何內容，誤勾再取消不會留下沒人選過的東西。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from icope_tool.domains import DOMAIN_IDS, sort_domain_ids
from icope_tool.models import Material, Resource


def sort_resources(resources: Iterable[Resource]) -> list[Resource]:
    """常用在前，再依類型、名稱。"""
    return sorted(resources, key=lambda r: (not r.pinned, r.type or "～", r.name))


def suggested_resources(resources: Iterable[Resource], domain_id: str) -> list[Resource]:
    return sort_resources(r for r in resources if r.enabled and domain_id in r.domains)


def default_resource_ids(resources: Iterable[Resource], domain_id: str) -> list[str]:
    return [r.id for r in suggested_resources(resources, domain_id) if r.include_by_default]


def default_material_ids(materials: Iterable[Material], domain_id: str) -> list[str]:
    return [m.id for m in materials if m.enabled and m.include_by_default and domain_id in m.domains]


@dataclass(frozen=True)
class AppliedDefaults:
    domains: tuple[str, ...]     # 這次處理的項目；空的表示什麼都沒做
    resources: int               # 實際新增的轉介資源（同一筆加進多個項目只算一次）
    materials: int               # 實際新增的單張（只多了來源紀錄不算）


def group_by_type(resources: Iterable[Resource]) -> list[tuple[str, list[Resource]]]:
    """回傳 [(群組名稱, 資源)]；常用獨立成第一組。"""
    pinned, groups = [], {}
    for resource in resources:
        if resource.pinned:
            pinned.append(resource)
        else:
            groups.setdefault(resource.type or "其他", []).append(resource)
    result = [("常用", pinned)] if pinned else []
    result.extend(sorted(groups.items(), key=lambda item: (item[0] == "其他", item[0])))
    return result


@dataclass
class ReferralState:
    domains: list[str] = field(default_factory=list)
    resources: dict[str, list[str]] = field(default_factory=dict)
    materials: list[str] = field(default_factory=list)
    patient_name: str = ""
    patient_pid: str = ""        # 不會印在單上，只用來確認沒有把兩位長者的內容混在一起
    assessor: str = ""
    notes: str = ""
    parked: dict[str, list[str]] = field(default_factory=dict, repr=False)   # 取消勾選的項目暫存已選資源
    # 院所預設：只描述「目前勾選」的項目，所以換長者保留勾選時不必另外清。
    # 不變條件：default_materials 的 key ⊆ defaults_applied ⊆ domains；紀錄的單張都在 materials 裡；沒有空清單。
    defaults_applied: set[str] = field(default_factory=set, repr=False)      # 已帶入過院所預設的項目
    default_materials: dict[str, list[str]] = field(default_factory=dict, repr=False)   # 項目 → 它帶入的單張

    # ---- 項目 ----
    def set_domain(self, domain_id: str, selected: bool) -> None:
        """取消勾選時暫存這個項目已選的資源；同一份轉介單再勾回來會還原。

        院所預設帶入的單張跟著最後一個帶入它的項目離開；勾回來只還原資源，預設要再明確按一次才會帶入。
        """
        if domain_id not in DOMAIN_IDS:
            return
        if selected:
            if domain_id not in self.domains:
                self.domains = sort_domain_ids([*self.domains, domain_id])
            restored = self.parked.pop(domain_id, [])
            chosen = self.resources.setdefault(domain_id, [])
            chosen.extend(i for i in restored if i not in chosen)
        else:
            self.domains = [d for d in self.domains if d != domain_id]
            chosen = self.resources.pop(domain_id, [])
            if chosen:
                self.parked[domain_id] = chosen
            self.defaults_applied.discard(domain_id)
            for material_id in self.default_materials.pop(domain_id, []):
                if material_id in self.materials and not self._claimed(material_id):
                    self.materials.remove(material_id)

    # ---- 資源 ----
    def applicable_domains(self, resource: Resource) -> list[str]:
        return [d for d in self.domains if d in resource.domains]

    def is_selected(self, resource_id: str, domain_id: str) -> bool:
        return resource_id in self.resources.get(domain_id, [])

    def set_resource(self, resource: Resource, domain_id: str, selected: bool) -> list[str]:
        """勾選或取消；回傳實際受影響的項目。"""
        if domain_id not in self.domains:
            return []
        targets = sort_domain_ids({domain_id, *self.applicable_domains(resource)})
        for target in targets:
            chosen = self.resources.setdefault(target, [])
            if selected and resource.id not in chosen:
                chosen.append(resource.id)
            elif not selected and resource.id in chosen:
                chosen.remove(resource.id)
        return targets

    def add_to_domain(self, resource_ids: Iterable[str], domain_id: str) -> int:
        if domain_id not in self.domains:
            return 0
        chosen = self.resources.setdefault(domain_id, [])
        added = 0
        for resource_id in resource_ids:
            if resource_id not in chosen:
                chosen.append(resource_id)
                added += 1
        return added

    def remove_from_domain(self, resource_id: str, domain_id: str) -> None:
        chosen = self.resources.get(domain_id, [])
        if resource_id in chosen:
            chosen.remove(resource_id)

    def domains_without_resources(self) -> list[str]:
        return [d for d in self.domains if not self.resources.get(d)]

    def unique_resource_ids(self) -> list[str]:
        seen: dict[str, None] = {}
        for domain in self.domains:
            for resource_id in self.resources.get(domain, []):
                seen.setdefault(resource_id, None)
        return list(seen)

    # ---- 單張 ----
    def set_material(self, material_id: str, selected: bool) -> None:
        """使用者自己勾選或取消（畫面同步勾選狀態不要呼叫這裡）：這份單張從此不再跟著項目走。"""
        self._release_material(material_id)
        if selected and material_id not in self.materials:
            self.materials.append(material_id)
        elif not selected and material_id in self.materials:
            self.materials.remove(material_id)

    def select_materials(self, material_ids: Iterable[str]) -> None:
        """「勾選所有建議的單張」：整批都算使用者自己選的，包含原本由院所預設帶入的。"""
        for material_id in material_ids:
            self.set_material(material_id, True)

    def _claimed(self, material_id: str) -> bool:
        return any(material_id in ids for ids in self.default_materials.values())

    def _release_material(self, material_id: str) -> None:
        for domain_id, ids in list(self.default_materials.items()):
            if material_id in ids:
                kept = [i for i in ids if i != material_id]
                if kept:
                    self.default_materials[domain_id] = kept
                else:
                    del self.default_materials[domain_id]

    # ---- 院所預設 ----
    def pending_default_domains(self, resources: Iterable[Resource], materials: Iterable[Material]) -> list[str]:
        """已勾選、還沒帶入過、而且院所有替它設預設的項目。"""
        resources, materials = list(resources), list(materials)
        return [d for d in self.domains if d not in self.defaults_applied
                and (default_resource_ids(resources, d) or default_material_ids(materials, d))]

    def apply_defaults(self, resources: Iterable[Resource], materials: Iterable[Material]) -> AppliedDefaults:
        """只處理 pending 的項目。資源逐項加入（不走 set_resource 的跨項目套用，別的項目不會被補回）；
        單張：原本人工勾選的不認領，其餘記在帶入它的每個項目名下。"""
        resources, materials = list(resources), list(materials)
        pending = self.pending_default_domains(resources, materials)
        manual = {i for i in self.materials if not self._claimed(i)}      # 迴圈前就決定：結果與處理順序無關
        added_resources: set[str] = set()
        added_materials = 0
        for domain_id in pending:
            chosen = self.resources.setdefault(domain_id, [])
            for resource_id in default_resource_ids(resources, domain_id):
                if resource_id not in chosen:
                    chosen.append(resource_id)
                    added_resources.add(resource_id)
            record = []
            for material_id in default_material_ids(materials, domain_id):
                if material_id in manual:
                    continue
                if material_id not in self.materials:
                    self.materials.append(material_id)
                    added_materials += 1
                record.append(material_id)
            if record:
                self.default_materials[domain_id] = record
            self.defaults_applied.add(domain_id)
        return AppliedDefaults(tuple(pending), len(added_resources), added_materials)

    def suggested_material_ids(self, materials: Iterable[Material]) -> list[str]:
        wanted = set(self.domains)
        return [m.id for m in materials if m.enabled and wanted.intersection(m.domains)]

    # ---- 維護 ----
    def prune(self, resources: Iterable[Resource], materials: Iterable[Material]) -> bool:
        """資料被刪除或停用後，把已不存在的選取拿掉。回傳是否有變動。"""
        valid_resources = {r.id for r in resources if r.enabled}
        valid_materials = {m.id for m in materials if m.enabled}
        changed = False
        for domain in list(self.resources):
            kept = [i for i in self.resources[domain] if i in valid_resources]
            if kept != self.resources[domain]:
                self.resources[domain] = kept
                changed = True
        for domain in list(self.parked):
            self.parked[domain] = [i for i in self.parked[domain] if i in valid_resources]
        kept_materials = [i for i in self.materials if i in valid_materials]
        if kept_materials != self.materials:
            self.materials = kept_materials
            changed = True
        # 來源紀錄跟著實際選取縮小；「帶入過」的標記不因內容被刪或預設旗標改變而清掉，也不補替代內容
        self.defaults_applied &= set(self.domains)
        self.default_materials = {
            d: kept for d, ids in self.default_materials.items()
            if d in self.defaults_applied and (kept := [i for i in ids if i in self.materials])
        }
        return changed

    def has_parked(self) -> bool:
        return any(self.parked.values())       # prune 後可能留下空清單，不能只看 dict 是否為空

    def is_empty(self) -> bool:
        return (not self.domains and not self.materials and not self.patient_name and not self.notes
                and not self.has_parked())

    def has_selections(self) -> bool:
        """草稿是否有內容（含取消勾選後暫存的資源）：換長者確認、關閉保護都靠它。"""
        return bool(self.domains or self.materials or self.notes) or self.has_parked()

    def can_print(self) -> bool:
        return bool(self.domains or self.materials)

    def reset(self, keep_assessor: bool = True) -> None:
        assessor = self.assessor if keep_assessor else ""
        self.domains, self.resources, self.materials, self.parked = [], {}, [], {}
        self.defaults_applied, self.default_materials = set(), {}
        self.patient_name, self.patient_pid, self.notes = "", "", ""
        self.assessor = assessor


def ordered_resources_for_domain(state: ReferralState, domain_id: str, by_id: dict[str, Resource]) -> list[Resource]:
    """PDF 內的排列：建議清單的順序（常用、類型、名稱），搜尋加入的排最後。"""
    chosen = [by_id[i] for i in state.resources.get(domain_id, []) if i in by_id]
    tagged = sort_resources(r for r in chosen if domain_id in r.domains)
    extra = [r for r in chosen if domain_id not in r.domains]
    return tagged + extra
