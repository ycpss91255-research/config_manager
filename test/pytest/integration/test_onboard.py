"""io/onboard — 把一份磁碟上的 config 納入管理（#12）.

編排：把 read_source（T22）、identity（T5）、config_list（T1）、repo（#186）、
git（T7）串起來。它不算出新的值，效果全都透過既有介面觀察——逐位元組相同用
`io/digest`（T20）、清單檔條目用 `core/config_list.load`（T1）、commit 用
`io/git.history`（T7）——所以不是新測試介面，跟 `io/repo` 同一個處理方式。

各介面各自全綠擋不住的是**接線**（來源目標接反、name 取錯路徑、沒 commit）。這裡的
每一條就是在擋那個。

整合層：這裡真的碰檔案系統與 git。
"""

import os
import re
import subprocess

from config_manager.core.config_list import load
from config_manager.core.errors import DuplicateTarget, DuplicateUid
from config_manager.io.digest import digest
from config_manager.io.errors import OnboardLeftBehind, SourceOutsideRoots
from config_manager.io.git import history
from config_manager.io.onboard import OnboardRequest, onboard
from config_manager.io.preflight import CONFIG_LIST_NAME
from config_manager.io.repo import source_relpath, write_config_list as real_write_config_list
from config_manager.io.source import local_hostname

import pytest

_AUTHOR = "陳小明 <ming@example.com>"

_SEED = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"
"""


def _repo(tmp_path):
    """一個像 entrypoint 種出來的 config repo：只有清單檔，沒有 files/。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / CONFIG_LIST_NAME).write_text(_SEED, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=seed", "-c", "user.email=s@e.x",
         "commit", "-q", "-m", "chore: 種下清單檔"],
        check=True,
    )
    return repo


def _source(tmp_path, name="nav2_params.yaml", content=b"max_vel: 0.8\n"):
    """一份要被納管的來源檔，放在白名單內。"""
    root = tmp_path / "managed"
    root.mkdir(exist_ok=True)
    path = root / name
    path.write_bytes(content)
    return root, path


def _request(root, path, fmt="yaml", note=""):
    return OnboardRequest(
        source_path=str(path), fmt=fmt, allowed_roots=(str(root),), ambiguity_note=note
    )


def _fixed_uids(monkeypatch, *values):
    """讓 onboard 依序取用這些 uid，而不看時鐘。

    毫秒時間戳的 uid 在兩次快速呼叫下可能真的撞號，所以要測「target 重複」與
    「uid 重複」得先把它們彼此隔開：想試哪一條，就給哪一條會撞的 uid。
    """
    supply = iter(values)
    monkeypatch.setattr("config_manager.io.onboard.new_uid", lambda _now: next(supply))


def _git_state(repo):
    """(HEAD sha, 工作區相對 HEAD 的變動)。攔截後這一對不變，才算 repo 完全沒動。

    兩者缺一不可：porcelain 看得到殘留檔與清單檔的改動，但它是**相對 HEAD** 算的，
    多出來的 commit 會讓 HEAD 一起前移、於是 porcelain 反而空的——而「寫進去**並提交**」
    正是 #172 的害處。補上 HEAD sha，才擋得住「驗證過了卻仍偷偷提交一筆」。
    """
    def _git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, check=True,
        ).stdout

    return _git("rev-parse", "HEAD").strip(), _git("status", "--porcelain")


def test_onboarded_source_is_byte_identical_in_the_repo(tmp_path):
    repo = _repo(tmp_path)
    root, path = _source(tmp_path, content=b"max_vel: 0.8\n\xff not utf-8\n")

    entry = onboard(str(repo), _request(root, path), _AUTHOR)

    assert digest(str(repo / entry.source)) == digest(str(path))


