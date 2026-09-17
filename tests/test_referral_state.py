import pytest

from icope_tool.models import Material, Resource
from icope_tool.ui.referral.state import (
    ReferralState, group_by_type, ordered_resources_for_domain, sort_resources, suggested_resources,
)


def _res(name, domains, pinned=False, type_="", enabled=True, default=False):
    return Resource(name=name, domains=domains, pinned=pinned, type=type_, enabled=enabled,
                    include_by_default=default)


def _leaflet(material_id, domains, default=True, enabled=True):
    return Material(id=material_id, name=material_id, filename=f"{material_id}.pdf", domains=domains,
                    include_by_default=default, enabled=enabled)


def test_domains_are_kept_in_form_order_and_removal_clears_resources():
    state = ReferralState()
    state.set_domain("social", True)
    state.set_domain("cognitive", True)
    assert state.domains == ["cognitive", "social"]
    station = _res("據點", ["cognitive", "social"])
    state.set_resource(station, "cognitive", True)
    state.set_domain("social", False)
    assert "social" not in state.resources
    state.set_domain("unknown", True)
    assert state.domains == ["cognitive"]


def test_selecting_resource_applies_to_other_selected_applicable_domains():
    state = ReferralState()
    for d in ("cognitive", "mobility", "hearing"):
        state.set_domain(d, True)
    station = _res("巷弄長照站", ["cognitive", "mobility", "depression"])
    affected = state.set_resource(station, "cognitive", True)
    assert affected == ["cognitive", "mobility"]
    assert state.is_selected(station.id, "mobility")
    assert not state.is_selected(station.id, "hearing")
    state.set_resource(station, "mobility", False)
    assert not state.is_selected(station.id, "cognitive")
    assert state.domains_without_resources() == ["cognitive", "mobility", "hearing"]


def test_picker_additions_only_affect_current_domain():
    state = ReferralState()
    state.set_domain("vision", True)
    state.set_domain("hearing", True)
    assert state.add_to_domain(["a", "b", "a"], "hearing") == 2
    assert state.resources["vision"] == []
    assert state.add_to_domain(["x"], "nutrition") == 0


def test_unique_resources_and_can_print():
    state = ReferralState()
    assert not state.can_print()
    state.set_domain("cognitive", True)
    state.set_domain("mobility", True)
    shared = _res("共用", ["cognitive", "mobility"])
    state.set_resource(shared, "cognitive", True)
    assert state.unique_resource_ids() == [shared.id]
    assert state.can_print()


def test_prune_removes_deleted_and_disabled_items():
    state = ReferralState()
    state.set_domain("cognitive", True)
    keep = _res("保留", ["cognitive"])
    gone = _res("已停用", ["cognitive"], enabled=False)
    state.add_to_domain([keep.id, gone.id, "deleted"], "cognitive")
    m1 = Material(id="m1", name="a", filename="a.pdf")
    m2 = Material(id="m2", name="b", filename="b.pdf", enabled=False)
    state.set_material("m1", True)
    state.set_material("m2", True)
    assert state.prune([keep, gone], [m1, m2]) is True
    assert state.resources["cognitive"] == [keep.id]
    assert state.materials == ["m1"]
    assert state.prune([keep, gone], [m1, m2]) is False


def test_suggested_materials_follow_selected_domains():
    state = ReferralState()
    state.set_domain("nutrition", True)
    materials = [Material(id="n", name="營養", filename="n.pdf", domains=["nutrition"]),
                 Material(id="f", name="跌倒", filename="f.pdf", domains=["mobility"]),
                 Material(id="x", name="停用", filename="x.pdf", domains=["nutrition"], enabled=False)]
    assert state.suggested_material_ids(materials) == ["n"]


