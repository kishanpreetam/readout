"""Build results/report.html: one self-contained page with the key tables and charts (inline SVG, no scripts)."""

from __future__ import annotations

import datetime as dt
import html
import json

from . import config
from .db import connect

OUT = config.RESULTS / "report.html"
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def fmt(n) -> str:
    return f"{n:,}" if isinstance(n, int) else esc(n)


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _load(path):
    p = config.RESULTS / path
    return json.loads(p.read_text()) if p.exists() else None


# ---------- charts (inline SVG; colors come from CSS variables so light and dark both work) ----------

def _bar_path(x, y, w, h, r=4, horizontal=False) -> str:
    """A bar with a 4px rounded data end and a square baseline end."""
    if h <= 0 or w <= 0:
        return ""
    if horizontal:  # grows to the right from x
        r = min(r, w, h / 2)
        return (f"M{x},{y} h{w - r} a{r},{r} 0 0 1 {r},{r} v{h - 2 * r} a{r},{r} 0 0 1 {-r},{r} "
                f"h{-(w - r)} z")
    r = min(r, h, w / 2)  # grows up from y + h
    return (f"M{x},{y + h} v{-(h - r)} a{r},{r} 0 0 1 {r},{-r} h{w - 2 * r} a{r},{r} 0 0 1 {r},{r} "
            f"v{h - r} z")


