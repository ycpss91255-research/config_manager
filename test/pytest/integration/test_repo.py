"""io/repo — 把來源位元組寫進 config repo，並把清單檔寫回磁碟（#186）。

納管缺的 io 能力。效果透過既有介面觀察：逐位元組相同用 `io/digest`（T20）、清單檔
可讀用 `core/config_list.load`（T1）——所以規格掛在既有介面的觀察上，不是新介面。

整合層：這裡真的碰檔案系統。
"""

from config_manager.core.config_list import load
from config_manager.io.digest import digest
from config_manager.io.repo import place_source, write_config_list

_LIST = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"
"""


def test_placed_source_is_byte_identical_to_the_content(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    content = b"name: amr01\n\x00\xff not utf-8\n"  # 連非 utf-8 的位元組都要原樣

    rel = place_source(str(repo), "amr01", "/opt/robot/config/nav2_params.yaml", content)

    placed = repo / rel
    assert digest(str(placed)) == digest_of(content, tmp_path)


def digest_of(content, tmp_path):
    reference = tmp_path / "reference.bin"
    reference.write_bytes(content)
    return digest(str(reference))


def test_source_path_encodes_the_target_under_the_hostname(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    rel = place_source(str(repo), "amr01", "/etc/docker/daemon.json", b"{}\n")

    # 目標路徑的 / 編碼成 __（去開頭的 /）；第一層是 hostname（D3）。與 §4.3 範例一致。
    assert rel == "files/amr01/etc__docker__daemon.json"


def test_the_returned_path_is_what_the_config_list_entry_uses(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    rel = place_source(str(repo), "amr01", "/opt/robot/params.yaml", b"a: 1\n")

    # 回傳的是 repo 內的相對路徑，直接填進 FileEntry.source；它真的在那個位置。
    assert (repo / rel).is_file()
    assert rel.startswith("files/")


def test_intermediate_directories_are_created(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    # files/ 與 files/amr01/ 都還不存在——entrypoint 只種下 config-list.toml。

    place_source(str(repo), "amr01", "/opt/robot/params.yaml", b"a: 1\n")

    assert (repo / "files" / "amr01").is_dir()


def test_two_sources_from_the_same_host_coexist(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    first = place_source(str(repo), "amr01", "/opt/a.yaml", b"a\n")
    second = place_source(str(repo), "amr01", "/etc/b.json", b"b\n")

    assert first != second
    assert (repo / first).read_bytes() == b"a\n"
    assert (repo / second).read_bytes() == b"b\n"


def test_targets_that_flatten_alike_get_distinct_paths(tmp_path):
    # #192：/etc/a/b 與 /etc/a__b 都把中段變成 `a__b`。編碼若只做 /→__、不跳脫路徑本身
    # 的 _，兩者會編成同一檔名，第二次靜默覆蓋第一次的複本。編碼必須單射。
    repo = tmp_path / "repo"
    repo.mkdir()

    nested = place_source(str(repo), "amr01", "/etc/a/b", b"nested\n")
    flat = place_source(str(repo), "amr01", "/etc/a__b", b"flat\n")

    assert nested != flat
    assert (repo / nested).read_bytes() == b"nested\n"
    assert (repo / flat).read_bytes() == b"flat\n"


def test_written_config_list_is_readable(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    write_config_list(str(repo), _LIST)

    written = (repo / "config-list.toml").read_text(encoding="utf-8")
    assert written == _LIST
    load(written)  # 解析得過（T1）


def test_written_config_list_replaces_the_previous_one(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config-list.toml").write_text("list_version = 1\n", encoding="utf-8")

    write_config_list(str(repo), _LIST)

    assert (repo / "config-list.toml").read_text(encoding="utf-8") == _LIST


def test_placing_a_source_does_not_leave_a_temporary_behind(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    place_source(str(repo), "amr01", "/opt/robot/params.yaml", b"a: 1\n")

    written = repo / "files" / "amr01"
    stray = [p for p in written.iterdir() if p.name.startswith(".config_manager-")]
    assert stray == []
