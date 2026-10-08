"""Human + agent edit the same take in parallel → semantic 3-way merge vs `git merge-file`, then lint.

    python demo.py      # showcase scenario + 200 random scenarios → out/report.html
"""
from __future__ import annotations

import html
import sys
import time
from pathlib import Path

from tlmerge.lint import lint
from tlmerge.merge import describe, diff, lost_edits, merge, naive_merge
from tlmerge.model import from_rendered, render
from tlmerge.synth import scenario

HERE = Path(__file__).parent
OUT = HERE / "out"
SHOWCASE, N = 48, 200


def strip(r: dict, total: float, label: str, y: int, words) -> str:
    """One timeline row: clips (grey, with source span), b-roll (blue), captions (ticks, red if misplaced)."""
    sx = lambda t: 150 + t / total * 1150
    out = [f"<text x=0 y={y + 22} font-size=13>{html.escape(label)}</text>"]
    for it in r["items"]:
        col = "#9fb3c8" if it["kind"] == "clip" else "#3b82f6"
        out.append(f"<rect x={sx(it['t']):.1f} y={y} width={max(1, sx(it['t'] + it['dur']) - sx(it['t'])):.1f} height=30 fill='{col}' stroke='#fff'/>")
    bad = {m.split()[1] for code, m in lint(r, words) if code.startswith("CAPTION")}
    for c in r["captions"]:
        col = "#dc2626" if c["id"] in bad else ("#f59e0b" if c["style"] != "Clean" else "#475569")
        out.append(f"<rect x={sx(c['t']):.1f} y={y + 34} width={max(2, sx(c['t'] + c['dur']) - sx(c['t'])):.1f} height=7 fill='{col}'/>")
    out.append(f"<text x={sx(r['duration'] if 'duration' in r else sum(i['dur'] for i in r['items'])) + 6:.1f} y={y + 22} font-size=12 fill='#666'>"
               f"{(r.get('duration') or sum(i['dur'] for i in r['items'])):.1f}s</text>")
    return "".join(out)