def test_sorting_grouping_and_pdf_order():
    a = _res("乙醫院", ["cognitive"], type_="醫療院所")
    b = _res("甲據點", ["cognitive"], type_="巷弄長照站")
    c = _res("門診追蹤", ["cognitive"], pinned=True, type_="門診追蹤")
    d = _res("沒有類型", ["cognitive"])
    e = _res("停用", ["cognitive"], enabled=False)
    assert [r.name for r in sort_resources([a, b, c, d])] == ["門診追蹤", "甲據點", "乙醫院", "沒有類型"]
    assert [r.name for r in suggested_resources([a, e], "cognitive")] == ["乙醫院"]
    groups = group_by_type([a, b, c, d])
    assert [g for g, _ in groups] == ["常用", "巷弄長照站", "醫療院所", "其他"]

    extra = _res("搜尋加入的", ["hearing"])
    state = ReferralState()
    state.set_domain("cognitive", True)
    state.add_to_domain([extra.id, a.id, c.id], "cognitive")
    by_id = {r.id: r for r in (a, c, extra)}
    assert [r.name for r in ordered_resources_for_domain(state, "cognitive", by_id)] == ["門診追蹤", "乙醫院", "搜尋加入的"]


def test_reset_keeps_assessor():
    state = ReferralState(assessor="林護理師", patient_name="王小明", notes="x")
    state.set_domain("cognitive", True)
    state.reset()
    assert state.is_empty() and state.assessor == "林護理師"


def test_unchecked_domain_restores_its_resources_when_checked_again():
    state = ReferralState()
    state.set_domain("cognitive", True)
    clinic = _res("記憶門診", ["cognitive"])
    state.set_resource(clinic, "cognitive", True)
    state.set_domain("cognitive", False)
    assert state.domains == [] and "cognitive" not in state.resources
    state.set_domain("cognitive", True)
    assert state.is_selected(clinic.id, "cognitive")
    state.set_domain("cognitive", False)
    state.prune([], [])                         # 暫存中的資源被刪除：勾回來不會出現
    state.set_domain("cognitive", True)
    assert not state.is_selected(clinic.id, "cognitive")
    state.reset()
    assert state.parked == {}


# ---------------------------------------------------------------------------- 院所預設（明確、逐項帶入）
def test_new_drafts_do_not_share_default_tracking():
    first, second = ReferralState(), ReferralState()
    first.defaults_applied.add("cognitive")
    first.default_materials["cognitive"] = ["m"]
    assert second.defaults_applied == set() and second.default_materials == {}


def test_only_enabled_flagged_and_tagged_items_are_brought_in():
    wanted = _res("記憶門診", ["cognitive"], default=True)
    resources = [wanted, _res("常用但不是預設", ["cognitive"], pinned=True),
                 _res("已停用", ["cognitive"], default=True, enabled=False), _res("別的項目", ["vision"], default=True)]
    leaflets = [_leaflet("cog", ["cognitive"]), _leaflet("off", ["cognitive"], enabled=False),
                _leaflet("plain", ["cognitive"], default=False), _leaflet("eye", ["vision"])]
    state = ReferralState()
    state.set_domain("cognitive", True)
    result = state.apply_defaults(resources, leaflets)
    assert (result.domains, result.resources, result.materials) == (("cognitive",), 1, 1)
    assert state.resources["cognitive"] == [wanted.id] and state.materials == ["cog"]


def test_defaults_apply_only_pending_domains():
    clinic = _res("記憶門診", ["cognitive"], default=True)
    station = _res("巷弄長照站", ["cognitive", "hearing"], default=True)        # 兩個項目共用
    resources = [clinic, station, _res("一般資源", ["cognitive"])]
    leaflets = [_leaflet("cog", ["cognitive"])]
    state = ReferralState()
    state.set_domain("cognitive", True)
    assert state.pending_default_domains(resources, leaflets) == ["cognitive"]
    first = state.apply_defaults(resources, leaflets)
    assert (first.domains, first.resources, first.materials) == (("cognitive",), 2, 1)
    assert set(state.resources["cognitive"]) == {clinic.id, station.id}

    state.set_resource(station, "cognitive", False)                            # 護理師不要認知的這一筆
    state.set_domain("hearing", True)                                           # 後來才發現聽力也異常
    assert state.pending_default_domains(resources, leaflets) == ["hearing"]
    second = state.apply_defaults(resources, leaflets)
    assert (second.domains, second.resources, second.materials) == (("hearing",), 1, 0)
    assert state.resources["hearing"] == [station.id]
    assert state.resources["cognitive"] == [clinic.id]                          # 已處理過的項目不被補回

    assert state.pending_default_domains(resources, leaflets) == []
    before = (dict(state.resources), list(state.materials))
    nothing = state.apply_defaults(resources, leaflets)
    assert nothing.domains == () and (dict(state.resources), list(state.materials)) == before


