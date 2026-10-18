from app.diff.functions import map_changed_functions
from app.diff.parser import parse_diff
from app.schemas.llm_output import RawFinding
from app.services.review_input import ReviewFile, render_input
from app.services.validation import clean_quote, validate_findings
from tests.samples import DB_PY, DIFF

QUOTE = "query = \"SELECT * FROM users WHERE name = '\" + name + \"'\""   # added line 5 in the sample PR


def sample_files():
    out = []
    for pf in parse_diff(DIFF):
        funcs = []
        if pf.reviewable and pf.touch_lines:
            funcs, _ = map_changed_functions(DB_PY, pf.touch_lines)
        out.append(ReviewFile(pf.path, pf.status, pf.language, pf.reviewable, pf.additions,
                              pf.deletions, pf.hunks, funcs))
    return out


def raw(**over):
    base = dict(agent="security", category="sql_injection", severity="Critical", file="app/db.py",
                line_start=5, line_end=6, evidence_quote=QUOTE, summary="SQL built by concatenation",
                confidence=0.9)
    base.update(over)
    return RawFinding(**base)


SHOWN = {"app/db.py": {5: QUOTE, 6: "return db.execute(query)"}}


# ---------- renderer ----------
def test_render_shows_exact_line_numbers_and_context():
    r = render_input("Fix user lookup", sample_files(), 60_000)
    assert "<pr_diff>" in r.text and r.text.rstrip().endswith("</pr_diff>")
    assert "+L5" in r.text and "+L6" in r.text and "def get_user(name):" in r.text
    assert "- README.md" in r.text and "[not reviewed" in r.text      # non-Python is listed, not reviewed
    assert set(r.shown) == {"app/db.py"} and r.shown["app/db.py"][5].strip().startswith("query")


def test_render_skips_files_that_do_not_fit():
    r = render_input("t", sample_files(), 300)
    assert r.skipped_files == ["app/db.py"] and r.shown == {}


def test_render_without_python_changes_has_nothing_to_review():
    files = [f for f in sample_files() if f.path != "app/db.py"]
    assert render_input(None, files, 60_000).shown == {}


# ---------- validator ----------
def test_valid_finding_is_kept_with_evidence():
    res = validate_findings([raw()], SHOWN)
    f = res.kept[0]
    assert (f.line_start, f.line_end, f.file) == (5, 6, "app/db.py")
    assert f.evidence[0].type == "llm" and "L5" in f.evidence[0].detail and not res.dropped


def test_off_by_few_lines_is_relocated_to_the_real_line():
    res = validate_findings([raw(line_start=9, line_end=9)], SHOWN)
    assert res.kept[0].line_start == 5 and res.relocated == 1


def test_far_away_line_is_dropped():
    res = validate_findings([raw(line_start=200, line_end=200)], SHOWN)
    assert not res.kept and res.dropped["lines_do_not_match_quote"] == 1


def test_invented_quote_is_dropped_as_hallucination():
    res = validate_findings([raw(evidence_quote="os.system(user_input)")], SHOWN)
    assert not res.kept and res.dropped["quote_not_in_added_lines"] == 1


def test_unknown_file_is_dropped():
    res = validate_findings([raw(file="app/other.py")], SHOWN)
    assert not res.kept and res.dropped["file_not_in_diff"] == 1


def test_path_variants_are_resolved():
    assert validate_findings([raw(file="b/app/db.py")], SHOWN).kept
    assert validate_findings([raw(file="db.py")], SHOWN).kept          # unique suffix match


def test_quote_with_diff_markers_still_matches():
    assert clean_quote("+L5   | return db.execute(query)") == "return db.execute(query)"
    assert validate_findings([raw(evidence_quote="+L6   | return db.execute(query)",
                                  line_start=6, line_end=6)], SHOWN).kept


def test_whitespace_differences_in_quote_are_ignored():
    q = "return   db.execute( query )".replace("( query )", "(query)")
    assert validate_findings([raw(evidence_quote=q, line_start=6, line_end=6)], SHOWN).kept