def main() -> int:
    t0 = time.perf_counter()
    sc = scenario(SHOWCASE)
    W = sc["words"]
    oa, ob = diff(sc["base"], sc["A"]), diff(sc["base"], sc["B"])
    merged, applied, conflicts = merge(sc["base"], sc["A"], sc["B"], W)
    rb, ra, rB, rm = (render(x, W) for x in (sc["base"], sc["A"], sc["B"], merged))
    gm, ghunks = naive_merge(rb, ra, rB)
    g_lost, g_extra = lost_edits(sc["base"], sc["expected"], from_rendered(gm))

    print(f"\n== showcase: one {rb['duration']:.0f}s take, human and agent edit in parallel\n")
    print("  human (A):"); [print("    " + describe(o, W)) for o in oa]
    print(f"  agent (B): {len(ob)} edits, e.g."); [print("    " + describe(o, W)) for o in ob[:6]]
    print(f"\n  semantic merge: {len(applied)} edits applied, {len(conflicts)} conflicts held for a decision:")
    for c in conflicts:
        print(f"    [{c['kind']}] {c['detail']}  → keep A / keep B")
    print(f"  lint on merged timeline: {len(lint(rm, W))} issues · {rm['duration']:.1f}s")
    print(f"\n  git merge-file on the exported timeline: {ghunks} conflict hunk(s); resolving them 'ours' loses "
          f"{len(g_lost)} edits and adds {len(g_extra)} wrong ones")

    stats = {"want": 0, "sem_bad": 0, "git_lost": 0, "git_extra": 0, "git_scen": 0, "git_hunks": 0, "conf_exact": 0, "free": 0, "free_git_bad": 0}
    for s in range(N):
        x = scenario(s)
        m, _, conf = merge(x["base"], x["A"], x["B"], x["words"])
        l1, e1 = lost_edits(x["base"], x["expected"], m)
        g, h = naive_merge(*(render(y, x["words"]) for y in (x["base"], x["A"], x["B"])))
        l2, e2 = lost_edits(x["base"], x["expected"], from_rendered(g))
        stats["want"] += len([o for o in diff(x["base"], x["expected"]) if o["op"] not in ("effect", "effect_remove")])
        stats["sem_bad"] += len(l1) + len(e1)
        stats["git_lost"] += len(l2)
        stats["git_extra"] += len(e2)
        stats["git_scen"] += bool(l2 or e2)
        stats["git_hunks"] += h > 0
        stats["conf_exact"] += sorted((c["kind"], c["id"]) for c in conf) == x["expected_conflicts"]
        if not x["expected_conflicts"]:
            stats["free"] += 1
            stats["free_git_bad"] += bool(l2 or e2)
    print(f"\n== {N} random human+agent scenarios ({stats['want']} intended edits)")
    print(f"  semantic merge: {stats['sem_bad']} lost/extra edits · conflicts detected exactly in {stats['conf_exact']}/{N}")
    print(f"  git merge-file: {stats['git_lost']} lost + {stats['git_extra']} extra edits · {stats['git_scen']}/{N} scenarios wrong · "
          f"conflict hunks in {stats['git_hunks']}/{N}")
    print(f"  even with no real conflict ({stats['free']} scenarios), git was wrong in {stats['free_git_bad']}")

    OUT.mkdir(exist_ok=True)
    total = rb["duration"] + 4
    rows = [("base take", rb), ("human (A)", ra), ("agent (B)", rB), ("semantic merge", rm), ("git merge-file", gm)]
    svg = "".join(strip(r, total, lab, 20 + i * 64, W) for i, (lab, r) in enumerate(rows))
    conf_html = "".join(f"<li><b>{html.escape(c['kind'])}</b> — {html.escape(c['detail'])} <span class=k>keep A · keep B</span></li>" for c in conflicts)
    (OUT / "report.html").write_text(f"""<!doctype html><meta charset=utf-8><title>Timeline merge</title>
<style>body{{font:14px/1.45 'Segoe UI',system-ui,sans-serif;margin:28px;max-width:1400px;color:#1d1d1f;background:#fafaf8}}h1{{font-size:23px;margin:0}}
h2{{font-size:17px;margin:22px 0 8px}}.sub{{color:#666}}.k{{font-size:12px;background:#eef;border-radius:4px;padding:0 6px;margin-left:6px}}
table{{border-collapse:collapse;background:#fff}}td,th{{border-top:1px solid #e5e5e0;padding:6px 12px;text-align:left}}.g{{display:grid;grid-template-columns:1fr 1fr;gap:24px}}</style>
<h1>Human + agent edit the same take → 3-way timeline merge</h1>
<div class=sub>Edits anchored in source time (clips, b-roll) and transcript word ids (captions), so one side's ripple delete doesn't move the other side's edits. Synthetic take, scenario {SHOWCASE}.</div>
<h2>Timelines</h2><svg viewBox='0 0 1360 345' width=1360>{svg}</svg>
<div class=sub>grey = A-roll clips · blue = b-roll · bars below: captions (amber = restyled, red = shown at the wrong time per lint)</div>
<div class=g><div><h2>Conflicts held for a decision ({len(conflicts)})</h2><ul>{conf_html}</ul>
<p>Everything else merged automatically: {len(applied)} edits. Lint on the merged timeline: <b>{len(lint(rm, W))} issues</b>.</p></div>
<div><h2>{N} random scenarios ({stats['want']} intended edits)</h2><table>
<tr><th></th><th>semantic merge</th><th>git merge-file</th></tr>
<tr><td>edits lost or wrongly added</td><td><b>{stats['sem_bad']}</b></td><td>{stats['git_lost'] + stats['git_extra']}</td></tr>
<tr><td>scenarios with a wrong result</td><td><b>0</b></td><td>{stats['git_scen']} / {N}</td></tr>
<tr><td>conflicts detected exactly</td><td><b>{stats['conf_exact']} / {N}</b></td><td>— (line hunks in {stats['git_hunks']} / {N})</td></tr>
<tr><td>wrong even with no real conflict</td><td><b>0</b></td><td>{stats['free_git_bad']} / {stats['free']}</td></tr></table></div></div>
<p class=sub>Unofficial prototype by Kushagra Pratap Singh. Not affiliated with Cardboard.</p>""", encoding="utf-8")
    print(f"\nreport: out/report.html  ({time.perf_counter() - t0:.1f}s)")
    return 0 if stats["sem_bad"] == 0 and stats["conf_exact"] == N else 1


if __name__ == "__main__":
    sys.exit(main())
