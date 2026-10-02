"""io/rules — 讀一份 config 的跨欄位規則，並備好跨 config 對照要用的值（#41）。

編排層：效果透過 T3 的 `check`（給它讀出來的規則）觀察——規則檔寫了什麼、內容違反時就回什麼
警告。這裡擋的是接線：沒有規則檔就是沒有規則；跨 config 對照真的去讀了另一份的值；規則檔
壞了、對照的那一份讀不到，不丟例外、也不默默略過，而是變成一個警告。

整合層：真的碰檔案系統與 git。
"""

import subprocess

from config_manager.core.rules import FILE_RULE
from config_manager.core.validate import check
from config_manager.io.onboard import OnboardRequest, onboard
from config_manager.io.preflight import CONFIG_LIST_NAME
from config_manager.io.rules import read_rules

_AUTHOR = "陳小明 <ming@example.com>"
_SEED = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"
"""


def _repo(tmp_path):
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


def _managed(tmp_path, repo, name, content, fmt="yaml"):
    root = tmp_path / "managed"
    root.mkdir(exist_ok=True)
    path = root / name
    path.write_bytes(content)
    request = OnboardRequest(source_path=str(path), fmt=fmt, allowed_roots=(str(root),))
    return onboard(str(repo), request, _AUTHOR)


def _write_rules(repo, uid, text):
    (repo / ".rules").mkdir(exist_ok=True)
    (repo / ".rules" / f"{uid}.toml").write_text(text, encoding="utf-8")


def _warned(repo, uid, text):
    return [(p.rule, p.path) for p in check(text, "yaml", rules=read_rules(str(repo), uid))]


def test_a_config_without_a_rules_file_has_no_rules(tmp_path):
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo, "nav.yaml", b"lo: 1\nhi: 5\n")

    assert read_rules(str(repo), entry.uid) is None


def test_the_rules_written_in_the_file_are_the_ones_applied(tmp_path):
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo, "nav.yaml", b"lo: 1\nhi: 5\n")
    _write_rules(
        repo, entry.uid, '[[rules]]\nid = "lo-below-hi"\nleft = "lo"\nop = "<"\nright = "hi"\n'
    )

    assert _warned(repo, entry.uid, "lo: 1\nhi: 5\n") == []
    assert _warned(repo, entry.uid, "lo: 9\nhi: 5\n") == [("lo-below-hi", "lo")]


def test_a_reference_reads_the_values_from_the_other_config(tmp_path):
    # 外部一致性＝跨 config 對照：frame 名稱必須是另一份 config 定義過的。清單與「清單裡每個
    # 物件的某個欄位」兩種寫法都取得到。
    repo = _repo(tmp_path)
    robot = _managed(
        tmp_path, repo, "robot.yaml",
        b"frames: [base_link, odom]\nlinks:\n  - name: lidar\n  - name: camera\n",
    )
    nav = _managed(tmp_path, repo, "nav.yaml", b"robot_frame: odom\nsensor: lidar\n")
    _write_rules(repo, nav.uid, f"""\
[[rules]]
id = "frame-is-defined"
left = "robot_frame"
in = {{ config = "{robot.uid}", field = "frames" }}

[[rules]]
id = "sensor-is-a-link"
left = "sensor"
in = {{ config = "{robot.uid}", field = "links[].name" }}
""")

    assert _warned(repo, nav.uid, "robot_frame: odom\nsensor: lidar\n") == []
    assert _warned(repo, nav.uid, "robot_frame: base_lnk\nsensor: radar\n") == [
        ("frame-is-defined", "robot_frame"), ("sensor-is-a-link", "sensor"),
    ]


def test_a_malformed_rules_file_becomes_a_warning_naming_the_file_not_an_exception(tmp_path):
    # 第 3 層不硬擋，規則檔壞了不該讓存檔整個失敗；但也不默默當成沒有規則。
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo, "nav.yaml", b"lo: 1\n")
    _write_rules(repo, entry.uid, '[[rules]]\nid = "r"\nleft = "lo"\nop = "<<"\nvalue = 1\n')

    problems = check("lo: 1\n", "yaml", rules=read_rules(str(repo), entry.uid))

    assert [p.rule for p in problems] == [FILE_RULE]
    assert f".rules/{entry.uid}.toml" in problems[0].message and "<<" in problems[0].message


def test_a_reference_that_cannot_be_resolved_becomes_a_warning_and_the_rest_still_runs(tmp_path):
    repo = _repo(tmp_path)
    other = _managed(tmp_path, repo, "robot.yaml", b"frames: [odom]\n")
    entry = _managed(tmp_path, repo, "nav.yaml", b"frame: odom\nlo: 9\nhi: 5\n")
    _write_rules(repo, entry.uid, f"""\
[[rules]]
id = "unknown-config"
left = "frame"
in = {{ config = "zzzzzzz9", field = "frames" }}

[[rules]]
id = "missing-field"
left = "frame"
in = {{ config = "{other.uid}", field = "nope" }}

[[rules]]
id = "lo-below-hi"
left = "lo"
op = "<"
right = "hi"
""")

    problems = check("frame: odom\nlo: 9\nhi: 5\n", "yaml", rules=read_rules(str(repo), entry.uid))

    assert sorted(str(p.rule) for p in problems) == ["lo-below-hi", FILE_RULE, FILE_RULE]
    said = " ".join(p.message for p in problems)
    assert "zzzzzzz9" in said and "nope" in said
