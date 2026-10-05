#!/usr/bin/env python3
"""Build the current English report from the public archive; no inference."""
from pathlib import Path
import hashlib, json, math, statistics as st
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab import rl_config
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, PageBreak, Flowable
from pypdf import PdfReader
ROOT = Path(__file__).resolve().parents[1]
rl_config.invariant = 1  # Stable PDF metadata on the same fonts/runtime.
def read(p): return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
PUBLIC = read(ROOT/'results/results.json')
PROVENANCE = read(ROOT/'results/provenance.json')
QUALITY = read(ROOT/'results/quality-v2.1.json')
STRENGTH = read(ROOT/'results/complex-test-strength.json')
INSIGHTS = read(ROOT/'results/insights.json')
for filename, expected in INSIGHTS['source_sha256'].items():
    assert sha(ROOT/filename) == expected, 'Insight source changed: ' + filename
AUDIT = read(ROOT/'results/report-audit.json')
assert AUDIT['verified']
assert AUDIT['source_json_sha256'] == PROVENANCE['original_consolidated_results_sha256']
assert AUDIT['quality_json_sha256'] == PROVENANCE['original_quality_results_sha256']
assert AUDIT['strength_json_sha256'] == PROVENANCE['original_complex_strength_sha256']
for rel, expected in read(ROOT/'results/archive-manifest.json').items():
    assert sha(ROOT/rel) == expected, 'Published archive changed: '+rel
ROWS = PUBLIC['runs']
DATA = {'runs':[r for r in ROWS if r['task'] in ['05-optimizer','06-transactions']]}
assert len(ROWS)==216 and len(DATA['runs'])==72 and len(STRENGTH['evaluations'])==72
assert all(r['status'] not in ['pending','initializing','running'] for r in ROWS)
REPORT_DATE = '05 Oct 2026'
SUM = PUBLIC['summary']
CFG = PUBLIC
MODELS = PUBLIC['models']
LEVELS = PUBLIC['reasoning_efforts']
LETTERS = dict(zip(LEVELS,['L','M','H','X','Max','U']))
TASKS = [t['id'] for t in PUBLIC['tasks']]
TASK_NAMES = ['Intervals','TTL/LRU cache','Concurrent DAG','SQLite service','Exact optimizer','MVCC store']
SHORT_TASKS = ['Intervals','Cache','DAG','Service','Optimizer','MVCC']
BYID = {r['run_id']:r for r in ROWS}
QROWS = QUALITY['runs']
CODE_QROWS = [q for q in QROWS if BYID[q['run_id']]['status']=='completed']
TS = {q['run_id']:q for q in STRENGTH['evaluations']}
QBY = {q['run_id']:q for q in QROWS}
QS = []
for raw in QUALITY['summary']:
    g=[q for q in CODE_QROWS if q['model']==raw['model'] and q['reasoning_effort']==raw['reasoning_effort']]
    s=dict(raw)
    for k in ['original_score','expanded_score','automated_evidence_score']:
        v=[q[k] for q in g]; s[k]=st.mean(v) if len(v)==8 and all(x is not None for x in v) else None
    QS.append(s)

def model(m): return 'Sol '+m.removeprefix('gpt-').removesuffix('-sol')
def label(s): return model(s['model'])+' / '+s['reasoning_effort']
def summary(m,e): return next(s for s in SUM if s['model']==m and s['reasoning_effort']==e)
def qsummary(m,e): return next(s for s in QS if s['model']==m and s['reasoning_effort']==e)
def group(s,task=None,quality=False):
    return [r for r in (CODE_QROWS if quality else ROWS) if r['model']==s['model'] and r['reasoning_effort']==s['reasoning_effort'] and (task is None or r['task']==task)]
def mean_complete(xs,n=2): return st.mean(xs) if len(xs)==n and all(x is not None for x in xs) else None
def cost(s): return None if s['api_usd_total'] is None else s['api_usd_total']/12
def cost_display(s): return usd(cost(s)) if cost(s) is not None else '>= '+usd_bound(s['api_usd_low_observed']/12)+'*'
def task_cost_display(s,task):
    rr=group(s,task); value=mean_complete([r['costs']['api_usd_estimated'] for r in rr])
    return usd(value) if value is not None else '>= '+usd_bound(sum(r['costs'].get('api_usd_low') or 0 for r in rr)/len(rr))+'*'