def test_shared_resource_counts_once_in_the_feedback():
    station = _res("巷弄長照站", ["cognitive", "mobility"], default=True)
    state = ReferralState()
    state.set_domain("cognitive", True)
    state.set_domain("mobility", True)
    result = state.apply_defaults([station], [])
    assert result.resources == 1
    assert state.is_selected(station.id, "cognitive") and state.is_selected(station.id, "mobility")


def test_domain_without_defaults_is_never_marked_applied():
    clinic = _res("記憶門診", ["cognitive"], default=True)
    state = ReferralState()
    state.set_domain("cognitive", True)
    state.set_domain("hearing", True)
    result = state.apply_defaults([clinic], [])
    assert result.domains == ("cognitive",) and state.defaults_applied == {"cognitive"}
    ear = _res("聽力所", ["hearing"], default=True)                             # 院所後來替聽力設了預設
    assert state.pending_default_domains([clinic, ear], []) == ["hearing"]


def test_untick_and_retick_restores_parked_resources_but_never_re_adds_defaults_silently():
    clinic = _res("記憶門診", ["cognitive"], default=True)
    gym = _res("運動中心", ["mobility"], default=True)
    ear = _res("聽力所", ["hearing"], default=True)
    resources, leaflets = [clinic, gym, ear], [_leaflet("cog", ["cognitive"]), _leaflet("fall", ["mobility"])]
    state = ReferralState()
    state.set_domain("cognitive", True)
    state.set_domain("mobility", True)
    state.apply_defaults(resources, leaflets)
    state.set_domain("hearing", True)
    assert state.apply_defaults(resources, leaflets).domains == ("hearing",)

    state.set_domain("cognitive", False)
    assert "cognitive" not in state.defaults_applied and "cognitive" not in state.default_materials
    assert state.materials == ["fall"] and state.parked == {"cognitive": [clinic.id]}
    state.set_domain("cognitive", True)
    assert state.resources["cognitive"] == [clinic.id]                          # parked 還原
    assert state.materials == ["fall"]                                          # 預設單張不會自己回來
    assert state.pending_default_domains(resources, leaflets) == ["cognitive"]  # 要再明確按一次
    state.apply_defaults(resources, leaflets)
    assert sorted(state.materials) == ["cog", "fall"] and state.resources["cognitive"] == [clinic.id]


def test_retick_is_pending_only_if_defaults_still_exist():
    clinic = _res("記憶門診", ["cognitive"], default=True)
    state = ReferralState()
    state.set_domain("cognitive", True)
    state.apply_defaults([clinic], [])
    state.set_domain("cognitive", False)
    state.set_domain("cognitive", True)
    no_longer_default = clinic.model_copy(update={"include_by_default": False})
    assert state.pending_default_domains([no_longer_default], []) == []
    assert state.pending_default_domains([clinic], []) == ["cognitive"]


