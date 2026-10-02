"""core/drafts — T18 草稿（#18）與進版 promote（#19）。

草稿存在**編輯階段**、不在來源也不在目標（CONTEXT.md）。核心層純邏輯：階段是不可變的資料
結構，每個操作回**新的**階段；儲存不產生變更紀錄、不寫到目標位置——這裡根本沒有 I/O 可做，
那正是結構上的保證。內容是改好的文字＋format（第 1 層驗證要靠它）。

save_draft 擋第 1 層（人正在編輯，壞內容不該存）；adopt_draft 不擋、只回警告（偏離處置的
「先納入、待修正」要把壞內容撈進來修）。草稿與四種狀態正交：這裡不看目標／來源是否一致。
"""

import pytest

from config_manager.core.drafts import Checks, Stage, adopt_draft, discard, promote, save_draft
from config_manager.core.errors import (
    DraftInvalid,
    DraftNotFound,
    OverrideRequired,
    PromoteInvalid,
    ReasonInvalid,
)
from config_manager.core.models import ConfigList, Defaults, FileEntry, Permissions
from config_manager.core.rules import Rules, parse_rules

_GOOD = "speed: 1.5\nname: amr01\n"
_BAD = "enabled: yes\n"  # 第 1 層拒絕：布林只接受 true／false


# ── 儲存草稿 ─────────────────────────────────────────────────────────────────


def test_saving_a_draft_keeps_it_on_the_stage_under_its_uid():
    stage = save_draft(Stage(), "mfz3k9q1", _GOOD, "yaml")

    assert stage.drafts["mfz3k9q1"].text == _GOOD


def test_the_stage_is_immutable_saving_returns_a_new_one():
    # 每個操作回新的階段：呼叫端拿到的舊階段不會被偷改（可比對、可回滾）。
    before = Stage()

    save_draft(before, "mfz3k9q1", _GOOD, "yaml")

    assert before.drafts == {}


def test_saving_runs_layer_one_validation_and_refuses_invalid_content():
    with pytest.raises(DraftInvalid) as exc:
        save_draft(Stage(), "mfz3k9q1", _BAD, "yaml")

    assert exc.value.problems and exc.value.problems[0].line == 1


def test_a_refused_save_leaves_the_stage_unchanged():
    stage = save_draft(Stage(), "mfz3k9q1", _GOOD, "yaml")

    with pytest.raises(DraftInvalid):
        save_draft(stage, "mfz3k9q1", _BAD, "yaml")

    assert stage.drafts["mfz3k9q1"].text == _GOOD


def test_multiple_drafts_coexist_independently():
    stage = save_draft(Stage(), "aaaaaaa1", "a: 1\n", "yaml")
    stage = save_draft(stage, "bbbbbbb2", "b: 2\n", "yaml")

    assert {uid: d.text for uid, d in stage.drafts.items()} == {
        "aaaaaaa1": "a: 1\n",
        "bbbbbbb2": "b: 2\n",
    }


def test_saving_again_replaces_that_uids_draft_only():
    stage = save_draft(Stage(), "aaaaaaa1", "a: 1\n", "yaml")
    stage = save_draft(stage, "bbbbbbb2", "b: 2\n", "yaml")

    stage = save_draft(stage, "aaaaaaa1", "a: 3\n", "yaml")

    assert (stage.drafts["aaaaaaa1"].text, stage.drafts["bbbbbbb2"].text) == ("a: 3\n", "b: 2\n")


# ── 捨棄 ─────────────────────────────────────────────────────────────────────


def test_discarding_one_uid_removes_only_that_draft():
    stage = save_draft(Stage(), "aaaaaaa1", "a: 1\n", "yaml")
    stage = save_draft(stage, "bbbbbbb2", "b: 2\n", "yaml")

    stage = discard(stage, "aaaaaaa1")

    assert list(stage.drafts) == ["bbbbbbb2"]


def test_discarding_without_a_uid_clears_every_draft():
    stage = save_draft(Stage(), "aaaaaaa1", "a: 1\n", "yaml")
    stage = save_draft(stage, "bbbbbbb2", "b: 2\n", "yaml")

    assert discard(stage).drafts == {}


def test_discarding_an_unknown_uid_is_a_named_error_not_a_silent_noop():
    with pytest.raises(DraftNotFound):
        discard(Stage(), "nope")