def own_score(r):
    if r['run_id'] in TS: return TS[r['run_id']]['score']
    return QBY[r['run_id']]['test_effectiveness']['score']
W, H = A4
BODY = W - 80
NAVY, INK, MUTED, PALE, RULE = [colors.HexColor(c) for c in ['#14283D', '#24364B', '#596B7D', '#F1F5F8', '#D9E2E9']]
PALETTE = dict(zip(MODELS, [colors.HexColor('#D75B3B'), colors.HexColor('#3478BE'), colors.HexColor('#128C7E')]))
# Use the recorded Windows font where available; portable core-font fallback.
font_path = Path('C:/Windows/Fonts/segoeui.ttf')
bold_path = Path('C:/Windows/Fonts/segoeuib.ttf')
if font_path.exists() and bold_path.exists():
    pdfmetrics.registerFont(TTFont('Segoe', str(font_path)))
    pdfmetrics.registerFont(TTFont('SegoeBold', str(bold_path)))
else:
    pdfmetrics.registerFont(pdfmetrics.Font('Segoe', 'Helvetica', 'WinAnsiEncoding'))
    pdfmetrics.registerFont(pdfmetrics.Font('SegoeBold', 'Helvetica-Bold', 'WinAnsiEncoding'))
pdfmetrics.registerFontFamily('Segoe', normal='Segoe', bold='SegoeBold', italic='Segoe', boldItalic='SegoeBold')
STYLES = {
    'title': ParagraphStyle('title', fontName='SegoeBold', fontSize=27, leading=32, textColor=NAVY, spaceAfter=10),
    'h1': ParagraphStyle('h1', fontName='SegoeBold', fontSize=22, leading=27, textColor=NAVY, spaceAfter=8),
    'h2': ParagraphStyle('h2', fontName='SegoeBold', fontSize=12.5, leading=16, textColor=NAVY, spaceAfter=5, spaceBefore=9),
    'body': ParagraphStyle('body', fontName='Segoe', fontSize=9.6, leading=13.5, textColor=INK, spaceAfter=7),
    'small': ParagraphStyle('small', fontName='Segoe', fontSize=8.2, leading=11.3, textColor=MUTED, spaceAfter=6),
    'cell': ParagraphStyle('cell', fontName='Segoe', fontSize=8.2, leading=10.5, textColor=INK),
    'th': ParagraphStyle('th', fontName='SegoeBold', fontSize=8.0, leading=10.2, textColor=colors.white),
}
def p(s, style='body'):
    return Paragraph(s, STYLES[style])
def fmt(n, d=2):
    return 'N/A' if n is None or not math.isfinite(n) else f'{n:,.{d}f}'
def usd(n, d=3):
    return 'N/A' if n is None else '$' + fmt(n, d)
def usd_bound(n, d=3):
    return usd(math.floor(n * 10**d) / 10**d, d)
def model(m):
    return 'Sol ' + m.removeprefix('gpt-').removesuffix('-sol')
def label(s):
    return model(s['model']) + ' / ' + s['reasoning_effort']
def summary(m, e):
    return next(s for s in SUM if s['model'] == m and s['reasoning_effort'] == e)
def qsummary(m, e):
    return next(s for s in QS if s['model'] == m and s['reasoning_effort'] == e)
def group(s, task=None, quality=False):
    return [r for r in (CODE_QROWS if quality else ROWS) if r['model'] == s['model'] and r['reasoning_effort'] == s['reasoning_effort'] and (task is None or r['task'] == task)]
def mean_complete(xs, n=2):
    return st.mean(xs) if len(xs) == n and all(x is not None for x in xs) else None
def cost(s):
    return None if s['api_usd_total'] is None else s['api_usd_total'] / 12
def cost_display(s):
    return usd(cost(s)) if cost(s) is not None else '\u2265 ' + usd_bound(s['api_usd_low_observed']/12) + '*'
def task_cost(s, task):
    return next(t for t in s['tasks'] if t['task'] == task)['api_usd_mean']
def task_cost_display(s, task):
    value = task_cost(s, task)
    rr = group(s, task)
    return usd(value) if value is not None else '\u2265 ' + usd_bound(sum(r['costs'].get('api_usd_low') or 0 for r in rr)/len(rr)) + '*'
