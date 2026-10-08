"""Timeline model anchored in source time.

A timeline over one source take (A-roll) is:
  segments  kept source ranges, in order               [[a, b], ...]
  inserts   b-roll placed at a source-time anchor       {id: {asset, dur, anchor}}
  captions  anchored to transcript word ids             {id: {words: [..], text, style}}
  effects   applied to source ranges                    [{a, b, name}]
Edits expressed this way commute far more often than edits on timeline positions,
because a ripple delete in one branch doesn't move anything the other branch points at.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

FPS = 30
EPS = 1e-6


@dataclass(frozen=True)
class Word:
    id: int
    text: str
    start: float
    end: float


def subtract(segs: list[list[float]], a: float, b: float) -> list[list[float]]:
    out = []
    for s, e in segs:
        if e <= a + EPS or s >= b - EPS:
            out.append([s, e])
            continue
        if s < a - EPS:
            out.append([s, a])
        if e > b + EPS:
            out.append([b, e])
    return out


def kept(segs, t: float) -> bool:
    return any(s - EPS <= t <= e + EPS for s, e in segs)


def word_kept(segs, w: Word) -> bool:
    return any(s - EPS <= w.start and w.end <= e + EPS for s, e in segs)


# ------------------------------------------------------------------ ops

def apply(tl: dict, ops: list[dict], words: dict[int, Word]) -> dict:
    t = copy.deepcopy(tl)
    for op in ops:
        k = op["op"]
        if k == "cut":
            t["segments"] = subtract(t["segments"], op["a"], op["b"])
        elif k == "insert":
            t["inserts"][op["id"]] = {"asset": op["asset"], "dur": op["dur"], "anchor": op["anchor"]}
        elif k == "delete_insert":
            t["inserts"].pop(op["id"], None)
        elif k == "caption_add":
            t["captions"][op["id"]] = {"words": op["words"], "text": op["text"], "style": op["style"]}
        elif k == "caption_text":
            t["captions"][op["id"]]["text"] = op["text"]
        elif k == "caption_style":
            t["captions"][op["id"]]["style"] = op["style"]
        elif k == "caption_delete":
            t["captions"].pop(op["id"], None)
        elif k == "effect":
            if not any(e == {"a": op["a"], "b": op["b"], "name": op["name"]} for e in t["effects"]):
                t["effects"].append({"a": op["a"], "b": op["b"], "name": op["name"]})
        elif k == "effect_remove":
            t["effects"] = [e for e in t["effects"] if e != {"a": op["a"], "b": op["b"], "name": op["name"]}]
    # captions whose words were all cut disappear with them
    t["captions"] = {cid: c for cid, c in t["captions"].items() if any(word_kept(t["segments"], words[w]) for w in c["words"])}
    t["inserts"] = {i: x for i, x in t["inserts"].items() if kept(t["segments"], x["anchor"])}
    return t


# ------------------------------------------------------------------ render to timeline time (what an NLE export sees)

def render(tl: dict, words: dict[int, Word]) -> dict:
    """Clips and captions with timeline start/duration, like an FCPXML/EDL export."""
    anchors = sorted(((x["anchor"], i, x) for i, x in tl["inserts"].items()), key=lambda z: (z[0], z[1]))
    items, t = [], 0.0
    for s, e in sorted(tl["segments"]):
        cur = s
        for anc, iid, x in anchors:
            if s - EPS <= anc <= e + EPS and cur - EPS <= anc:
                if anc > cur + EPS:
                    items.append({"kind": "clip", "src": [round(cur, 3), round(anc, 3)], "t": round(t, 3), "dur": round(anc - cur, 3)})
                    t += anc - cur
                items.append({"kind": "broll", "id": iid, "asset": x["asset"], "t": round(t, 3), "dur": x["dur"]})
                t += x["dur"]
                cur = anc
        if e > cur + EPS:
            items.append({"kind": "clip", "src": [round(cur, 3), round(e, 3)], "t": round(t, 3), "dur": round(e - cur, 3)})
            t += e - cur
    caps = []
    for cid, c in sorted(tl["captions"].items()):
        ws = [words[w] for w in c["words"] if word_kept(tl["segments"], words[w])]
        if not ws:
            continue
        st, en = to_timeline(items, ws[0].start), to_timeline(items, ws[-1].end)
        caps.append({"kind": "caption", "id": cid, "words": c["words"], "text": c["text"], "style": c["style"],
                     "t": round(st, 3), "dur": round(en - st, 3)})
    return {"items": items, "captions": caps, "duration": round(t, 3)}


def to_timeline(items: list[dict], src_t: float) -> float:
    for it in items:
        if it["kind"] == "clip" and it["src"][0] - EPS <= src_t <= it["src"][1] + EPS:
            return it["t"] + (src_t - it["src"][0])
    return float("nan")


def from_rendered(r: dict) -> dict:
    """Rebuild a source-anchored timeline from an exported (timeline-time) one, e.g. git's merge output."""
    clips = sorted((i for i in r["items"] if i["kind"] == "clip"), key=lambda x: x["t"])
    segs = []
    for c in clips:
        a, b = c["src"]
        if segs and abs(segs[-1][1] - a) < EPS:
            segs[-1][1] = b
        else:
            segs.append([a, b])
    inserts = {}
    for it in sorted(r["items"], key=lambda x: x["t"]):
        if it["kind"] == "broll":
            prev = [c for c in clips if c["t"] + c["dur"] <= it["t"] + EPS]
            inserts[it["id"]] = {"asset": it["asset"], "dur": it["dur"], "anchor": prev[-1]["src"][1] if prev else 0.0}
    caps = {c["id"]: {"words": c["words"], "text": c["text"], "style": c["style"]} for c in r["captions"]}
    return {"segments": segs, "inserts": inserts, "captions": caps, "effects": []}