def test_the_entry_records_the_target_as_the_original_path(tmp_path):
    # 接反的守門：target 是磁碟上原本的位置，source 是 repo 內的複本，兩者不能對調。
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)

    entry = onboard(str(repo), _request(root, path), _AUTHOR)

    assert entry.target == str(path)
    assert entry.source == f"files/{entry.hostname}/{str(path).lstrip('/').replace('/', '__')}"


def test_the_new_entry_is_in_the_config_list(tmp_path):
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)

    entry = onboard(str(repo), _request(root, path), _AUTHOR)

    listed = load((repo / CONFIG_LIST_NAME).read_text(encoding="utf-8"))
    assert [e.uid for e in listed.files] == [entry.uid]


def test_name_is_derived_from_the_target_path(tmp_path):
    repo = _repo(tmp_path)
    root, path = _source(tmp_path, name="nav2_params.yaml")

    entry = onboard(str(repo), _request(root, path), _AUTHOR)

    # 最後兩層、去副檔名、底線轉連字號（T5）：managed/nav2_params.yaml → managed-nav2-params
    assert entry.name == "managed-nav2-params"


def test_uid_is_an_eight_char_base36(tmp_path):
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)

    entry = onboard(str(repo), _request(root, path), _AUTHOR)

    assert re.fullmatch(r"[0-9a-z]{8}", entry.uid)


def test_permissions_record_the_original_mode(tmp_path):
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)
    os.chmod(path, 0o640)

    entry = onboard(str(repo), _request(root, path), _AUTHOR)

    assert entry.permissions.mode == "0640"


def test_a_commit_is_recorded_with_the_import_kind(tmp_path):
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)

    entry = onboard(str(repo), _request(root, path), _AUTHOR)

    changes = history(str(repo), entry.uid)
    assert len(changes) == 1
    assert changes[0].kind == "import"
    assert changes[0].summary == f"{entry.name}@{entry.hostname}"


def test_the_ambiguity_note_goes_into_the_commit_body(tmp_path):
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)
    note = "歧義確認：\n- 第 1 行「no」判定為字串"

    entry = onboard(str(repo), _request(root, path, note=note), _AUTHOR)

    assert history(str(repo), entry.uid)[0].body == note


def test_no_ambiguity_note_means_an_empty_commit_body(tmp_path):
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)

    entry = onboard(str(repo), _request(root, path), _AUTHOR)

    assert history(str(repo), entry.uid)[0].body == ""


def test_the_original_source_file_is_left_untouched(tmp_path):
    repo = _repo(tmp_path)
    root, path = _source(tmp_path, content=b"max_vel: 0.8\n")

    onboard(str(repo), _request(root, path), _AUTHOR)

    assert path.read_bytes() == b"max_vel: 0.8\n"


def test_a_source_outside_the_whitelist_is_refused(tmp_path):
    repo = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.yaml"
    secret.write_bytes(b"stolen\n")
    # 白名單只涵蓋 managed/，來源在 outside/。
    root = tmp_path / "managed"
    root.mkdir()

    request = OnboardRequest(source_path=str(secret), fmt="yaml", allowed_roots=(str(root),))
    with pytest.raises(SourceOutsideRoots):
        onboard(str(repo), request, _AUTHOR)


def test_refusal_leaves_the_repo_untouched(tmp_path):
    repo = _repo(tmp_path)
    before = (repo / CONFIG_LIST_NAME).read_text(encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.yaml"
    secret.write_bytes(b"stolen\n")
    root = tmp_path / "managed"
    root.mkdir()

    request = OnboardRequest(source_path=str(secret), fmt="yaml", allowed_roots=(str(root),))
    with pytest.raises(SourceOutsideRoots):
        onboard(str(repo), request, _AUTHOR)

    assert (repo / CONFIG_LIST_NAME).read_text(encoding="utf-8") == before
    assert not (repo / "files").exists()


def test_onboarding_the_same_target_twice_is_refused(tmp_path, monkeypatch):
    # AC1／AC4：第二次納管同一路徑這條路。兩個不同 uid，孤立出 target 這一項。
    _fixed_uids(monkeypatch, "aaaaaaaa", "bbbbbbbb")
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)
    onboard(str(repo), _request(root, path), _AUTHOR)
    before = _git_state(repo)

    with pytest.raises(DuplicateTarget) as caught:
        onboard(str(repo), _request(root, path), _AUTHOR)

    # AC1：訊息指名既有那一筆——ref 帶著 uid，訊息也帶著共用的 target。
    assert "aaaaaaaa" in str(caught.value)
    assert str(path) in str(caught.value)
    # AC2：DuplicateTarget 這條路上，攔截時 repo 也完全沒動（沒有殘留、沒有偷偷提交）。
    assert _git_state(repo) == before


