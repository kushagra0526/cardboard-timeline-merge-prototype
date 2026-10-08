from tlmerge.lint import lint
from tlmerge.merge import diff, lost_edits, merge, naive_merge
from tlmerge.model import Word, apply, from_rendered, render, subtract
from tlmerge.synth import scenario


def test_property_semantic_merge_matches_oracle_and_conflicts():
    for s in range(60):
        x = scenario(s)
        m, _, conf = merge(x["base"], x["A"], x["B"], x["words"])
        assert render(m, x["words"]) == render(x["expected"], x["words"]), s
        assert sorted((c["kind"], c["id"]) for c in conf) == x["expected_conflicts"], s
        assert lint(render(m, x["words"]), x["words"]) == [], s


def test_ripple_delete_does_not_shift_other_branch():
    x = scenario(48)
    m, _, _ = merge(x["base"], x["A"], x["B"], x["words"])
    r = render(m, x["words"])
    # every caption still sits exactly on its words after both branches' cuts
    assert not [e for e in lint(r, x["words"]) if e[0] == "CAPTION_DRIFT"]


def test_diff_recovers_intents():
    x = scenario(7)
    oa, _ = x["intents"]
    got = diff(x["base"], x["A"])
    assert {o["op"] for o in got} >= {o["op"] for o in oa if o["op"] != "cut"} or not oa


def test_git_baseline_loses_edits_even_without_conflict():
    free = [s for s in range(60) if not scenario(s)["expected_conflicts"]]
    wrong = 0
    for s in free:
        x = scenario(s)
        g, _ = naive_merge(*(render(y, x["words"]) for y in (x["base"], x["A"], x["B"])))
        lost, extra = lost_edits(x["base"], x["expected"], from_rendered(g))
        wrong += bool(lost or extra)
    assert free and wrong / len(free) > 0.8


def test_subtract_and_apply_cut_drops_fully_cut_caption():
    words = {0: Word(0, "hello", 0.0, 0.4), 1: Word(1, "world", 0.5, 0.9)}
    tl = {"segments": [[0.0, 1.0]], "inserts": {}, "captions": {"c1": {"words": [1], "text": "world", "style": "Clean"}}, "effects": []}
    assert subtract([[0, 10]], 2, 3) == [[0, 2], [3, 10]]
    assert apply(tl, [{"op": "cut", "a": 0.45, "b": 0.95}], words)["captions"] == {}


def test_lint_catches_mid_word_cut_flash_frame_and_drift():
    words = {0: Word(0, "launch", 1.0, 1.6), 1: Word(1, "today", 0.2, 0.6)}
    bad = {"items": [{"kind": "clip", "src": [0.0, 1.3], "t": 0.0, "dur": 1.3},
                     {"kind": "clip", "src": [2.0, 2.03], "t": 1.3, "dur": 0.03}],
           "captions": [{"kind": "caption", "id": "c1", "words": [1], "text": "today", "style": "Clean", "t": 0.9, "dur": 0.4}]}
    codes = {c for c, _ in lint(bad, words)}
    assert {"CUT_MID_WORD", "FLASH_FRAME", "CAPTION_DRIFT"} <= codes
