"""Synthetic talking-head take + random concurrent edits by a human and an agent, with expected conflicts.

Expected conflicts are computed here straight from the edit *intents*. The merger only ever sees the two
resulting timelines, so it has to recover the intents through the diff.
"""
from __future__ import annotations

import random

from .model import Word, apply

SCRIPT = [
    "hey everyone this is the launch of our new product",
    "we built it we built it in three weeks",        # retake: the first "we built it" gets cut by the agent
    "it turns raw footage into a first cut in minutes",
    "you give it your clips and a goal",
    "and cardboard handles silences captions and b roll",
    "teams use it for ads demos and podcasts",
    "try it free today and tell us what you think",
]


def transcript(rng: random.Random):
    words, t, sentences = [], rng.uniform(0.8, 2.0), []
    for si, s in enumerate(SCRIPT):
        ids = []
        for k, tok in enumerate(s.split()):
            d = 0.18 + 0.045 * len(tok) + rng.uniform(0, 0.08)
            words.append(Word(len(words), tok, round(t, 3), round(t + d, 3)))
            ids.append(len(words) - 1)
            t += d + rng.uniform(0.04, 0.12)
            if si == 1 and k == 2:
                t += rng.uniform(0.7, 1.2)      # pause before the retake
        sentences.append(ids)
        t += rng.uniform(0.3, 1.6) if si < len(SCRIPT) - 1 else 0.6
    return words, sentences, round(t, 3)


def base_timeline(words, sentences, total, rng):
    caps, n = {}, 0
    for ids in sentences:
        i = 0
        while i < len(ids):
            k = rng.choice([3, 4, 5])
            grp = ids[i:i + k]
            n += 1
            caps[f"c{n:02d}"] = {"words": grp, "text": " ".join(words[w].text for w in grp), "style": "Clean"}
            i += k
    return {"segments": [[0.0, total]], "inserts": {}, "captions": caps, "effects": []}


def _gap_mid(words, a, b):
    return round((words[a].end + words[b].start) / 2, 3)


def scenario(seed: int):
    rng = random.Random(seed)
    words, sentences, total = transcript(rng)
    W = {w.id: w for w in words}
    base = base_timeline(words, sentences, total, rng)
    caps = list(base["captions"])
    gaps = [(w.id, words[w.id + 1].id) for w in words[:-1] if words[w.id + 1].start - w.end > 0.6]
    retake = (sentences[1][0], sentences[1][2])           # first "we built it"

    # agent (B): silence removal, retake removal, maybe a grade and a caption restyle pass
    ob = [{"op": "cut", "a": round(words[a].end + 0.15, 3), "b": round(words[b].start - 0.15, 3)} for a, b in gaps]
    ob.append({"op": "cut", "a": round(words[retake[0]].start - 0.05, 3), "b": round(words[retake[1] + 1].start - 0.05, 3)})
    if rng.random() < 0.5:
        ob.append({"op": "effect", "a": 8.0, "b": 20.0, "name": "warm grade"})
    b_restyle = rng.random() < 0.5
    if b_restyle:
        ob += [{"op": "caption_style", "id": c, "style": "Word Pop"} for c in caps]

    # human (A): a few edits
    oa = []
    if words[0].start > 0.5 and rng.random() < 0.7:
        oa.append({"op": "cut", "a": 0.0, "b": round(words[0].start - 0.1, 3)})
    if rng.random() < 0.5:
        s = rng.choice([3, 5])
        oa.append({"op": "cut", "a": _gap_mid(words, sentences[s][0] - 1, sentences[s][0]),
                   "b": _gap_mid(words, sentences[s][-1], sentences[s][-1] + 1)})
    a_text = rng.choice(caps) if rng.random() < 0.6 else None
    if a_text:
        t = base["captions"][a_text]["text"].replace("cardboard", "Cardboard")
        oa.append({"op": "caption_text", "id": a_text, "text": t if t != base["captions"][a_text]["text"] else t.capitalize() + "!"})
    a_style = rng.choice(caps) if rng.random() < 0.4 else None
    if a_style:
        oa.append({"op": "caption_style", "id": a_style, "style": "Bold Yellow"})
    if rng.random() < 0.6:
        x, y = rng.choice(gaps) if gaps and rng.random() < 0.5 else (sentences[2][-1], sentences[2][-1] + 1)
        oa.append({"op": "insert", "id": "broll1", "asset": "product_closeup.mp4", "dur": 2.0, "anchor": _gap_mid(words, x, y)})

    # expected conflicts, from intents
    b_cuts = [(o["a"], o["b"]) for o in ob if o["op"] == "cut"]
    a_cuts = [(o["a"], o["b"]) for o in oa if o["op"] == "cut"]
    cut_by = lambda w, cuts: any(a - 1e-6 <= W[w].start and W[w].end <= b + 1e-6 for a, b in cuts)
    expect, held_a, held_b = [], set(), set()
    for i, o in enumerate(oa):
        if o["op"] == "caption_text" and any(cut_by(w, b_cuts) for w in base["captions"][o["id"]]["words"]):
            expect.append(("caption edited on cut words", o["id"])); held_a.add(i)
        if o["op"] == "insert" and any(a + 1e-6 < o["anchor"] < b - 1e-6 for a, b in b_cuts):
            expect.append(("insert anchored in cut", o["id"])); held_a.add(i)
        if o["op"] == "caption_style" and b_restyle and not all(cut_by(w, a_cuts) for w in base["captions"][o["id"]]["words"]) \
                and not all(cut_by(w, b_cuts) for w in base["captions"][o["id"]]["words"]):
            expect.append(("both changed caption style", o["id"])); held_a.add(i)
            held_b |= {j for j, q in enumerate(ob) if q["op"] == "caption_style" and q["id"] == o["id"]}
    a_ok = [o for i, o in enumerate(oa) if i not in held_a]
    b_ok = [o for j, o in enumerate(ob) if j not in held_b]
    return {"words": W, "base": base, "A": apply(base, oa, W), "B": apply(base, ob, W), "intents": (oa, ob),
            "expected": apply(base, a_ok + b_ok, W), "expected_conflicts": sorted(expect)}
