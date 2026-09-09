"""T4 — 白名單判定。core/whitelist 的單元規格。

白名單管的是「哪些目錄可以納管」這個**政策**問題（#11）。`decide(rules, path)` 是純
函式：規則由呼叫端傳進來，比對只在正規化後的字面上進行——不讀檔案系統、不解析符號
連結。符號連結逃逸在寫出時點以 realpath 判定，那是 T8 的事（#11 於 2026-09-04 修訂）。

核心層測試在無檔案系統、無 git、無網路下執行（CLAUDE.md）：規則與路徑都是字串。
"""

from config_manager.core.whitelist import decide

_RULES = ("/opt/robot/config",)


def test_path_inside_the_whitelist_is_allowed():
    assert decide(_RULES, "/opt/robot/config/params.yaml").allowed


def test_path_equal_to_a_rule_is_allowed():
    assert decide(_RULES, "/opt/robot/config").allowed


def test_path_outside_the_whitelist_is_denied():
    assert not decide(_RULES, "/etc/docker/daemon.json").allowed


def test_denial_reason_names_what_the_whitelist_covers():
    # 「未涵蓋」要說得出目前涵蓋的是什麼，否則使用者不知道自己差在哪（#11）。
    denied = decide(_RULES, "/etc/docker/daemon.json")
    assert "/opt/robot/config" in denied.reason


def test_denial_reason_is_actionable():
    # 拒絕原因須可行動——含加入白名單的入口（#11）。
    denied = decide(_RULES, "/etc/docker/daemon.json")
    assert "下一步" in denied.reason


def test_dotdot_escaping_the_whitelist_is_denied():
    # 正規化後比對：這條路徑實際指向 /etc/passwd。
    assert not decide(_RULES, "/opt/robot/config/../../../etc/passwd").allowed


def test_dotdot_staying_inside_the_whitelist_is_allowed():
    # 有 .. 不等於逃逸——正規化後仍在白名單內就允許。
    assert decide(_RULES, "/opt/robot/config/sub/../params.yaml").allowed


def test_relative_path_is_denied():
    assert not decide(_RULES, "params.yaml").allowed


def test_relative_path_denial_says_an_absolute_path_is_required():
    denied = decide(_RULES, "params.yaml")
    assert "絕對路徑" in denied.reason


def test_sibling_directory_sharing_a_prefix_is_denied():
    # /opt/robotics 不在 /opt/robot 底下——比對要看路徑分界，不是字串開頭。
    assert not decide(("/opt/robot",), "/opt/robotics/params.yaml").allowed


def test_any_one_of_several_rules_may_allow():
    assert decide(("/etc", "/opt/robot/config"), "/etc/docker/daemon.json").allowed


def test_empty_whitelist_denies_everything():
    # 沒有規則就是什麼都不涵蓋，不是什麼都放行。
    assert not decide((), "/opt/robot/config/params.yaml").allowed


def test_allowed_decision_carries_no_reason():
    assert decide(_RULES, "/opt/robot/config/params.yaml").reason is None