def test_onboarding_does_not_sweep_unrelated_untracked_files(tmp_path):
    # #173 AC4：repo 裡本來就有的、與這次納管無關的未追蹤檔（前一次失敗的殘留、或操作者
    # 的暫存檔），不該被 import commit 收編。舊的 git add -A 會把它掃進來。
    repo = _repo(tmp_path)
    (repo / "leftover.txt").write_text("unrelated\n", encoding="utf-8")
    root, path = _source(tmp_path)

    onboard(str(repo), _request(root, path), _AUTHOR)

    # 沒被收編 → 納管後它仍是未追蹤的（若被 commit 就會變成已追蹤、不再出現在 ?? 行）。
    assert "?? leftover.txt" in _git_state(repo)[1]
    assert (repo / "leftover.txt").read_text(encoding="utf-8") == "unrelated\n"


def test_a_refused_duplicate_uid_leaves_the_repo_untouched(tmp_path, monkeypatch):
    # AC2／AC3：兩個不同 target 撞同一個 uid。第二筆的來源會被編碼成一個新檔名，
    # 若在完整性檢查之前就寫下去，那就是一個沒人清的殘留檔（#172 的後果鏈）。
    # 攔截必須發生在任何寫入之前。
    _fixed_uids(monkeypatch, "cccccccc", "cccccccc")
    repo = _repo(tmp_path)
    root, first = _source(tmp_path, name="first.yaml")
    _, second = _source(tmp_path, name="second.yaml")
    onboard(str(repo), _request(root, first), _AUTHOR)
    before = _git_state(repo)

    with pytest.raises(DuplicateUid):
        onboard(str(repo), _request(root, second), _AUTHOR)

    assert _git_state(repo) == before


# ── 寫入步驟中途失敗即整批回滾（#173 的 AC1／AC2／AC3）─────────────────────────


def test_a_failed_list_write_rolls_the_repo_back(tmp_path, monkeypatch):
    # 乙-1：來源檔放進去了，寫清單檔失敗。回滾把來源檔清掉，repo 回到納管前。
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)
    before = _git_state(repo)

    seen = []

    def _fail_first_write(*args, **kwargs):
        # 納管當下那一次寫入失敗；回滾要還原清單檔時（第二次）放行。
        seen.append(1)
        if len(seen) == 1:
            raise OSError("寫不進去")
        return real_write_config_list(*args, **kwargs)

    monkeypatch.setattr("config_manager.io.onboard.write_config_list", _fail_first_write)

    with pytest.raises(OSError, match="寫不進去"):
        onboard(str(repo), _request(root, path), _AUTHOR)

    assert _git_state(repo) == before


def test_a_failed_commit_rolls_the_repo_back(tmp_path, monkeypatch):
    # 乙-2：來源檔與清單檔都寫好、也 stage 了，commit 失敗。回滾 unstage、還原清單檔、
    # 清掉來源檔，repo 回到納管前。
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)
    before = _git_state(repo)

    def _commit_boom(*args, **kwargs):
        raise RuntimeError("commit 壞了")

    monkeypatch.setattr("config_manager.io.onboard.record", _commit_boom)

    with pytest.raises(RuntimeError, match="commit 壞了"):
        onboard(str(repo), _request(root, path), _AUTHOR)

    assert _git_state(repo) == before


