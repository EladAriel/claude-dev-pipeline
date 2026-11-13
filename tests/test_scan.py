from tests.conftest import repo_root

from claude_dev_pipeline.scan import load_allowlist, scan_tree


def test_private_path_is_flagged_in_a_temp_tree(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("see " + "/home" + "/ada\n", encoding="utf-8")
    found = scan_tree(tmp_path, set())
    assert [item.rule for item in found] == ["private-path"]


def test_repo_scan_is_clean_with_the_allowlist():
    root = repo_root()
    assert scan_tree(root, load_allowlist(root / ".pathscan-allow")) == []