# ── 先納入、待修正（adopt_draft）──────────────────────────────────────────────


def test_adopting_loads_the_target_content_even_when_it_is_invalid():
    # 偏離處置：目標現況照載，不因非法而拒載——人要把它撈進來修。
    stage, warnings = adopt_draft(Stage(), "mfz3k9q1", _BAD, "yaml")

    assert stage.drafts["mfz3k9q1"].text == _BAD
    assert warnings and warnings[0].line == 1 and warnings[0].suggestion


def test_adopting_valid_content_yields_no_warnings():
    stage, warnings = adopt_draft(Stage(), "mfz3k9q1", _GOOD, "yaml")

    assert (stage.drafts["mfz3k9q1"].text, warnings) == (_GOOD, [])


def test_save_blocks_what_adopt_merely_warns_about():
    # 兩者的分界：同一份壞內容，save 擋、adopt 載入並警告。
    with pytest.raises(DraftInvalid):
        save_draft(Stage(), "mfz3k9q1", _BAD, "yaml")
    stage, warnings = adopt_draft(Stage(), "mfz3k9q1", _BAD, "yaml")

    assert "mfz3k9q1" in stage.drafts and warnings


# ── 第 2 層：schema（#39）────────────────────────────────────────────────────

_COUNT_IS_INT = {"type": "object", "properties": {"count": {"type": "integer"}}}


def test_saving_is_refused_when_the_content_does_not_fit_the_schema():
    # 第 2 層是硬擋：型別不符 schema 的內容存不成草稿，問題指名欄位。
    with pytest.raises(DraftInvalid) as exc:
        save_draft(Stage(), "mfz3k9q1", "count: many\n", "yaml", Checks(schema=_COUNT_IS_INT))

    assert [problem.path for problem in exc.value.problems] == ["count"]
    assert "第 1 行" in str(exc.value)


def test_saving_without_a_schema_only_runs_the_first_layer():
    stage = save_draft(Stage(), "mfz3k9q1", "count: many\n", "yaml")

    assert "mfz3k9q1" in stage.drafts


def test_adopting_reports_schema_problems_as_warnings_without_refusing():
    # 偏離處置的「先納入、待修正」：內容照載，schema 的問題也列成警告，進版前要改正。
    stage, warnings = adopt_draft(
        Stage(), "mfz3k9q1", "count: many\n", "yaml", Checks(schema=_COUNT_IS_INT)
    )

    assert "mfz3k9q1" in stage.drafts
    assert [problem.path for problem in warnings] == ["count"]


# ── 進版（promote，#19）────────────────────────────────────────────────────────

_DEFAULTS = Permissions(owner="root", group="root", mode="0644")
_OWN = Permissions(owner="amr", group="ros", mode="0600")


def _entry(uid, name, permissions=None):
    return FileEntry(
        uid=uid, name=name, hostname="amr01", source=f"files/{name}.yaml",
        target=f"/etc/{name}.yaml", format="yaml", permissions=permissions,
    )


def _config_list(*entries):
    return ConfigList(list_version=1, defaults=Defaults(permissions=_DEFAULTS), files=list(entries))


def test_promoting_yields_one_promotion_per_draft_with_its_record_summary():
    # 進版成功 → 每份 config 各一筆變更紀錄，主旨指名 name@hostname（介面顯示「修改參數」）。
    stage = save_draft(Stage(), "aaaaaaa1", "a: 1\n", "yaml")
    stage = save_draft(stage, "bbbbbbb2", "b: 2\n", "yaml")

    plans = promote(stage, _config_list(_entry("aaaaaaa1", "a"), _entry("bbbbbbb2", "b")))

    assert [(p.uid, p.source, p.target, p.text, p.summary) for p in plans] == [
        ("aaaaaaa1", "files/a.yaml", "/etc/a.yaml", "a: 1\n", "修改參數（a@amr01）"),
        ("bbbbbbb2", "files/b.yaml", "/etc/b.yaml", "b: 2\n", "修改參數（b@amr01）"),
    ]


