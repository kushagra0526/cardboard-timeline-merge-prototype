"""Semantic diff and 3-way merge of timelines in source time, plus a git-style baseline for comparison."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from .model import EPS, Word, apply, kept

# ------------------------------------------------------------------ semantic diff


def _coverage_removed(base_segs, br_segs) -> list[tuple[float, float]]:
    """Source ranges kept in base but not in branch."""
    pts = sorted({x for s in base_segs + br_segs for x in s})
    out = []
    for a, b in zip(pts, pts[1:]):
        m = (a + b) / 2
        if kept(base_segs, m) and not kept(br_segs, m):
            if out and abs(out[-1][1] - a) < EPS:
                out[-1] = (out[-1][0], b)
            else:
                out.append((a, b))
    return out


def diff(base: dict, br: dict) -> list[dict]:
    ops = [{"op": "cut", "a": a, "b": b} for a, b in _coverage_removed(base["segments"], br["segments"])]
    for i, x in br["inserts"].items():
        if i not in base["inserts"]:
            ops.append({"op": "insert", "id": i, **x})
    for i in base["inserts"]:
        if i not in br["inserts"] and kept(br["segments"], base["inserts"][i]["anchor"]):
            ops.append({"op": "delete_insert", "id": i})
    for cid, c in br["captions"].items():
        b0 = base["captions"].get(cid)
        if b0 is None:
            ops.append({"op": "caption_add", "id": cid, **c})
            continue
        if c["text"] != b0["text"]:
            ops.append({"op": "caption_text", "id": cid, "text": c["text"], "words": b0["words"]})
        if c["style"] != b0["style"]:
            ops.append({"op": "caption_style", "id": cid, "style": c["style"], "words": b0["words"]})
    for e in br["effects"]:
        if e not in base["effects"]:
            ops.append({"op": "effect", **e})
    for e in base["effects"]:
        if e not in br["effects"]:
            ops.append({"op": "effect_remove", **e})
    return ops


def describe(op: dict, words: dict[int, Word]) -> str:
    f = lambda t: f"{int(t // 60)}:{t % 60:04.1f}"
    k = op["op"]
    if k == "cut":
        inside = [w.text for w in words.values() if op["a"] - EPS <= w.start and w.end <= op["b"] + EPS]
        what = f"“{' '.join(inside[:6])}{' …' if len(inside) > 6 else ''}”" if inside else "silence"
        return f"cut {f(op['a'])}–{f(op['b'])} ({what})"
    if k == "insert":
        return f"insert b-roll '{op['asset']}' ({op['dur']}s) at {f(op['anchor'])}"
    if k in ("caption_text", "caption_style", "caption_add", "caption_delete"):
        return f"{k.replace('_', ' ')} {op['id']}" + (f" → “{op['text']}”" if "text" in op and k != "caption_style" else "") + \
               (f" → {op['style']}" if k == "caption_style" else "")
    if k == "effect":
        return f"effect '{op['name']}' on {f(op['a'])}–{f(op['b'])}"
    return f"{k} {op.get('id', '')}"


# ------------------------------------------------------------------ 3-way merge


def _in_cuts(t: float, cuts) -> bool:
    return any(a + EPS < t < b - EPS for a, b in cuts)


def _words_cut(wids, cuts, words) -> list[int]:
    return [w for w in wids if any(a - EPS <= words[w].start and words[w].end <= b + EPS for a, b in cuts)]


def merge(base: dict, a: dict, b: dict, words: dict[int, Word]):
    """→ (merged timeline, applied ops, conflicts). Conflicting ops are held back for the user to pick a side."""
    oa, ob = diff(base, a), diff(base, b)
    cuts = {"A": [(o["a"], o["b"]) for o in oa if o["op"] == "cut"], "B": [(o["a"], o["b"]) for o in ob if o["op"] == "cut"]}
    held, conflicts = set(), []

    def other(side):
        return "B" if side == "A" else "A"

    for side, ops in (("A", oa), ("B", ob)):
        for i, o in enumerate(ops):
            if o["op"] in ("caption_text", "caption_style", "caption_add"):
                gone = _words_cut(o["words"], cuts[other(side)], words)
                if o["op"] == "caption_style" and len(gone) == len(o["words"]):
                    continue  # restyling a caption the other side deleted outright: deletion wins, nothing is lost
                if o["op"] == "caption_style":
                    gone = []
                if gone:
                    held.add((side, i))
                    conflicts.append({"kind": "caption edited on cut words", "side": side, "id": o["id"],
                                      "detail": f"{side} edits caption {o['id']} but {other(side)} cut “{' '.join(words[w].text for w in gone)}”"})
            if o["op"] == "insert" and _in_cuts(o["anchor"], cuts[other(side)]):
                held.add((side, i))
                conflicts.append({"kind": "insert anchored in cut", "side": side, "id": o["id"],
                                  "detail": f"{side} placed b-roll '{o['asset']}' at {o['anchor']:.2f}s, inside a range {other(side)} cut"})
    for i, x in enumerate(oa):
        for j, y in enumerate(ob):
            if x["op"] == y["op"] and x["op"] in ("caption_text", "caption_style") and x["id"] == y["id"]:
                key = "text" if x["op"] == "caption_text" else "style"
                if x[key] != y[key]:
                    held |= {("A", i), ("B", j)}
                    conflicts.append({"kind": f"both changed caption {key}", "side": "A+B", "id": x["id"],
                                      "detail": f"caption {x['id']} {key}: A “{x[key]}” vs B “{y[key]}”"})
    applied = [o for i, o in enumerate(oa) if ("A", i) not in held] + [o for j, o in enumerate(ob) if ("B", j) not in held]
    return apply(base, applied, words), applied, conflicts


# ------------------------------------------------------------------ git-style baseline: diff3 over the exported timeline


def _lines(rendered: dict) -> list[str]:
    return [json.dumps(x, sort_keys=True) for x in rendered["items"] + rendered["captions"]]


def naive_merge(base_r: dict, a_r: dict, b_r: dict) -> tuple[dict, int]:
    """Real git: `git merge-file` over the exported timeline (one JSON line per clip/caption). Conflicts take side A."""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, r in (("a", a_r), ("base", base_r), ("b", b_r)):
            p = Path(d) / name
            p.write_text("\n".join(_lines(r)) + "\n", encoding="utf-8")
            paths.append(str(p))
        n = subprocess.run(["git", "merge-file", "-p", *paths], capture_output=True).returncode
        merged = subprocess.run(["git", "merge-file", "-p", "--ours", *paths], capture_output=True, text=True).stdout
    objs = [json.loads(l) for l in merged.splitlines() if l.strip()]
    return {"items": [o for o in objs if o["kind"] != "caption"], "captions": [o for o in objs if o["kind"] == "caption"]}, max(n, 0)


def lost_edits(base: dict, expected: dict, got: dict) -> tuple[list[dict], list[dict]]:
    """Edits that should be in the merge but aren't, and edits that appear but shouldn't (by semantic diff)."""
    key = lambda o: json.dumps({k: (round(v, 2) if isinstance(v, float) else v) for k, v in o.items() if k != "words"}, sort_keys=True)
    want = {key(o): o for o in diff(base, expected) if o["op"] not in ("effect", "effect_remove")}
    have = {key(o): o for o in diff(base, got) if o["op"] not in ("effect", "effect_remove")}
    return [want[k] for k in want.keys() - have.keys()], [have[k] for k in have.keys() - want.keys()]