def table(headers, rows, widths, height=22, group_size=6):
    t = Table([[p(escape(str(h)), 'th') for h in headers]] + [[p(str(v), 'cell') for v in row] for row in rows],
              colWidths=widths, rowHeights=[31] + [height] * len(rows), repeatRows=1)
    commands = [('BACKGROUND', (0, 0), (-1, 0), NAVY), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                ('LINEBELOW', (0, -1), (-1, -1), .6, RULE)]
    for i in range(1, len(rows) + 1):
        if i % 2:
            commands.append(('BACKGROUND', (0, i), (-1, i), PALE))
        if group_size and i > 1 and (i - 1) % group_size == 0:
            commands.append(('LINEABOVE', (0, i), (-1, i), .8, RULE))
    t.setStyle(TableStyle(commands))
    return t
class Graphic(Flowable):
    def __init__(self, width, height, fn):
        super().__init__()
        self.width, self.height, self.fn = width, height, fn
    def draw(self):
        self.fn(self.canv, self.width, self.height)

def public_figure(name, max_height=None):
    """Draw a generated public SVG as PDF vectors, using its exact geometry."""
    root = ET.parse(ROOT / 'assets' / (name + '.svg')).getroot()
    _, _, source_width, source_height = map(float, root.attrib['viewBox'].split())
    scale = min(BODY / source_width, max_height / source_height if max_height else BODY / source_width)
    offset = (BODY - source_width * scale) / 2
    height = source_height * scale
    def draw(c, width, height):
        def point(x, y): return offset + float(x) * scale, height - float(y) * scale
        for node in root:
            kind = node.tag.rsplit('}', 1)[-1]
            a = node.attrib
            if kind in ['title', 'desc']: continue
            fill, stroke = a.get('fill', 'none'), a.get('stroke', 'none')
            c.setLineWidth(float(a.get('stroke-width', 1)) * scale)
            c.setDash([float(v) * scale for v in a.get('stroke-dasharray', '').split()])
            if fill != 'none': c.setFillColor(colors.HexColor(fill))
            if stroke != 'none': c.setStrokeColor(colors.HexColor(stroke))
            if kind == 'rect':
                x, y = point(a['x'], float(a['y']) + float(a['height']))
                c.roundRect(x, y, float(a['width'])*scale, float(a['height'])*scale,
                            float(a.get('rx', 0))*scale, fill=fill!='none', stroke=stroke!='none')
            elif kind == 'circle':
                x, y = point(a['cx'], a['cy'])
                c.circle(x, y, float(a['r'])*scale, fill=fill!='none', stroke=stroke!='none')
            elif kind in ['polygon', 'polyline']:
                points = [point(*p.split(',')) for p in a['points'].split()]
                path = c.beginPath(); path.moveTo(*points[0])
                for p in points[1:]: path.lineTo(*p)
                if kind == 'polygon': path.close()
                c.drawPath(path, fill=fill!='none', stroke=stroke!='none')
            elif kind == 'text':
                x, y = point(a['x'], a['y'])
                text(c, x, y, node.text or '', float(a['font-size'])*scale,
                     colors.HexColor(fill), a.get('font-weight')=='bold',
                     {'start':'left','middle':'center','end':'right'}[a.get('text-anchor','start')])
            else: raise ValueError('Unsupported public SVG element: ' + kind)
        c.setDash()
    return Graphic(BODY, height, draw)
def text(c, x, y, s, size=8.5, color=INK, bold=False, align='left'):
    c.setFillColor(color)
    c.setFont('SegoeBold' if bold else 'Segoe', size)
    {'left': c.drawString, 'center': c.drawCentredString, 'right': c.drawRightString}[align](x, y, str(s))
def nice_max(value, steps=5):
    raw = max(value, 1e-6) / steps
    power = 10 ** math.floor(math.log10(raw))
    step = next(v * power for v in [1, 2, 2.5, 5, 10] if v * power >= raw)
    return math.ceil(value / step) * step, step
def ticks(limit, step):
    return [i * step for i in range(round(limit / step) + 1)]
def tick_label(x, dollar=False):
    s = f'{x:g}'
    return '$' + s if dollar else s