def test_promotion_uses_the_entrys_own_permissions_else_the_list_defaults():
    stage = save_draft(Stage(), "aaaaaaa1", "a: 1\n", "yaml")
    stage = save_draft(stage, "bbbbbbb2", "b: 2\n", "yaml")

    plans = promote(stage, _config_list(_entry("aaaaaaa1", "a", _OWN), _entry("bbbbbbb2", "b")))

    assert [p.permissions for p in plans] == [_OWN, _DEFAULTS]


def test_one_invalid_draft_blocks_the_whole_batch_and_names_it():
    # adopt_draft 撈進來的壞內容在進版被擋；錯誤指出是哪一份（uid）的哪一個參數（行號）。
    stage = save_draft(Stage(), "aaaaaaa1", "a: 1\n", "yaml")
    stage, _ = adopt_draft(stage, "bbbbbbb2", _BAD, "yaml")

    with pytest.raises(PromoteInvalid) as exc:
        promote(stage, _config_list(_entry("aaaaaaa1", "a"), _entry("bbbbbbb2", "b")))

    assert (exc.value.uid, exc.value.problems[0].line) == ("bbbbbbb2", 1)


def test_a_draft_whose_entry_left_the_list_blocks_the_batch():
    # 進版期間該 config 被解除管理：沒有目標可寫，整批不進版而非默默跳過那一份。
    stage = save_draft(Stage(), "aaaaaaa1", "a: 1\n", "yaml")
    stage = save_draft(stage, "gone0000", "g: 1\n", "yaml")

    with pytest.raises(PromoteInvalid) as exc:
        promote(stage, _config_list(_entry("aaaaaaa1", "a")))

    assert exc.value.uid == "gone0000"


def test_a_cleaned_up_adopted_draft_promotes_normally():
    # 改乾淨後正常進版——擋的是內容，不是 adopt 這條來路。
    stage, _ = adopt_draft(Stage(), "bbbbbbb2", _BAD, "yaml")
    stage = save_draft(stage, "bbbbbbb2", "enabled: true\n", "yaml")

    plans = promote(stage, _config_list(_entry("bbbbbbb2", "b")))

    assert [p.text for p in plans] == ["enabled: true\n"]


def test_promoting_an_empty_stage_yields_nothing():
    assert promote(Stage(), _config_list()) == []


def test_promotions_are_recorded_as_cfg_changes():
    # 進版的每一筆進版資料都是 cfg 紀錄；退版另組 kind=revert 的 Promotion 走同一條 apply（#24）。
    stage = save_draft(Stage(), "aaaaaaa1", "a: 1\n", "yaml")

    plans = promote(stage, _config_list(_entry("aaaaaaa1", "a")))

    assert [p.kind for p in plans] == ["cfg"]


def test_promotion_checks_each_draft_against_its_own_schema():
    # 進版時重驗：草稿存下之後 schema 被收緊，或 adopt 進來的內容不符 schema，都在這裡被擋。
    # 只有 bbbbbbb2 有 schema；aaaaaaa1 沒有，照舊只過第 1 層。
    stage = save_draft(Stage(), "aaaaaaa1", "count: many\n", "yaml")
    stage = save_draft(stage, "bbbbbbb2", "count: many\n", "yaml")
    listing = _config_list(_entry("aaaaaaa1", "a"), _entry("bbbbbbb2", "b"))

    with pytest.raises(PromoteInvalid) as exc:
        promote(stage, listing, {"bbbbbbb2": Checks(schema=_COUNT_IS_INT)})

    assert (exc.value.uid, exc.value.problems[0].path) == ("bbbbbbb2", "count")


# ── 第 3 層：規則的警告要填理由才略過（#42）────────────────────────────────────
# 警告不硬擋，但也不能看都不看就過：填了理由才存得成，理由跟著草稿、進版時寫進變更紀錄。

_MIN_BELOW_MAX = Rules(
    parse_rules('[[rules]]\nid = "min-below-max"\nleft = "lo"\nop = "<"\nright = "hi"\n'), {}
)
_VIOLATING = "lo: 5\nhi: 1\n"


def _with_rules(reasons=None):
    return Checks(rules=_MIN_BELOW_MAX, reasons=reasons or {})


