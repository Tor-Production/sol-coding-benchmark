#!/usr/bin/env python3
"""Regenerate the public results page and charts without inference or network use.

SVG generation uses only the Python standard library. Pillow is optional and
adds PNG previews; SVG geometry and data do not depend on Pillow or local fonts.
The archived PDF and original result files are read-only inputs.
"""
from __future__ import annotations

import argparse
import html
import json
import math
from collections import Counter
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
MODELS = ("gpt-5.6-sol", "gpt-6-sol", "gpt-6.1-sol")
EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")
TASKS = ("01-easy", "02-medium", "03-hard", "04-components", "05-optimizer", "06-transactions")
LABELS = {"gpt-5.6-sol": "Sol 5.6", "gpt-6-sol": "Sol 6", "gpt-6.1-sol": "Sol 6.1"}
COLORS = {"gpt-5.6-sol": "#D75B3B", "gpt-6-sol": "#3478BE", "gpt-6.1-sol": "#128C7E"}
INK, MUTED, GRID, PAPER = "#182D44", "#53667B", "#E1E8EF", "#FFFFFF"


def read_json(name):
    return json.loads((ROOT / "results" / name).read_text(encoding="utf-8"))


def close(a, b):
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-8)


def load():
    data = read_json("results.json")
    runs = data["runs"]
    assert len(runs) == 216 and len({r["run_id"] for r in runs}) == 216
    summaries = {(s["model"], s["reasoning_effort"]): s for s in data["summary"]}
    assert len(summaries) == 18
    for model in MODELS:
        for effort in EFFORTS:
            s = summaries[model, effort]
            group = [r for r in runs if r["model"] == model and r["reasoning_effort"] == effort]
            assert len(group) == s["expected"] == s["attempted"] == 12
            assert Counter(r["task"] for r in group) == {t: 2 for t in TASKS}
            assert all({r["repetition"] for r in group if r["task"] == t} == {1, 2} for t in TASKS)
            accepted = sum(r["status"] == "completed" and r["grading"]["accepted"] for r in group)
            assert accepted == s["accepted"]
            assert close(mean(r["model_elapsed_seconds"] for r in group), s["model_seconds_mean"])
            macro = mean(mean(r["grading"]["score"] for r in group if r["task"] == t) for t in TASKS)
            assert close(macro, s["quality_macro_mean"])
            observed = sum(r["costs"]["api_usd_low"] for r in group)
            assert close(observed, s["api_usd_low_observed"])
            exact = all(r["costs"]["api_estimate_complete"] for r in group)
            assert exact == (s["api_usd_total"] is not None)
            if exact:
                assert close(sum(r["costs"]["api_usd_estimated"] for r in group), s["api_usd_total"])
    original = read_json("quality-v2.1.json")["runs"]
    complex_tests = read_json("complex-test-strength.json")["evaluations"]
    assert len(original) == 144 and len(complex_tests) == 72
    mutation = {r["run_id"]: r["test_effectiveness"].get("score") for r in original}
    mutation.update({r["run_id"]: r["score"] for r in complex_tests})
    assert set(mutation) == {r["run_id"] for r in runs}
    assert sum(r["status"] == "completed" for r in runs) == 214
    assert sum(r["status"] == "completed" and r["grading"]["accepted"] for r in runs) == 213
    assert sum(r["costs"]["api_estimate_complete"] for r in runs) == 214
    assert sum(mutation[r["run_id"]] is not None for r in runs if r["status"] == "completed") == 175
    return data, summaries, mutation, original, complex_tests


