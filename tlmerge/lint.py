"""Timeline lint on the exported (timeline-time) form: the checks an editor would otherwise catch by eye."""
from __future__ import annotations

from .model import EPS, FPS, Word


def lint(rendered: dict, words: dict[int, Word]) -> list[tuple[str, str]]:
    out = []
    clips = [i for i in rendered["items"] if i["kind"] == "clip"]
    # timeline continuity: every item starts where the previous one ended
    t = 0.0
    for it in sorted(rendered["items"], key=lambda x: x["t"]):
        if abs(it["t"] - t) > 1 / FPS:
            out.append(("TIMELINE_GAP_OR_OVERLAP", f"item at {it['t']:.2f}s, previous ended at {t:.2f}s"))
        t = it["t"] + it["dur"]
    # duplicated source footage
    srcs = sorted(tuple(c["src"]) for c in clips)
    for (a1, b1), (a2, b2) in zip(srcs, srcs[1:]):
        if a2 < b1 - EPS:
            out.append(("DUPLICATE_SOURCE", f"source {a2:.2f}–{min(b1, b2):.2f}s appears twice"))
    for c in clips:
        if c["dur"] < 2 / FPS - EPS:
            out.append(("FLASH_FRAME", f"{c['dur'] * 1000:.0f} ms clip at {c['t']:.2f}s"))
        for edge in c["src"]:
            for w in words.values():
                if w.start + 0.02 < edge < w.end - 0.02:
                    out.append(("CUT_MID_WORD", f"cut at source {edge:.2f}s splits “{w.text}”"))
    # captions: words present and timing matches where those words now play
    def where(src_t):
        for c in clips:
            if c["src"][0] - EPS <= src_t <= c["src"][1] + EPS:
                return c["t"] + src_t - c["src"][0]
        return None
    for cap in rendered["captions"]:
        ws = [words[w] for w in cap["words"]]
        missing = [w.text for w in ws if where(w.start) is None or where(w.end) is None]
        if len(missing) == len(ws):
            out.append(("CAPTION_ON_CUT_WORDS", f"caption {cap['id']} “{cap['text']}”: none of its words are in the cut"))
            continue
        first = next(w for w in ws if where(w.start) is not None)
        if abs(where(first.start) - cap["t"]) > 1 / FPS:
            out.append(("CAPTION_DRIFT", f"caption {cap['id']} shows at {cap['t']:.2f}s, its words play at {where(first.start):.2f}s"))
    return out