def test_saving_content_that_breaks_a_rule_without_a_reason_is_held_back():
    # 不是硬擋的錯（DraftInvalid），是「要理由」：指名哪一條規則，草稿沒有存下。
    with pytest.raises(OverrideRequired) as exc:
        save_draft(Stage(), "mfz3k9q1", _VIOLATING, "yaml", _with_rules())

    assert [problem.rule for problem in exc.value.problems] == ["min-below-max"]
    assert "min-below-max" in str(exc.value) and "下一步" in str(exc.value)


def test_a_reason_lets_the_draft_be_saved_and_travels_with_it():
    stage = save_draft(
        Stage(), "mfz3k9q1", _VIOLATING, "yaml",
        _with_rules({"min-below-max": "現場測試低速模式"}),
    )

    assert stage.drafts["mfz3k9q1"].reasons == {"min-below-max": "現場測試低速模式"}


def test_a_blank_reason_does_not_count():
    with pytest.raises(OverrideRequired):
        save_draft(Stage(), "mfz3k9q1", _VIOLATING, "yaml", _with_rules({"min-below-max": "  "}))


@pytest.mark.parametrize("reason", ["第一行\n第二行", "理" * 201])
def test_a_reason_that_would_break_the_record_is_refused(reason):
    # 理由會寫進變更紀錄的一行：不可換行、不可過長。
    with pytest.raises(ReasonInvalid):
        save_draft(Stage(), "mfz3k9q1", _VIOLATING, "yaml", _with_rules({"min-below-max": reason}))


def test_a_reason_for_a_rule_that_is_not_broken_is_not_kept():
    # 只記下這次真的略過的規則：內容沒違反就沒有東西要略過。
    stage = save_draft(
        Stage(), "mfz3k9q1", "lo: 1\nhi: 5\n", "yaml", _with_rules({"min-below-max": "用不到"})
    )

    assert stage.drafts["mfz3k9q1"].reasons == {}


def test_a_hard_error_still_blocks_even_with_a_reason():
    # override 後的修改仍走完整驗證：理由只略過第 3 層的警告，蓋不掉第 1／2 層的硬擋。
    with pytest.raises(DraftInvalid):
        save_draft(
            Stage(), "mfz3k9q1", "lo: 5\nhi: 1\nenabled: yes\n", "yaml",
            _with_rules({"min-below-max": "理由"}),
        )


def test_adopting_lists_rule_warnings_without_asking_for_a_reason_yet():
    # 「先納入、待修正」照載；規則的警告列出來，理由留到在草稿上儲存時填。
    stage, warnings = adopt_draft(Stage(), "mfz3k9q1", _VIOLATING, "yaml", _with_rules())

    assert [problem.rule for problem in warnings] == ["min-below-max"]
    assert stage.drafts["mfz3k9q1"].reasons == {}


def test_promotion_writes_the_override_and_its_reason_into_the_record():
    # 理由記入變更紀錄：主旨之後的內文一行一條 `override(<規則>): <理由>`。
    stage = save_draft(
        Stage(), "aaaaaaa1", _VIOLATING, "yaml", _with_rules({"min-below-max": "現場測試低速模式"})
    )

    plans = promote(stage, _config_list(_entry("aaaaaaa1", "a")), {"aaaaaaa1": _with_rules()})

    assert plans[0].summary == "修改參數（a@amr01）\n\noverride(min-below-max): 現場測試低速模式"


def test_promotion_without_overrides_keeps_a_plain_summary():
    stage = save_draft(Stage(), "aaaaaaa1", "lo: 1\nhi: 5\n", "yaml", _with_rules())

    plans = promote(stage, _config_list(_entry("aaaaaaa1", "a")), {"aaaaaaa1": _with_rules()})

    assert plans[0].summary == "修改參數（a@amr01）"


def test_a_rule_added_after_the_draft_was_saved_blocks_promotion_until_a_reason_is_given():
    # 進版時重驗：草稿存下時還沒有這條規則、所以沒有理由——整批不進版，指名那份與那條規則。
    stage = save_draft(Stage(), "aaaaaaa1", _VIOLATING, "yaml")

    with pytest.raises(PromoteInvalid) as exc:
        promote(stage, _config_list(_entry("aaaaaaa1", "a")), {"aaaaaaa1": _with_rules()})

    assert (exc.value.uid, exc.value.problems[0].rule) == ("aaaaaaa1", "min-below-max")
    assert "理由" in str(exc.value)