def marker(c, x, y, level, color, radius=3.8):
    c.setStrokeColor(color); c.setFillColor(color); c.setLineWidth(1.1)
    if level == 'low': c.circle(x,y,radius,fill=1,stroke=1)
    elif level == 'medium': c.rect(x-radius,y-radius,2*radius,2*radius,fill=1,stroke=1)
    elif level == 'max':
        c.setLineWidth(2.3); c.line(x-radius*1.2,y,x+radius*1.2,y); c.line(x,y-radius*1.2,x,y+radius*1.2)
    else:
        q=c.beginPath()
        if level == 'high': points=[(x,y+radius*1.35),(x-radius*1.25,y-radius),(x+radius*1.25,y-radius)]
        elif level == 'xhigh': points=[(x,y+radius*1.3),(x+radius*1.3,y),(x,y-radius*1.3),(x-radius*1.3,y)]
        else: points=[(x+math.cos(math.pi/2+i*math.pi/5)*radius*(1.4 if i%2==0 else .58),y+math.sin(math.pi/2+i*math.pi/5)*radius*(1.4 if i%2==0 else .58)) for i in range(10)]
        q.moveTo(*points[0])
        for point in points[1:]: q.lineTo(*point)
        q.close(); c.drawPath(q,fill=1,stroke=1)

def legend(c, width, height):
    for i, m in enumerate(MODELS):
        x = 14 + i * width / 3
        c.setStrokeColor(PALETTE[m]); c.setLineWidth(1.7); c.line(x, height-10, x+18, height-10)
        text(c, x+25, height-13, model(m), 8.6)
    for i, e in enumerate(LEVELS):
        x = 14 + i * width / 6
        marker(c, x, height-30, e, MUTED, 3)
        text(c, x+9, height-33, LETTERS[e] + ': ' + e, 7.4)
def scatter(c, width, height, task=None, small=False, measure='functional'):
    pts=[]
    for s in SUM:
        rr=group(s,task)
        t=None if task is None else next(t for t in s['tasks'] if t['task']==task)
        x=cost(s) if task is None else t['api_usd_mean']
        bound=x is None
        if bound: x=sum(r['costs'].get('api_usd_low') or 0 for r in rr)/len(rr)
        y=(s['quality_macro_mean'] if t is None else t['quality_mean']) if measure=='functional' else 100*sum(r['main_accepted_and_completed'] for r in rr)/len(rr)
        assert 0<=y<=100 and x>=0
        pts.append((s['model'],s['reasoning_effort'],x,y,bound))
    left,right,bottom,top=48,width-12,38,height-18
    xmax,step=nice_max(max(v[2] for v in pts)*1.20,4)
    sx=lambda x:left+x/xmax*(right-left)
    sy=lambda y:bottom+y/110*(top-bottom)
    c.setStrokeColor(RULE); c.setLineWidth(.5)
    for v in ticks(xmax,step):
        x=sx(v); c.line(x,bottom,x,top); text(c,x,bottom-14,tick_label(v,True),7.2 if small else 8,MUTED,align='center')
    for v in [0,25,50,75,100]:
        y=sy(v); c.line(left,y,right,y); text(c,left-7,y-2,str(v),7.5,MUTED,align='right')
    text(c,(left+right)/2,7,'API-equivalent USD / attempt',7.7 if small else 8.5,align='center')
    c.saveState(); c.translate(10,(bottom+top)/2); c.rotate(90)
    text(c,0,0,'Main functional / 100' if measure=='functional' else 'Accepted deliveries (%)',7.7 if small else 8.5,align='center'); c.restoreState()
    for m in MODELS:
        points=[next(v for v in pts if v[0]==m and v[1]==e) for e in LEVELS]
        c.setStrokeColor(PALETTE[m]); c.setLineWidth(1.5)
        for a,b in zip(points,points[1:]):
            c.setDash(3,2) if a[4] or b[4] else c.setDash()
            c.line(sx(a[2]),sy(a[3]),sx(b[2]),sy(b[3]))
        c.setDash()
        for v in points:
            x,y=sx(v[2]),sy(v[3]); marker(c,x,y,v[1],PALETTE[m],2.8 if small else 3.8)
            if v[4]:
                c.setStrokeColor(PALETTE[m]); c.setLineWidth(1.2)
                c.line(x+7,y,x+21,y); c.line(x+21,y,x+17,y+3); c.line(x+21,y,x+17,y-3)
                text(c,x+8,y+7,'*',8,PALETTE[m],True)
            if v[3]<99.9:
                name=model(m)+' '+LETTERS[v[1]]+('*' if v[4] else '') if not small else LETTERS[v[1]]+('*' if v[4] else '')
                dx=6 if x<right-95 else -90
                dy=-16 if v[1]=='low' else -28 if v[1]=='ultra' else -12
                text(c,x+dx,y+dy,name,6.8 if small else 8,PALETTE[m],True)
    if task is None:
        v=next(v for v in pts if v[0]=='gpt-6.1-sol' and v[1]=='low')
        x,y=sx(v[2]),sy(v[3]); c.setStrokeColor(PALETTE[v[0]]); c.line(x+3,y+3,x+37,y+21)
        text(c,x+40,y+18,'Sol 6.1 Low',8,PALETTE[v[0]],True)
    text(c,right,top+6,'Full score scale: 0-100',6.8,MUTED,align='right')