def test_a_rollback_that_cannot_finish_fails_loudly(tmp_path, monkeypatch):
    # AC2：commit 失敗觸發回滾，而回滾還原清單檔那一步也失敗（磁碟持續寫不進去）。
    # onboard 大聲失敗（OnboardLeftBehind）並指名殘留了什麼，原本的失敗掛在 __cause__。
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)

    seen = []

    def _write_then_fail(*args, **kwargs):
        seen.append(1)
        if len(seen) == 1:
            return real_write_config_list(*args, **kwargs)  # 納管當下寫成功
        raise OSError("還原也寫不進去")  # 回滾要還原清單檔時失敗

    monkeypatch.setattr("config_manager.io.onboard.write_config_list", _write_then_fail)

    def _commit_boom(*args, **kwargs):
        raise RuntimeError("commit 壞了")

    monkeypatch.setattr("config_manager.io.onboard.record", _commit_boom)

    with pytest.raises(OnboardLeftBehind) as caught:
        onboard(str(repo), _request(root, path), _AUTHOR)

    assert CONFIG_LIST_NAME in str(caught.value)
    assert isinstance(caught.value.__cause__, RuntimeError)


def _repo_relpath(repo, path):
    """來源檔在 repo 內會落到的相對路徑，與 onboard 內部算的一致（用 realpath 的目標）。"""
    return source_relpath(local_hostname(), os.path.realpath(str(path)))


def test_a_rollback_restores_a_pre_existing_source_copy(tmp_path, monkeypatch):
    # 來源檔該落的位置本來就有東西（前一次失敗納管的孤兒）。這次納管會覆蓋它，中途失敗時
    # 回滾要還原成原本的位元組、不是刪掉——「回到納管前」對本來就存在的檔也成立（AC1）。
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)
    orphan = repo / _repo_relpath(repo, path)
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"old orphan\n")
    before = _git_state(repo)

    def _commit_boom(*args, **kwargs):
        raise RuntimeError("commit 壞了")

    monkeypatch.setattr("config_manager.io.onboard.record", _commit_boom)

    with pytest.raises(RuntimeError, match="commit 壞了"):
        onboard(str(repo), _request(root, path), _AUTHOR)

    assert orphan.read_bytes() == b"old orphan\n"
    assert _git_state(repo) == before


def test_a_rollback_names_the_index_when_unstage_fails(tmp_path, monkeypatch):
    # AC2：回滾第一步 unstage 就失敗（索引被鎖）。殘留裡指名索引，原本的失敗掛在 __cause__。
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)

    def _commit_boom(*args, **kwargs):
        raise RuntimeError("commit 壞了")

    def _unstage_boom(*args, **kwargs):
        raise OSError("索引鎖住")

    monkeypatch.setattr("config_manager.io.onboard.record", _commit_boom)
    monkeypatch.setattr("config_manager.io.onboard.unstage", _unstage_boom)

    with pytest.raises(OnboardLeftBehind) as caught:
        onboard(str(repo), _request(root, path), _AUTHOR)

    assert "索引" in str(caught.value)
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_a_rollback_names_the_source_when_cleanup_fails(tmp_path, monkeypatch):
    # AC2：回滾清掉來源檔那一步失敗（刪不掉）。殘留指名那個孤兒來源檔的路徑。
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)

    def _commit_boom(*args, **kwargs):
        raise RuntimeError("commit 壞了")

    def _remove_boom(*args, **kwargs):
        raise OSError("刪不掉")

    monkeypatch.setattr("config_manager.io.onboard.record", _commit_boom)
    monkeypatch.setattr("config_manager.io.onboard.os.remove", _remove_boom)

    with pytest.raises(OnboardLeftBehind) as caught:
        onboard(str(repo), _request(root, path), _AUTHOR)

    assert _repo_relpath(repo, path) in str(caught.value)
    assert isinstance(caught.value.__cause__, RuntimeError)