def monthly_columns(rows: list[tuple[str, int, int]]) -> str:
    """Stacked columns per month: Phase 3 or 2/3 (series 1) under Phase 2 (series 2)."""
    W, H, left, bottom, top = 760, 300, 44, 34, 16
    plot_w, plot_h = W - left - 12, H - bottom - top
    vmax = max((a + b for _, a, b in rows), default=1)
    step = 50 if vmax <= 400 else 100
    ymax = ((vmax // step) + 1) * step
    band = plot_w / max(len(rows), 1)
    bw = min(24, band * 0.55)
    parts = []
    for i in range(0, ymax + 1, step):
        y = top + plot_h - plot_h * i / ymax
        parts.append(f'<line x1="{left}" x2="{W - 12}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                     f'<text x="{left - 8}" y="{y + 4:.1f}" class="tick" text-anchor="end">{i}</text>')
    for k, (label, p3, p2) in enumerate(rows):
        x = left + band * k + (band - bw) / 2
        h3 = plot_h * p3 / ymax
        h2 = plot_h * p2 / ymax
        y3 = top + plot_h - h3
        y2 = y3 - h2 - (2 if p2 and p3 else 0)
        title = f"{label}: {p3} Phase 3 or 2/3, {p2} Phase 2"
        if p3:
            r = 0 if p2 else 4
            parts.append(f'<path d="{_bar_path(x, y3, bw, h3, r=r)}" class="s1"><title>{esc(title)}</title></path>')
        if p2:
            parts.append(f'<path d="{_bar_path(x, y2, bw, h2)}" class="s2"><title>{esc(title)}</title></path>')
        parts.append(f'<text x="{x + bw / 2:.1f}" y="{H - 14}" class="tick" text-anchor="middle">{esc(label)}</text>')
        parts.append(f'<rect x="{left + band * k:.1f}" y="{top}" width="{band:.1f}" height="{plot_h}" '
                     f'class="hit"><title>{esc(title)}</title></rect>')
    parts.append(f'<line x1="{left}" x2="{W - 12}" y1="{top + plot_h}" y2="{top + plot_h}" class="axis"/>')
    return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Upcoming primary completions by month">'
            + "".join(parts) + "</svg>")


def horizontal_stacked(rows: list[tuple[str, int, int]], labels=("Phase 3 or 2/3", "Phase 2")) -> str:
    W, row_h, left, right = 760, 26, 260, 48
    H = row_h * len(rows) + 10
    vmax = max((a + b for _, a, b in rows), default=1)
    plot_w = W - left - right
    parts = []
    for k, (name, a, b) in enumerate(rows):
        y = 5 + k * row_h
        bh = 16
        wa = plot_w * a / vmax
        wb = plot_w * b / vmax
        title = f"{name}: {a} {labels[0]}, {b} {labels[1]}"
        short = name if len(name) <= 38 else name[:36] + "…"
        parts.append(f'<text x="{left - 10}" y="{y + 12}" class="lbl" text-anchor="end">{esc(short)}</text>')
        if a:
            parts.append(f'<path d="{_bar_path(left, y, wa, bh, r=0 if b else 4, horizontal=True)}" class="s1">'
                         f'<title>{esc(title)}</title></path>')
        if b:
            xb = left + wa + (2 if a else 0)
            parts.append(f'<path d="{_bar_path(xb, y, wb, bh, horizontal=True)}" class="s2"><title>{esc(title)}</title></path>')
        parts.append(f'<text x="{left + wa + wb + 8:.1f}" y="{y + 12}" class="val">{a + b}</text>')
    parts.append(f'<line x1="{left}" x2="{left}" y1="0" y2="{H}" class="axis"/>')
    return f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Organizations by upcoming readouts">' + "".join(parts) + "</svg>"


def horizontal_single(rows: list[tuple[str, float, str]], vmax: float = 1.0) -> str:
    """One series of horizontal bars; each row carries its own value label."""
    W, row_h, left, right = 760, 30, 260, 120
    H = row_h * len(rows) + 10
    plot_w = W - left - right
    parts = []
    for k, (name, v, label) in enumerate(rows):
        y = 5 + k * row_h
        w = plot_w * v / vmax
        parts.append(f'<text x="{left - 10}" y="{y + 14}" class="lbl" text-anchor="end">{esc(name)}</text>')
        parts.append(f'<path d="{_bar_path(left, y + 2, max(w, 1), 16, horizontal=True)}" class="s1">'
                     f'<title>{esc(name)}: {esc(label)}</title></path>')
        parts.append(f'<text x="{left + w + 8:.1f}" y="{y + 14}" class="val">{esc(label)}</text>')
    parts.append(f'<line x1="{left}" x2="{left}" y1="0" y2="{H}" class="axis"/>')
    return f'<svg viewBox="0 0 {W} {H}" role="img">' + "".join(parts) + "</svg>"


def grouped_f1(rows: list[tuple[str, float, float]]) -> str:
    """Baseline (series 2) and LLM (series 1) side by side for each field."""
    W, group_h, left, right = 760, 64, 160, 70
    H = group_h * len(rows) + 6
    plot_w = W - left - right
    parts = []
    for k, (field, base, model) in enumerate(rows):
        y = 6 + k * group_h
        parts.append(f'<text x="{left - 10}" y="{y + 25}" class="lbl" text-anchor="end">{esc(field)}</text>')
        for j, (v, cls, who) in enumerate([(model, "s1", "LLM"), (base, "s2", "Dictionary baseline")]):
            yy = y + j * 20
            w = plot_w * v
            parts.append(f'<path d="{_bar_path(left, yy, w, 16, horizontal=True)}" class="{cls}">'
                         f'<title>{esc(field)}, {who}: F1 {v:.3f}</title></path>')
            parts.append(f'<text x="{left + w + 8:.1f}" y="{yy + 12}" class="val">{v:.2f}</text>')
    parts.append(f'<line x1="{left}" x2="{left}" y1="0" y2="{H}" class="axis"/>')
    return f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="F1 by extractor">' + "".join(parts) + "</svg>"


def legend(items: list[tuple[str, str]]) -> str:
    return '<div class="legend">' + "".join(
        f'<span><i class="sw {cls}"></i>{esc(t)}</span>' for cls, t in items) + "</div>"


def table(headers: list[str], rows: list[list], numeric: set[int] = frozenset()) -> str:
    head = "".join(f'<th class="{"num" if i in numeric else ""}">{esc(h)}</th>' for i, h in enumerate(headers))
    body = "".join("<tr>" + "".join(
        f'<td class="{"num" if i in numeric else ""}">{c if isinstance(c, str) and c.startswith("<") else fmt(c)}</td>'
        for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="tablewrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _dbt_line(build: dict) -> str:
    kinds = build.get("by_kind") or {}
    models = sum((kinds.get("model") or {}).values())
    tests = kinds.get("test") or {}
    line = f"DuckDB and dbt: {models} models built, {tests.get('pass', 0)} data tests passed"
    if tests.get("warn"):
        line += (f", {tests['warn']} warning (a data-quality check that flags studies whose primary completion "
                 "date precedes their start date)")
    if tests.get("fail") or tests.get("error"):
        line += f", {tests.get('fail', 0) + tests.get('error', 0)} failed"
    return line


def tile(value: str, label: str, note: str = "") -> str:
    return (f'<div class="tile"><div class="tv">{esc(value)}</div><div class="tl">{esc(label)}</div>'
            + (f'<div class="tn">{esc(note)}</div>' if note else "") + "</div>")


# ---------- page ----------

CSS = """
:root{--bg:#ffffff;--card:#f5f5f7;--ink:#1d1d1f;--ink2:#6e6e73;--ink3:#86868b;--line:#d2d2d7;--grid:#e8e8ed;
--s1:#2a78d6;--s2:#eb6834;--accent:#0066cc;--warn-bg:#fff8e6;--warn-ink:#6b4e00;color-scheme:light}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#000000;--card:#1c1c1e;--ink:#f5f5f7;
--ink2:#a1a1a6;--ink3:#86868b;--line:#3a3a3c;--grid:#2c2c2e;--s1:#3987e5;--s2:#d95926;--accent:#2997ff;
--warn-bg:#2b2410;--warn-ink:#f2d27a;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#000000;--card:#1c1c1e;--ink:#f5f5f7;--ink2:#a1a1a6;--ink3:#86868b;--line:#3a3a3c;
--grid:#2c2c2e;--s1:#3987e5;--s2:#d95926;--accent:#2997ff;--warn-bg:#2b2410;--warn-ink:#f2d27a;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text",
"Helvetica Neue",Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:980px;margin:0 auto;padding:56px 16px 80px}
h1{font-size:44px;line-height:1.08;letter-spacing:-.02em;margin:0 0 12px}
h2{font-size:28px;letter-spacing:-.01em;margin:64px 0 8px}
h3{font-size:19px;margin:28px 0 8px}
p{margin:8px 0;color:var(--ink2)} p.lead{font-size:21px;color:var(--ink2);max-width:760px}
a{color:var(--accent)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:24px 0}
.tile{background:var(--card);border-radius:18px;padding:18px 18px 16px}
.tv{font-size:30px;font-weight:600;letter-spacing:-.01em}.tl{font-size:14px;color:var(--ink2)}
.tn{font-size:12px;color:var(--ink3);margin-top:4px}
.card{background:var(--card);border-radius:18px;padding:20px 18px;margin:16px 0}
svg{width:100%;height:auto;display:block}
.s1{fill:var(--s1)}.s2{fill:var(--s2)}.grid{stroke:var(--grid);stroke-width:1}.axis{stroke:var(--line);stroke-width:1}
.tick{fill:var(--ink3);font-size:12px}.lbl{fill:var(--ink);font-size:13px}.val{fill:var(--ink2);font-size:12px}
.hit{fill:transparent}
.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;color:var(--ink2);margin:4px 0 10px}
.legend span{display:inline-flex;align-items:center;gap:6px}.sw{width:12px;height:12px;border-radius:3px;display:inline-block}
.sw.s1{background:var(--s1)}.sw.s2{background:var(--s2)}
.tablewrap{overflow-x:auto;margin:8px 0}
table{border-collapse:collapse;width:100%;font-size:14px}
th{text-align:left;font-weight:600;color:var(--ink2);border-bottom:1px solid var(--line);padding:8px 10px;white-space:nowrap}
td{border-bottom:1px solid var(--grid);padding:8px 10px;vertical-align:top}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
details{margin:8px 0}summary{cursor:pointer;color:var(--accent);font-size:14px}
.notice{background:var(--warn-bg);color:var(--warn-ink);border-radius:14px;padding:14px 16px;margin:16px 0}
.notice p{color:inherit}
code{font:13px ui-monospace,SFMono-Regular,Menlo,monospace}
pre{white-space:pre-wrap;word-break:break-word;margin:8px 0 0;color:var(--ink)}
.miss p{margin:4px 0}
footer{margin-top:64px;font-size:13px;color:var(--ink3)}
@media (max-width:600px){h1{font-size:32px}h2{font-size:24px}p.lead{font-size:18px}}
"""


def build() -> str:
    as_of = config.as_of_date()
    con = connect(read_only=True)
    q = lambda sql, p=None: con.execute(sql, p or []).fetchall()
    n_trials = q("select count(*) from marts.dim_trial")[0][0]
    n_orgs = q("select count(*) from marts.dim_organization")[0][0]
    n_names = q("select count(*) from staging.stg_resolution__sponsor_organizations")[0][0]
    up = q("select count(distinct nct_id), count(distinct nct_id) filter (where is_phase3), "
           "count(distinct org_id) filter (where is_phase3) from marts.mart_upcoming_readouts")[0]
    months = q("""
        select strftime(primary_completion_date, '%Y-%m') as m,
               count(distinct nct_id) filter (where is_phase3) as p3,
               count(distinct nct_id) filter (where not is_phase3) as p2
        from marts.mart_upcoming_readouts group by 1 order by 1""")
    month_rows = [(f"{MONTHS[int(m[5:]) - 1]} {m[2:4]}", p3, p2) for m, p3, p2 in months]
    top_orgs = q("""
        select org_name, n_phase3_readouts_12m, n_readouts_12m - n_phase3_readouts_12m
        from marts.mart_org_pipeline order by n_readouts_12m desc, org_name limit 15""")
    p3_table = q("""
        select org_name, n_phase3_readouts_12m, next_phase3_primary_completion, n_active_phase3, n_trials_as_lead
        from marts.mart_org_pipeline where n_phase3_readouts_12m > 0
        order by n_phase3_readouts_12m desc, org_name limit 20""")
    soon = q("""
        select u.primary_completion_date, u.org_name, u.nct_id, u.phase_label, u.brief_title, u.conditions
        from marts.mart_upcoming_readouts u where u.is_phase3 and u.sponsor_role = 'lead'
        order by u.primary_completion_date, u.org_name, u.nct_id limit 20""")
    con.close()

    res = _load("resolution/metrics.json")
    rsum = _load("resolution/summary.json")
    ext = _load("extraction/metrics.json")
    qa = _load("qa/metrics.json")
    qa_q = {x["id"]: x for x in (_load("qa/questions.json") or [])}
    sec = _load("sec_access.json") or {}
    manifest = _load("data_manifest.json") or {}
    usage = _load("llm_usage.json") or {}
    dbt_build = _load("dbt_build.json") or {}

    g = res["gold_groups"] if res else None
    s = []
    s.append(f"""<main>
<h1>Readout</h1>
<p class="lead">Clinical-trial pipelines from ClinicalTrials.gov, resolved to the companies behind them, with the
next twelve months of Phase 2 and 3 readouts. Snapshot of {esc(as_of)}.</p>
<div class="tiles">
{tile(fmt(n_trials), "studies in the snapshot", "interventional, industry lead sponsor, primary completion 2020 or later")}
{tile(fmt(n_names), "sponsor name strings", "lead sponsors and industry collaborators")}
{tile(fmt(n_orgs), "organizations after resolution", f"{fmt(n_names - n_orgs)} names merged")}
{tile(fmt(up[1]), "Phase 3 or 2/3 readouts ahead", f"active studies, next 12 months, {fmt(up[2])} organizations")}
</div>""")
    if sec.get("status") != "ok":
        s.append(f"""<div class="notice"><p><strong>Financials are not in this run.</strong> SEC EDGAR refused every
request made without a contact email in the User-Agent (HTTP {esc(sec.get('http_status'))} at
<code>{esc(sec.get('url'))}</code>, checked {esc(sec.get('checked_at'))}). The pipeline stopped SEC fetching as designed,
so the company universe, cash runway and the readout-plus-runway screen are empty here. The dbt models for them are
built and tested on a synthetic fixture; set <code>READOUT_SEC_USER_AGENT</code> to a declared contact and run
<code>make all</code> to fill them.</p></div>""")

    s.append("<h2>Upcoming readouts</h2><p>Active Phase 2, 2/3 and 3 studies whose primary completion (actual or "
             f"anticipated) falls in the 12 months after {esc(as_of)}. Each study counts once per month.</p>")
    s.append('<div class="card">' + legend([("s1", "Phase 3 or Phase 2/3"), ("s2", "Phase 2")])
             + monthly_columns(month_rows) + "</div>")
    s.append("<p>December stands out partly because many sponsors give an anticipated date as a month or a year end, "
             "and month-only dates are read as the last day of the month.</p>")
    s.append("<details><summary>Table view</summary>" + table(
        ["Month", "Phase 3 or 2/3", "Phase 2", "Total"], [[m, a, b, a + b] for m, a, b in month_rows], {1, 2, 3})
        + "</details>")
    s.append("<h3>Organizations with the most upcoming readouts</h3><p>Lead sponsor or collaborator, after resolution.</p>")
    s.append('<div class="card">' + legend([("s1", "Phase 3 or Phase 2/3"), ("s2", "Phase 2")])
             + horizontal_stacked(top_orgs) + "</div>")
    s.append("<details><summary>Table view</summary>" + table(
        ["Organization", "Phase 3 or 2/3", "Phase 2", "Total"], [[o, a, b, a + b] for o, a, b in top_orgs], {1, 2, 3})
        + "</details>")
    s.append("<h3>Phase 3 readouts in the next 12 months, by organization</h3>")
    s.append(table(["Organization", "Phase 3 or 2/3 readouts", "Next one", "Active Phase 3 or 2/3", "Studies led"],
                   [[o, a, str(d), c, e] for o, a, d, c, e in p3_table], {1, 3, 4}))
    s.append("<h3>The next 20 Phase 3 readouts (lead sponsor)</h3>")
    s.append(table(["Primary completion", "Organization", "Study", "Phase", "Title", "Conditions"],
                   [[str(d), o, f'<a href="https://clinicaltrials.gov/study/{esc(n)}">{esc(n)}</a>', ph, t,
                     (c or "")[:80]] for d, o, n, ph, t, c in soon]))

    s.append("<h2>Readouts and cash runway</h2>")
    s.append("<p>The screen joins each SEC-linked company's Phase 3 readouts with its runway: cash, equivalents and "
             "short-term investments divided by the average quarterly cash burn over the trailing 12 months, times 3. "
             "Quarterly burn comes from the operating cash flow line, derived from year-to-date XBRL values.</p>")
    if sec.get("status") != "ok":
        s.append('<div class="notice"><p>No rows in this run: SEC data was refused (see above). The logic is covered '
                 'by <code>tests/test_dbt_financials.py</code>, which runs the dbt project on a small synthetic '
                 'company and checks the derived quarters, the runway months and the funding-risk flag.</p></div>')

    if g:
        kinds = g["recall_by_kind_of_name"]
        audit = res.get("pair_audit") or {}
        s.append("<h2>Sponsor resolution, measured</h2>")
        s.append(f"""<p>{fmt(rsum['sponsor_names'])} sponsor strings became {fmt(rsum['organizations'])} organizations.
Rules merge spellings and units of one company; ownership clauses that sponsors write into their names
(“a wholly owned subsidiary of Pfizer”, “a GSK company”) roll subsidiaries up to parents. No
hand-made alias list is used, so the labels below are independent of the resolver.</p>""")
        s.append('<div class="tiles">'
                 + tile(pct(g["name_precision_micro"]), "precision on the 20 largest groups",
                        f"{g['gold_member_names']} hand-labeled member names")
                 + tile(pct(g["trial_recall_micro"]), "of their trials attributed correctly", "trial-weighted recall")
                 + tile(pct(g["name_recall_micro"]), "of their names found", "name-level recall")
                 + (tile(pct(audit['merged']['share_same']), "precision on long-tail merges",
                         f"{audit['merged']['pairs']} audited pairs, {audit['merged']['unsure']} unsure and excluded") if audit.get("merged") else "")
                 + "</div>")
        kind_rows = [("Spelling or unit variant", kinds["spelling_or_unit_variant"]),
                     ("Name states its parent", kinds["states_its_parent"]),
                     ("Separately named subsidiary", kinds["separately_named_subsidiary"])]
        s.append("<h3>Recall by kind of name</h3><p>Where the resolver works and where it cannot without outside "
                 "knowledge (a subsidiary such as Genentech never names Roche).</p>")
        s.append('<div class="card">' + horizontal_single(
            [(k, v["recall"], f"{pct(v['recall'])} ({v['found']} of {v['names']})") for k, v in kind_rows]) + "</div>")
        s.append("<details><summary>Per-group results</summary>" + table(
            ["Group", "Resolved as", "Gold names", "Found", "Precision", "Trial recall"],
            [[r["group"], r["predicted_name"], r["gold_names"], r["correct_names"], pct(r["name_precision"]),
              pct(r["trial_recall"])] for r in g["per_group"]], {2, 3, 4, 5}) + "</details>")
        if audit.get("near_miss"):
            s.append(f"<p>Long tail: of {audit['near_miss']['pairs']} similar-looking pairs the resolver kept apart, "
                     f"{audit['near_miss']['same']} were in fact the same company "
                     f"({pct(audit['near_miss']['share_same'])}).</p>")

    if ext and ext.get("llm"):
        b, l = ext["baseline"], ext["llm"]
        s.append("<h2>Entity extraction from trial text</h2>")
        s.append(f"""<p>Conditions and interventions extracted from the title and brief summary of {ext['sample_size']}
seeded Phase 2 and 3 drug studies, scored against the registry's own structured fields (a silver standard).
The baseline matches dictionaries built only from other studies; the model is {esc(ext.get('model'))}.</p>""")
        s.append('<div class="card">' + legend([("s1", "LLM"), ("s2", "Dictionary baseline")]) + grouped_f1(
            [("Conditions F1", b["conditions"]["f1"], l["conditions"]["f1"]),
             ("Interventions F1", b["interventions"]["f1"], l["interventions"]["f1"])]) + "</div>")
        s.append(table(["Field", "Extractor", "Precision", "Recall", "F1", "Recall on items the text mentions"],
                       [[f, who, f"{m[f]['precision']:.3f}", f"{m[f]['recall']:.3f}", f"{m[f]['f1']:.3f}",
                         f"{m[f]['recall_on_items_mentioned_in_text']:.3f}"]
                        for f in ("conditions", "interventions") for who, m in (("Baseline", b), ("LLM", l))],
                       {2, 3, 4, 5}))
        hr = ext.get("hand_review_of_sampled_disagreements") or {}
        if hr:
            s.append(f"""<p>A hand review of {hr['reviewed']} of the model's {ext.get('llm_disagreements_total')}
disagreements with the registry found {hr.get('same_concept_different_wording', 0)} cases of the same concept in
different words, {hr.get('granularity', 0)} differences in granularity, {hr.get('valid_but_not_registered', 0)}
valid items the registry does not list, {hr.get('registry_item_not_in_text', 0)} registry items absent from the
text, and {hr.get('clear_extraction_errors', 0)} clear extraction errors. The automatic scores understate the
model.</p>""")

    if qa:
        sd, so = qa["variants"].get("schema_docs", {}), qa["variants"].get("schema_only", {})
        s.append("<h2>Question answering over the warehouse</h2>")
        from .qa import QUESTIONS
        n_multi = sum(1 for x in QUESTIONS if int(x[0][1:]) > 36)
        s.append(f"""<p>{qa['questions']} questions with hand-written gold SQL ({qa['questions'] - n_multi} single-step,
{n_multi} multi-step). The model
writes DuckDB SQL, which runs in a read-only sandbox (one SELECT only, file and network access off, timeout);
the result must equal the gold answer.</p>""")
        s.append('<div class="tiles">'
                 + tile(f"{sd.get('correct')}/{qa['questions']}", "schema plus documentation",
                        "column meanings, code values, 3 worked examples")
                 + tile(f"{so.get('correct')}/{qa['questions']}", "schema only", "table and column names and types")
                 + "</div>")
        try:
            recs = [json.loads(x) for x in (config.RESULTS / "qa" / "predictions.jsonl").read_text().splitlines()]
        except FileNotFoundError:
            recs = []
        wrong = [r for r in recs if not r.get("correct")]
        if wrong:
            s.append("<h3>Every wrong answer</h3>")
            for r in wrong:
                got = (r.get("result") or [[None]])[0]
                gold_ans = qa_q.get(r["id"], {}).get("gold_answer") or [[None]]
                def show(v):
                    return int(v) if isinstance(v, float) and v.is_integer() else v

                def rows_word(n):
                    return f"{n} row" if n == 1 else f"{n} rows"

                s.append(f'<div class="card miss"><p><strong>{esc(r["question"])}</strong></p>'
                         f'<p>Prompt: {esc(r["variant"])}. Got {esc(show(got[0]) if got else None)} '
                         f'({rows_word(r.get("n_rows", 0))}); gold {esc(show(gold_ans[0][0]))} '
                         f'({rows_word(len(gold_ans))}).</p>'
                         f'<pre><code>{esc(r.get("sql"))}</code></pre></div>')
            cats: dict[str, int] = {}
            for v in qa["variants"].values():
                for c, n in (v.get("failure_categories_hand_reviewed") or {}).items():
                    cats[c] = cats.get(c, 0) + n
            if cats.get("phase_code_semantics") == len(wrong):
                s.append("<p>Hand review: every miss has the same cause. Without documented phase codes the model "
                         "filtered <code>phase_number &gt;= 2.5</code> for “Phase 3 or Phase 2/3”, which also "
                         "counts Phase 4. The documented prompt answered all of these correctly.</p>")
            elif cats:
                s.append("<p>Hand-reviewed causes: " + esc(", ".join(f"{c} ({n})" for c, n in cats.items())) + ".</p>")

    s.append("<h2>Data and method</h2>")
    s.append(table(["Item", "Value"], [
        ["ClinicalTrials.gov query", "AREA[LeadSponsorClass]INDUSTRY AND AREA[StudyType]INTERVENTIONAL AND "
                                     "AREA[PrimaryCompletionDate]RANGE[2020-01-01,MAX]"],
        ["Snapshot date", as_of],
        ["Files downloaded", fmt(manifest.get("files"))],
        ["Size", f"{manifest.get('bytes_uncompressed', 0) / 1e6:.1f} MB as JSON, "
                 f"{manifest.get('bytes_stored_gzip', 0) / 1e6:.1f} MB stored gzip"],
        ["SEC EDGAR", "refused (HTTP 403) without a contact email; not used in this run" if sec.get("status") != "ok"
         else "downloaded"],
        ["Warehouse", _dbt_line(dbt_build)],
        ["Model calls", f"{usage.get('calls')} calls to {', '.join(usage.get('models', {}))} (extraction "
                        f"{usage.get('by_task', {}).get('extraction')}, question answering "
                        f"{usage.get('by_task', {}).get('qa_schema_only', 0) + usage.get('by_task', {}).get('qa_schema_docs', 0)})"],
    ]))
    s.append(f"""<footer><p>Data: ClinicalTrials.gov, U.S. National Library of Medicine, retrieved {esc(as_of)}.
Registry dates marked estimated are sponsors' forecasts and often slip.
This report is for research and education only. It is not investment advice.</p>
<p>Built {dt.date.today().isoformat()} by <code>python -m readout report</code>.</p></footer></main>""")

    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width, initial-scale=1"><title>Readout Report</title><style>{CSS}</style>'
            f'</head><body>{"".join(s)}</body></html>')
    OUT.write_text(page)
    return str(OUT.relative_to(config.ROOT))