@pytest.mark.parametrize("order", [("cognitive", "mobility"), ("mobility", "cognitive")])
@pytest.mark.parametrize("together", [True, False])
def test_shared_default_leaflet_leaves_with_its_last_domain(order, together):
    shared = _leaflet("shared", ["cognitive", "mobility"])
    state = ReferralState()
    for domain in order:
        state.set_domain(domain, True)
        if not together:
            state.apply_defaults([], [shared])                                  # 分兩次帶入
    if together:
        assert state.apply_defaults([], [shared]).materials == 1                # 一次帶入：單張只算一份
    assert state.materials == ["shared"] and set(state.default_materials) == {"cognitive", "mobility"}
    state.set_domain(order[0], False)
    assert state.materials == ["shared"]
    state.set_domain(order[1], False)
    assert state.materials == [] and state.default_materials == {} and state.defaults_applied == set()
    assert state.is_empty() and not state.has_selections()


def test_manually_chosen_leaflet_is_never_claimed_but_the_domain_still_counts_as_applied():
    shared = _leaflet("shared", ["cognitive", "mobility"])
    state = ReferralState()
    state.set_material("shared", True)                                          # 護理師自己先勾的
    state.set_domain("cognitive", True)
    state.set_domain("mobility", True)
    result = state.apply_defaults([], [shared])
    assert result.domains == ("cognitive", "mobility") and result.materials == 0
    assert state.defaults_applied == {"cognitive", "mobility"} and state.default_materials == {}
    state.set_domain("cognitive", False)
    state.set_domain("mobility", False)
    assert state.materials == ["shared"]


def test_manual_tick_or_untick_releases_default_ownership():
    leaflet = _leaflet("cog", ["cognitive"])
    state = ReferralState()
    state.set_domain("cognitive", True)
    state.apply_defaults([], [leaflet])
    state.set_material("cog", False)                                            # 人工取消
    assert state.materials == [] and state.default_materials == {}
    assert state.defaults_applied == {"cognitive"}                              # 取消內容不會讓項目變回「未帶入」
    assert state.pending_default_domains([], [leaflet]) == []

    state.set_material("cog", True)                                             # 人工重勾：從此不跟著項目走
    state.set_domain("cognitive", False)
    assert state.materials == ["cog"]


def test_select_all_suggested_makes_the_whole_batch_manual():
    brought, plain = _leaflet("a", ["cognitive"]), _leaflet("b", ["cognitive"], default=False)
    state = ReferralState()
    state.set_domain("cognitive", True)
    state.apply_defaults([], [brought, plain])
    assert state.default_materials == {"cognitive": ["a"]}
    state.select_materials(["a", "b"])                                          # 「勾選所有建議的單張」
    assert state.materials == ["a", "b"] and state.default_materials == {}
    state.set_domain("cognitive", False)
    assert state.materials == ["a", "b"]


def test_prune_and_reset_handle_default_tracking():
    clinic = _res("記憶門診", ["cognitive"], default=True)
    keep, gone = _leaflet("keep", ["cognitive"]), _leaflet("gone", ["cognitive"])
    state = ReferralState()
    state.set_domain("cognitive", True)
    state.apply_defaults([clinic], [keep, gone])
    assert state.prune([clinic], [keep, gone.model_copy(update={"enabled": False})]) is True
    assert state.materials == ["keep"] and state.default_materials == {"cognitive": ["keep"]}
    state.prune([], [])                                                          # 預設全被刪掉：不補替代內容
    assert state.materials == [] and state.default_materials == {}
    assert state.defaults_applied == {"cognitive"}                               # 也不重新開放補選
    assert state.pending_default_domains([clinic], [keep]) == []

    state.reset()
    assert state.defaults_applied == set() and state.default_materials == {} and state.is_empty()


def test_changing_a_flag_elsewhere_never_edits_the_current_draft():
    clinic = _res("記憶門診", ["cognitive"], default=True)
    leaflet = _leaflet("cog", ["cognitive"])
    state = ReferralState()
    state.set_domain("cognitive", True)
    state.apply_defaults([clinic], [leaflet])
    unflagged = [clinic.model_copy(update={"include_by_default": False})]
    assert state.prune(unflagged, [leaflet.model_copy(update={"include_by_default": False})]) is False
    assert state.resources["cognitive"] == [clinic.id] and state.materials == ["cog"]
    assert state.default_materials == {"cognitive": ["cog"]}