def effort_chart(c, width, height, metric='cost'):
    left,right,bottom,top=43,width-14,30,height-16
    def value(s):
        if metric=='cost': return cost(s) if cost(s) is not None else s['api_usd_low_observed']/12
        return s['quality_macro_mean'] if metric=='quality' else s['model_seconds_mean']/60
    vals=[value(s) for s in SUM]
    maximum,step=(110,25) if metric=='quality' else nice_max(max(vals)*1.1,4)
    sy=lambda v:bottom+v/maximum*(top-bottom)
    sx=lambda i:left+(i+.1)/5.2*(right-left)
    c.setStrokeColor(RULE); c.setLineWidth(.5)
    for v in ([0,25,50,75,100] if metric=='quality' else ticks(maximum,step)):
        y=sy(v); c.line(left,y,right,y); text(c,left-7,y-2,tick_label(v,metric=='cost'),8,MUTED,align='right')
    for i,e in enumerate(LEVELS): text(c,sx(i),bottom-15,e,8.5,align='center')
    for m in MODELS:
        points=[value(summary(m,e)) for e in LEVELS]
        c.setStrokeColor(PALETTE[m]); c.setLineWidth(1.7)
        for i in range(5):
            c.setDash(3,2) if metric=='cost' and (cost(summary(m,LEVELS[i])) is None or cost(summary(m,LEVELS[i+1])) is None) else c.setDash()
            c.line(sx(i),sy(points[i]),sx(i+1),sy(points[i+1]))
        c.setDash()
        for i,v in enumerate(points):
            marker(c,sx(i),sy(v),LEVELS[i],PALETTE[m])
            if metric=='cost' and cost(summary(m,LEVELS[i])) is None:
                x,y=sx(i),sy(v); c.line(x,y+6,x,y+17); c.line(x,y+17,x-3,y+13); c.line(x,y+17,x+3,y+13)
                text(c,x+6,y+8,'*',8,PALETTE[m],True)
    caption={'cost':'USD per attempt','quality':'Main functional score / 100','time':'Elapsed time per attempt (minutes)'}[metric]
    text(c,left,height-2,caption,8,MUTED)

def cards(c,width,height):
    metrics = [('ATTEMPTS', '216', '3 models x 6 efforts x 6 tasks x 2 repeats'),
               ('API-EQUIVALENT TOTAL', usd(None,2) if None is not None else '\u2265 '+usd_bound(AUDIT['observed_api_usd_subtotal'],2), 'Observed token price; \u2265 marks a lower bound'),
               ('ACCEPTED', str(AUDIT['accepted']) + '/216', 'Completion and full functional acceptance')]
    gap=9; cw=(width-gap*2)/3
    for i,(a,b,d) in enumerate(metrics):
        x=i*(cw+gap); c.setFillColor(PALE); c.roundRect(x,4,cw,height-8,6,fill=1,stroke=0)
        text(c,x+11,height-22,a,7.4,MUTED,True); text(c,x+11,height-49,b,22,NAVY,True)
        # Short wrapped card details use normal Paragraph layout.
        q=p(d,'small'); q.wrap(cw-22,40); q.drawOn(c,x+11,11)
def footer(c,doc):
    c.setStrokeColor(RULE); c.line(40,35,W-40,35)
    text(c,40,22,'Sol coding benchmark | Consolidated report | ' + REPORT_DATE,7.8,MUTED)
    text(c,W-40,22,str(doc.page),8,MUTED,align='right')


for s in SUM:
    assert s['expected'] == 12
    if s['api_usd_total'] is not None:
        assert math.isclose(cost(s), s['api_usd_total']/s['expected']), 'USD denominator mismatch'
        assert math.isclose(cost(s), st.mean(r['costs']['api_usd_estimated'] for r in group(s))), 'USD row mean mismatch'

# Report contents share this verified data/chart namespace.
exec((ROOT/'scripts/report_story.py').read_text(encoding='utf-8'))
