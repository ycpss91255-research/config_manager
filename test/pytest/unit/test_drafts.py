"""core/drafts — T18 草稿（#18）與進版 promote（#19）。

草稿存在**編輯階段**、不在來源也不在目標（CONTEXT.md）。核心層純邏輯：階段是不可變的資料
結構，每個操作回**新的**階段；儲存不產生變更紀錄、不寫到目標位置——這裡根本沒有 I/O 可做，
那正是結構上的保證。內容是改好的文字＋format（第 1 層驗證要靠它）。

save_draft 擋第 1 層（人正在編輯，壞內容不該存）；adopt_draft 不擋、只回警告（偏離處置的
「先納入、待修正」要把壞內容撈進來修）。草稿與四種狀態正交：這裡不看目標／來源是否一致。
"""

import pytest

from config_manager.core.drafts import Stage, adopt_draft, discard, promote, save_draft
from config_manager.core.errors import DraftInvalid, DraftNotFound, PromoteInvalid
from config_manager.core.models import ConfigList, Defaults, FileEntry, Permissions

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