class Canvas:
    """A small deterministic vector canvas with an optional Pillow renderer."""
    def __init__(self, width, height, title, description):
        self.width, self.height = width, height
        self.title, self.description = title, description
        self.nodes = []
        self.rect(0, 0, width, height, PAPER)

    def rect(self, x, y, w, h, fill, stroke=None, radius=0):
        self.nodes.append(("rect", (x, y, w, h, fill, stroke, radius)))

    def line(self, points, color=GRID, width=1, dash=False):
        self.nodes.append(("line", (points, color, width, dash)))

    def text(self, x, y, text, size=16, color=INK, weight="normal", anchor="start"):
        self.nodes.append(("text", (x, y, str(text), size, color, weight, anchor)))

    def circle(self, x, y, r, fill, stroke=None, width=1):
        self.nodes.append(("circle", (x, y, r, fill, stroke, width)))

    def polygon(self, points, fill, stroke=None, width=1):
        self.nodes.append(("polygon", (points, fill, stroke, width)))

    def marker(self, x, y, effort, color, radius=7):
        if effort == "low":
            self.circle(x, y, radius, color, PAPER, 1.5)
        elif effort == "medium":
            self.rect(x-radius, y-radius, 2*radius, 2*radius, color, PAPER)
        elif effort == "high":
            self.polygon([(x, y-radius-2), (x-radius-2, y+radius), (x+radius+2, y+radius)], color, PAPER, 1.5)
        elif effort == "xhigh":
            self.polygon([(x, y-radius-2), (x+radius+2, y), (x, y+radius+2), (x-radius-2, y)], color, PAPER, 1.5)
        elif effort == "max":
            r, k = radius+1, radius/2
            self.polygon([(x-k,y-r),(x+k,y-r),(x+k,y-k),(x+r,y-k),(x+r,y+k),(x+k,y+k),(x+k,y+r),(x-k,y+r),(x-k,y+k),(x-r,y+k),(x-r,y-k),(x-k,y-k)], color, PAPER, 1.5)
        else:
            points = [(x + (radius+3 if i%2 == 0 else radius*.45)*math.cos(-math.pi/2+i*math.pi/5), y + (radius+3 if i%2 == 0 else radius*.45)*math.sin(-math.pi/2+i*math.pi/5)) for i in range(10)]
            self.polygon(points, color, PAPER, 1.5)

    def arrow(self, x, y, color):
        self.line([(x,y-11),(x,y-31)], color, 2)
        self.polygon([(x,y-36),(x-4,y-28),(x+4,y-28)], color)

    def save(self, name, png):
        elements = []
        n = lambda x: f"{x:.3f}".rstrip("0").rstrip(".")
        for kind, args in self.nodes:
            if kind == "rect":
                x,y,w,h,fill,stroke,r = args
                elements.append(f'<rect x="{n(x)}" y="{n(y)}" width="{n(w)}" height="{n(h)}" rx="{n(r)}" fill="{fill}" stroke="{stroke or "none"}"/>')
            elif kind == "line":
                points,color,width,dash = args
                xy = " ".join(f"{n(x)},{n(y)}" for x,y in points)
                elements.append(f'<polyline points="{xy}" fill="none" stroke="{color}" stroke-width="{n(width)}" stroke-linejoin="round" stroke-linecap="round"' + (' stroke-dasharray="6 6"' if dash else '') + '/>')
            elif kind == "text":
                x,y,text,size,color,weight,anchor = args
                elements.append(f'<text x="{n(x)}" y="{n(y)}" font-family="Arial, Helvetica, sans-serif" font-size="{n(size)}" font-weight="{weight}" text-anchor="{anchor}" fill="{color}">{html.escape(text)}</text>')
            elif kind == "circle":
                x,y,r,fill,stroke,width = args
                elements.append(f'<circle cx="{n(x)}" cy="{n(y)}" r="{n(r)}" fill="{fill}" stroke="{stroke or "none"}" stroke-width="{n(width)}"/>')
            else:
                points,fill,stroke,width = args
                xy = " ".join(f"{n(x)},{n(y)}" for x,y in points)
                elements.append(f'<polygon points="{xy}" fill="{fill}" stroke="{stroke or "none"}" stroke-width="{n(width)}"/>')
        path = ROOT / "assets" / (name + ".svg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="title desc" viewBox="0 0 '+str(self.width)+' '+str(self.height)+'">\n<title id="title">'+html.escape(self.title)+'</title>\n<desc id="desc">'+html.escape(self.description)+'</desc>\n'+"\n".join(elements)+'\n</svg>\n', encoding="utf-8", newline="\n")
        if png:
            self.save_png(path.with_suffix(".png"))

    def save_png(self, path):
        from PIL import Image, ImageDraw, ImageFont
        scale = 2
        image = Image.new("RGB", (self.width*scale, self.height*scale), PAPER)
        draw = ImageDraw.Draw(image)
        fonts = {}
        def font(size, weight):
            key = (size, weight)
            if key not in fonts:
                options = [Path("C:/Windows/Fonts/arialbd.ttf" if weight == "bold" else "C:/Windows/Fonts/arial.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if weight == "bold" else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
                available = next((p for p in options if p.exists()), None)
                fonts[key] = ImageFont.truetype(str(available), round(size*scale)) if available else ImageFont.load_default(size=round(size*scale))
            return fonts[key]
        pt = lambda points: [(round(x*scale),round(y*scale)) for x,y in points]
        for kind,args in self.nodes:
            if kind == "rect":
                x,y,w,h,fill,stroke,r = args
                draw.rounded_rectangle((round(x*scale),round(y*scale),round((x+w)*scale),round((y+h)*scale)), radius=round(r*scale), fill=fill, outline=stroke, width=scale)
            elif kind == "line":
                points,color,width,dash = args
                if dash:
                    for (ax,ay),(bx,by) in zip(points,points[1:]):
                        length = math.hypot(bx-ax,by-ay)
                        for start in range(0,math.ceil(length),12):
                            a,b = min(1,start/length),min(1,(start+6)/length)
                            draw.line(pt([(ax+(bx-ax)*a,ay+(by-ay)*a),(ax+(bx-ax)*b,ay+(by-ay)*b)]), fill=color, width=round(width*scale))
                else:
                    draw.line(pt(points), fill=color, width=round(width*scale), joint="curve")
            elif kind == "text":
                x,y,text,size,color,weight,anchor = args
                draw.text((round(x*scale),round(y*scale)),text,font=font(size,weight),fill=color,anchor={"start":"ls","middle":"ms","end":"rs"}[anchor])
            elif kind == "circle":
                x,y,r,fill,stroke,width = args
                draw.ellipse((round((x-r)*scale),round((y-r)*scale),round((x+r)*scale),round((y+r)*scale)),fill=fill,outline=stroke,width=round(width*scale))
            else:
                points,fill,stroke,width = args
                draw.polygon(pt(points),fill=fill)
                if stroke:
                    draw.line(pt(points+[points[0]]),fill=stroke,width=round(width*scale),joint="curve")
        image.save(path, optimize=True)


def cost(summary):
    return (summary["api_usd_total"] if summary["api_usd_total"] is not None else summary["api_usd_low_observed"])/12


def usd(summary, divisor=12):
    if summary["api_usd_total"] is None:
        # Rounding a lower bound up would overstate the proven bound.
        return "≥$" + f"{math.floor(summary['api_usd_low_observed']/divisor*1000)/1000:.3f}"
    return "$" + f"{summary['api_usd_total']/divisor:.3f}"


def model_legend(canvas, y, start=70, gap=185):
    for i,model in enumerate(MODELS):
        x = start+i*gap
        canvas.line([(x,y-5),(x+26,y-5)],COLORS[model],3)
        canvas.text(x+36,y,LABELS[model],16,INK,"bold")


def scatter(summaries, png):
    c = Canvas(1200, 720, "API-equivalent cost versus elapsed agent time", "Eighteen model and effort settings, each averaging twelve attempts. Effort points are joined within each model. Sol 6 Max and Ultra costs are observed lower bounds. Elapsed time includes tool use.")
    c.text(54,44,"Cost and time across the same six coding tasks",28,INK,"bold")
    c.text(54,74,"Mean per attempt · 2 repetitions per task · frozen Standard API-equivalent USD",16,MUTED)
    model_legend(c,108)
    left,top,right,bottom = 95,150,1130,579
    px=lambda t:left+t/15*(right-left)
    py=lambda v:bottom-v/1.25*(bottom-top)
    for v in [0,.25,.5,.75,1,1.25]:
        y=py(v); c.line([(left,y),(right,y)],GRID,1); c.text(left-13,y+5,f"${v:.2f}",14,MUTED,anchor="end")
    for t in range(0,16,3):
        x=px(t); c.line([(x,top),(x,bottom)],GRID,1); c.text(x,bottom+25,str(t),14,MUTED,anchor="middle")
    c.text(left,top-12,"USD / attempt",15,INK,"bold")
    c.text((left+right)/2,bottom+55,"Elapsed agent time / attempt (minutes)",16,INK,"bold",anchor="middle")
    offsets = {
        "gpt-5.6-sol":[(-8,-18),(-13,-19),(-13,-20),(-15,-20),(-30,-21),(14,22)],
        "gpt-6-sol":[(-12,-16),(-17,-15),(-10,-19),(-18,-19),(12,-13),(13,-15)],
        "gpt-6.1-sol":[(12,21),(12,19),(12,21),(12,20),(12,21),(-48,28)],
    }
    for model in MODELS:
        rows=[summaries[model,e] for e in EFFORTS]
        points=[(px(s["model_seconds_mean"]/60),py(cost(s))) for s in rows]
        for i in range(5):
            partial=any(s["api_usd_total"] is None for s in rows[i:i+2])
            c.line(points[i:i+2],COLORS[model],2.5,partial)
        for i,(s,(x,y)) in enumerate(zip(rows,points)):
            c.marker(x,y,EFFORTS[i],COLORS[model])
            dx,dy=offsets[model][i]
            c.text(x+dx,y+dy,EFFORTS[i].title()+("*" if s["api_usd_total"] is None else ""),13,COLORS[model],"bold", "end" if dx<0 else "start")
            if s["api_usd_total"] is None:c.arrow(x,y,COLORS[model])
    c.rect(56,656,1088,43,"#F2F6FA",radius=8)
    c.text(72,674,"* Sol 6 Max / Ultra: partial cost telemetry. Upward arrows indicate a lower bound; the complete cost is unknown.",13,MUTED)
    c.text(72,691,"Lines show effort order within a model, not interpolation. Lower-left means less money and less elapsed time.",13,MUTED)
    c.save("cost-time",png)


def curves(summaries,png):
    c=Canvas(1200,570,"How cost and agent time change with reasoning effort","Mean API-equivalent dollars and elapsed agent minutes for six efforts and three models; twelve attempts per setting. Two Sol 6 cost values are lower bounds.")
    c.text(54,44,"Higher effort usually costs more time and money",28,INK,"bold")
    c.text(54,74,"Same 12 attempts per setting · observed means · two runs per task",16,MUTED)
    model_legend(c,108)
    top,bottom=161,431
    for panel in [0,1]:
        left,right=(95,553) if panel==0 else (681,1139)
        c.text(left,145,"API-equivalent USD / attempt" if panel==0 else "Elapsed agent time / attempt",17,INK,"bold")
        xmax=1.25 if panel==0 else 15
        py=lambda v:bottom-v/xmax*(bottom-top)
        px=lambda i:left+i/5*(right-left)
        ticks=[0,.25,.5,.75,1,1.25] if panel==0 else [0,3,6,9,12,15]
        for v in ticks:
            y=py(v);c.line([(left,y),(right,y)],GRID);c.text(left-12,y+5,f"${v:.2f}" if panel==0 else f"{v} min",13,MUTED,anchor="end")
        for i,e in enumerate(EFFORTS):
            c.text(px(i),bottom+25,e.title(),13,MUTED,anchor="middle")
        for model in MODELS:
            rows=[summaries[model,e] for e in EFFORTS]
            points=[(px(i),py(cost(s) if panel==0 else s["model_seconds_mean"]/60)) for i,s in enumerate(rows)]
            for i in range(5):c.line(points[i:i+2],COLORS[model],2.5,panel==0 and any(s["api_usd_total"] is None for s in rows[i:i+2]))
            for i,(s,(x,y)) in enumerate(zip(rows,points)):
                c.marker(x,y,EFFORTS[i],COLORS[model],6)
                if panel==0 and s["api_usd_total"] is None:c.arrow(x,y,COLORS[model])
    c.rect(56,488,1088,62,"#F2F6FA",radius=8)
    c.text(72,508,"Cost arrows: incomplete Sol 6 Max / Ultra totals. Time remains the observed elapsed time across all 12 attempts.",13,MUTED)
    c.text(72,530,"These are descriptive averages, not a smooth scaling law or a claim that additional reasoning always improves code.",13,MUTED)
    c.save("effort-curves",png)


def shade(score):
    if score is None:return "#EEF1F5"
    if score>=99.9:return "#D5EEE8"
    if score>=90:return "#E6F2E7"
    if score>=75:return "#EFF0CD"
    if score>=50:return "#F4E2B9"
    if score>0:return "#F3D1B9"
    return "#ECC4BD"


def quality(data, summaries, mutation, png):
    c=Canvas(1370,1000,"Functional score and generated-test mutation sensitivity","Functional scores for all six tasks, plus two separately interpreted generated-test sensitivity panels. Mutation pair means require two usable repetitions; incomplete pairs show N/A and their actual usable coverage. N/A is not zero.")
    c.text(42,44,"Functional scores are near the ceiling; tests show more variation",26,INK,"bold")
    c.text(42,76,"Two repetitions per cell · scores in % · mutation pair means require 2/2 usable evaluations",16,MUTED)
    panels=[(255,70,TASKS,"Main functional score"),(708,76,TASKS[:4],"Generated tests: original four"),(1038,128,TASKS[4:],"Generated tests: complex two")]
    headers=["Intervals","TTL/LRU","DAG","Service","Optimizer","MVCC"]
    start,rowh=181,35
    for left,w,tasks,title in panels:
        c.text(left,118,title,17,INK,"bold")
        for i,t in enumerate(tasks):
            x=left+i*w
            c.text(x+w/2,145,headers[TASKS.index(t)],12,MUTED,"bold",anchor="middle")
    for row,(model,effort) in enumerate((m,e) for m in MODELS for e in EFFORTS):
        y=start+row*rowh
        if row in [0,6,12]:c.line([(42,y-8),(1330,y-8)],GRID,1.5)
        c.text(48,y+18,LABELS[model],14,COLORS[model],"bold")
        c.text(139,y+18,effort.title(),14,INK)
        group=[r for r in data["runs"] if r["model"]==model and r["reasoning_effort"]==effort]
        for pi,(left,w,tasks,title) in enumerate(panels):
            for i,t in enumerate(tasks):
                rows=[r for r in group if r["task"]==t]
                if pi==0:
                    value=mean(r["grading"]["score"] for r in rows)
                    coverage=2
                else:
                    values=[mutation[r["run_id"]] for r in rows if r["status"]=="completed" and mutation[r["run_id"]] is not None]
                    value=mean(values) if len(values)==2 else None
                    coverage=len(values)
                x=left+i*w
                c.rect(x,y-1,w-4,rowh-4,shade(value),radius=4)
                label="N/A" if value is None else f"{value:.0f}" if close(value,round(value)) else f"{value:.1f}"
                c.text(x+(w-4)/2,y+12,label,13,INK,"bold",anchor="middle")
                if pi>0:c.text(x+(w-4)/2,y+25,f"{coverage}/2",10,MUTED,anchor="middle")
    c.rect(42,847,1286,129,"#F2F6FA",radius=8)
    c.text(58,869,"Main score grades the saved code. It is separate from accepted delivery: the Sol 6 Max service timed out with a passing snapshot.",13,MUTED)
    c.text(58,891,"Mutation panels measure sensitivity to fixed seeded defects, not overall code quality; their detection rules differ (see scoring.md).",13,MUTED)
    c.text(58,913,"Mutation pair means require 2/2 usable completed candidates. No added tests = 0; invalid, inconclusive, or incomplete pairs = N/A.",13,MUTED)
    c.text(58,935,"The complex optimizer panel has substantial missing coverage, including positive-control timeouts. Compare coverage before comparing scores.",13,MUTED)
    c.text(58,957,"Manual maintainability / design review remains pending. No overall quality score or general intelligence ranking is claimed.",13,MUTED)
    c.save("quality",png)


def results_page(data,summaries,mutation,original,complex_tests):
    rows=[]
    for model in MODELS:
        for effort in EFFORTS:
            s=summaries[model,effort]
            rows.append(f"| {LABELS[model]} | {effort.title()} | {usd(s)} | {usd(s,s['accepted'])} | {s['model_seconds_mean']:.1f} | {s['accepted']}/12 | {s['quality_macro_mean']:.2f} |")
    mutation_rows=[]
    for model in MODELS:
        for effort in EFFORTS:
            group=[r for r in data['runs'] if r['model']==model and r['reasoning_effort']==effort and r['status']=='completed']
            cells=[]
            for t in TASKS:
                values=[mutation[r['run_id']] for r in group if r['task']==t and mutation[r['run_id']] is not None]
                cells.append(f"{mean(values):.1f}% (2/2)" if len(values)==2 else f"N/A ({len(values)}/2)")
            mutation_rows.append(f"| {LABELS[model]} {effort.title()} | "+" | ".join(cells)+" |")
    text='''# Results

**Sol 6.1 Low is the least expensive configuration with 12/12 accepted deliveries:** $0.101 API-equivalent USD and 161.7 seconds per attempt, averaged over all six tasks and both repetitions. Sol 6 Low was slightly faster (158.7 seconds) but one optimizer attempt missed a scale requirement.

The archive contains **216 attempts**, **214 completed deliveries**, and **213 accepted deliveries**. A delivery counts as accepted only when its status is `completed` **and** its main grade is accepted. All attempts, including failures, remain in the cost and time denominators. See the [methodology](methodology.md), [scoring rules](scoring.md), and [task catalogue](tasks.md).

## Cost and time

![API-equivalent cost versus elapsed agent time](../assets/cost-time.svg)

Cost is a counterfactual calculation from observed tokens at the **frozen 30 September 2026 Standard API rates** in [results.json](../results/results.json). It is not a Codex subscription invoice or a measurement of purchased credits. Elapsed time is the wall-clock agent time, including tool use; it is not the generated code's execution speed.

Each setting has **12 attempts**: six tasks × two repetitions. The connected points follow Low → Medium → High → Xhigh → Max → Ultra within each model. They do not imply interpolation or a monotonic scaling law. Upward arrows and dashed segments mark the two settings with incomplete cost telemetry.

![Cost and elapsed time by reasoning effort](../assets/effort-curves.svg)

### All 18 settings

`USD / accepted` divides the cost of **all attempted work** by the number of accepted deliveries. Main functional score is the macro mean of the six task scores, each averaged over two snapshots; it is a different measure from accepted delivery.

| Model | Effort | USD / attempt | USD / accepted | Agent seconds / attempt | Accepted | Main functional score % |
|---|---|---:|---:|---:|---:|---:|
'''+"\n".join(rows)+'''

`≥` indicates a conservative observed cost lower bound, rounded down. The complete cost of Sol 6 Max and Ultra is unknown because one attempt in each setting has partial token telemetry. The observed API-equivalent subtotal for the whole archive is **$87.5980432**; it is not a complete final total. Exact cost estimates are available for **214/216** attempts. The two complex tasks contribute **$41.8110868**, all with complete cost telemetry.

## What the quality evidence shows

![Functional scores and generated-test sensitivity with coverage](../assets/quality.svg)

The main checks are close to a ceiling: 15 of 18 settings achieved 12/12 accepted deliveries. Increasing effort adds substantial cost and time without separating most settings on these checks. Two repetitions per task are too few to estimate rare failures reliably, and six tasks cannot establish a general intelligence ranking. The reported means are descriptive; no confidence intervals or significance claims are inferred from this small sample.

The complex tasks make algorithmic and state reasoning explicit. The optimizer combines signed values, dependencies, asymmetric conflicts, multiple resource constraints, mandatory projects, and exact tie-breaking. The MVCC task checks snapshots, conservative serializability, savepoints retaining reads, phantom detection, ABA, write skew, and checkpoint/replay. All small exhaustive optimizer oracle checks and all MVCC main checks passed. The optimizer's scale test produced the one new functional failure.

Generated tests provide an additional, narrower signal: **Sol 6.1's MVCC tests detected all six fixed seeded defects in both repetitions at High, Xhigh, Max, and Ultra.** This measures test sensitivity for that task and defect set; it does not establish an overall quality or intelligence winner.

### Generated-test sensitivity and coverage

The table shows a pair mean **only when both repetitions are usable**, with usable coverage out of two. A partially covered pair shows N/A and its actual coverage (for example, `N/A (1/2)`). Only completed candidates enter this table. **N/A is missing evidence, not zero.** A zero score for no recognized added tests concerns this criterion alone. Scores on the original four tasks and the complex two use different frozen detection rules and defect sets; do not average the six columns into an overall quality number.

| Model / effort | Intervals | TTL/LRU | DAG | Service | Optimizer | MVCC |
|---|---:|---:|---:|---:|---:|---:|
'''+"\n".join(mutation_rows)+'''

Usable generated-test evidence is available for **175/214 completed candidates**, including **29** with no recognized additional test files. For the complex tasks, **55/72** scores are usable (including ten zero scores for no recognized added tests); **17** are unavailable. Of the 16 unavailable optimizer evaluations, 12 positive-control reference runs exceeded the 30-second limit, three had import/execution problems, and one mutant result was inconclusive. The remaining unavailable evaluation is Sol 6 Max MVCC repetition 2, whose tests failed the positive reference. These evaluator limitations do not prove a candidate implementation defect.

For the original four tasks, additional boundary/property checks passed **920/922 checks** across completed candidates. The two failures concern huge integer TTL values. Code runtime and peak Python allocation evidence in [quality-v2.1.json](../results/quality-v2.1.json) covers those original four tasks only. Manual maintainability, design, and broader code review remain pending; the archive does not support a completed overall code-quality score.

## Failures and diagnostic evidence

| Attempt | Outcome | Evidence and interpretation |
|---|---|---|
| [Sol 6 Low optimizer r1](../candidates/05-optimizer-6-sol-low-r1/) | Completed; rejected | `Planner.test_scale_positive_bound` exceeded the eight-second limit. Small exact-answer cases passed. |
| [Sol 6 Ultra intervals r1](../candidates/01-easy-6-sol-ultra-r1/) | Provider failure | The provider reported capacity failure; the saved stub scored zero. Cost telemetry is partial. |
| [Sol 6 Max service r2](../candidates/04-components-6-sol-max-r2/) | 40-minute timeout | The saved code passed main and expanded checks, but the delivery did not complete. Cost telemetry is partial. |
| [Sol 5.6 Low cache r2](../candidates/02-medium-5.6-sol-low-r2/) | Expanded-check defect | Main checks passed. A valid huge integer TTL with an integer clock raises `OverflowError` through `math.isfinite`. |
| [Sol 6 Low cache r1](../candidates/02-medium-6-sol-low-r1/) | Expanded-check defect | The same huge integer TTL boundary defect was exposed by the expanded suite. |

The [bounded optimizer replay](../results/scale-diagnostic.json) reproduced the Sol 6 Low r1 timeout (8.012 seconds). The reference completed in 0.196 seconds and paired r2 in 0.223 seconds. This replay used saved code, made no model calls, and **did not replace the original grades or benchmark timings**. The fixture has 31 affordable positive projects and one huge-value, individually unaffordable decoy, exposing the importance of a useful pruning bound.

## Explore and reproduce

- [Canonical results and per-attempt token/cost/grade records](../results/results.json)
- [Original-task expanded quality evidence](../results/quality-v2.1.json)
- [Complex-task generated-test evidence](../results/complex-test-strength.json)
- [Immutable candidate snapshots](../candidates/)
- [Frozen experiment provenance](../results/provenance.json)
- [Archive manifest](../results/archive-manifest.json)
- [Consolidated English PDF](../artifacts/Sol_Benchmark_Consolidated_EN.pdf)

Regenerate this page and the SVG charts from the archived data without network access or model calls:

```sh
python scripts/analyze_results.py
```

PNG previews are optional:

```sh
python -m pip install -r requirements-report.txt
python scripts/analyze_results.py --png
```

The script asserts 216 unique attempts, the full model/effort/task/repetition matrix, 12 attempts per setting, acceptance status, functional macro scores, observed mean times, and cost telemetry coverage before writing derived outputs. It reads the frozen archive and writes only this page and the three chart families under `assets/`. It does not rerun inference, modify saved candidates, change grades, or regenerate the archived PDF. SVG outputs are deterministic and do not depend on third-party packages; PNG rasterization uses local fonts and can vary slightly across platforms.
'''
    path=ROOT/"docs"/"results.md"
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(text,encoding="utf-8",newline="\n")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--png",action="store_true",help="Also write PNG previews (requires Pillow).")
    args=parser.parse_args()
    data,summaries,mutation,original,complex_tests=load()
    scatter(summaries,args.png)
    curves(summaries,args.png)
    quality(data,summaries,mutation,args.png)
    results_page(data,summaries,mutation,original,complex_tests)
    print("Validated 216 attempts / 18 settings; wrote docs/results.md and 3 SVG charts"+(" with PNG previews." if args.png else "."))


if __name__ == "__main__":
    main()
