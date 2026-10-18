from app.diff.functions import map_changed_functions
from app.diff.parser import parse_diff
from app.integrations.github_client import parse_pr_url
from tests.samples import DB_PY, DIFF
import pytest


def test_parse_pr_url():
    ref = parse_pr_url("https://github.com/psf/requests/pull/123/files")
    assert (ref.owner, ref.repo, ref.number) == ("psf", "requests", 123)
    assert ref.full_name == "psf/requests"


@pytest.mark.parametrize("bad", ["https://github.com/psf/requests", "https://gitlab.com/a/b/pull/1", "nope"])
def test_parse_pr_url_rejects(bad):
    with pytest.raises(ValueError):
        parse_pr_url(bad)


def test_parse_diff_files_and_status():
    files = {f.path: f for f in parse_diff(DIFF)}
    assert set(files) == {"app/db.py", "README.md", "old.py"}
    assert files["app/db.py"].status == "modified" and files["app/db.py"].reviewable
    assert files["README.md"].status == "added" and not files["README.md"].reviewable
    assert files["old.py"].status == "removed" and not files["old.py"].reviewable


def test_parse_diff_line_numbers():
    f = {f.path: f for f in parse_diff(DIFF)}["app/db.py"]
    assert (f.additions, f.deletions) == (2, 2)
    h = f.hunks[0]
    assert [a["line"] for a in h["added"]] == [5, 6]
    assert [r["line"] for r in h["removed"]] == [5, 6]
    assert f.touch_lines == [5, 6]


def test_map_changed_functions():
    funcs, note = map_changed_functions(DB_PY, [5, 6])
    assert note is None and len(funcs) == 1
    assert funcs[0]["qualname"] == "get_user"
    assert (funcs[0]["start_line"], funcs[0]["end_line"]) == (4, 6)
    assert "def get_user" in funcs[0]["source"]


def test_map_method_and_module_level():
    funcs, _ = map_changed_functions(DB_PY, [1, 11])
    names = {f["qualname"]: f for f in funcs}
    assert set(names) == {"<module>", "Repo.find"}
    assert names["<module>"]["source"] is None


def test_map_syntax_error():
    funcs, note = map_changed_functions("def broken(:\n", [1])
    assert funcs == [] and "syntax error" in note
