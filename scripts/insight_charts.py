#!/usr/bin/env python3
"""Draw six descriptive benchmark findings from frozen derived records.

SVG uses the existing standard-library Canvas. PNG previews require Pillow.
No figure recalculates a score, changes an observation, or runs a model.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter

from analyze_results import (
    Canvas, ROOT, MODELS, EFFORTS, LABELS, COLORS, INK, MUTED, GRID, PAPER,
    model_legend, effort_legend,
)


def pale(color, fraction=.30):
    """Mix a model color with white; actual repetitions stay visually secondary."""
    rgb = [int(color[i:i+2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(255+(v-255)*fraction):02X}" for v in rgb)


def ordered(rows):
    return sorted(rows, key=lambda r: (MODELS.index(r['model']), EFFORTS.index(r['effort'])))


def actual(c, x, y, rep, color, r=4):
    """Unshifted repeated observations; circle r1, square r2."""
    color = pale(color, .48)
    if rep == 1:
        c.circle(x, y, r, PAPER, color, 1.4)
    else:
        c.rect(x-r, y-r, r*2, r*2, PAPER, color)


def actual_legend(c, y, x=55):
    actual(c, x+5, y-5, 1, MUTED, 4)
    c.text(x+18, y, 'r1', 14, MUTED)
    actual(c, x+53, y-5, 2, MUTED, 4)
    c.text(x+66, y, 'r2: individual attempts; filled effort shapes are pair means', 14, MUTED)


def grid(c, left, top, right, bottom, xmax, ymax, xticks, yticks, xfmt, yfmt):
    px = lambda v: left+v/xmax*(right-left)
    py = lambda v: bottom-v/ymax*(bottom-top)
    for value in yticks:
        y = py(value)
        c.line([(left,y),(right,y)], GRID)
        c.text(left-13,y+5,yfmt(value),15,MUTED,anchor='end')
    for value in xticks:
        x=px(value)
        c.line([(x,top),(x,bottom)], GRID)
        c.text(x,bottom+25,xfmt(value),15,MUTED,anchor='middle')
    return px, py


def mvcc_value(insights, png):
    rows=ordered(insights['mvcc']['settings'])
    c=Canvas(1200,850,'The cost of better MVCC tests',
        'X is frozen API-equivalent dollars per MVCC attempt. Y is the percentage of six fixed injected defects detected by candidate-written tests. The scale is zero to one hundred. Hollow points show both actual repetitions; filled effort shapes and connected lines show pair means only when both scores are available. Sol 6 Max has one usable score of two and no pair mean. No-added-tests observations have explicit numerical zero sensitivity.')
    c.text(54,44,'The cost of tests that detect MVCC defects',28,INK,'bold')
    c.text(54,75,'One complex task, six fixed injected defects, two independent attempts per setting',17,MUTED)
    model_legend(c,111)
    effort_legend(c,111)
    actual_legend(c,145)
    left,top,right,bottom=94,201,1130,601
    px,py=grid(c,left,top,right,bottom,.9,108,[0,.15,.3,.45,.6,.75,.9],list(range(0,101,20)),lambda x:f'${x:.2f}',lambda y:f'{y}%')
    c.text(left,top-19,'Fixed-defect mutation sensitivity (full 0-100% scale)',16,INK,'bold')
    c.text((left+right)/2,bottom+55,'Frozen API-equivalent USD / MVCC attempt',17,INK,'bold',anchor='middle')
    for model in MODELS:
        settings=[r for r in rows if r['model']==model]
        for a,b in zip(settings,settings[1:]):
            if a['pair_test_score'] is not None and b['pair_test_score'] is not None:
                c.line([(px(a['mean_usd']),py(a['pair_test_score'])),(px(b['mean_usd']),py(b['pair_test_score']))],COLORS[model],2.6)
        for row in settings:
            for rep in row['repetitions']:
                if rep['score'] is not None:
                    actual(c,px(rep['usd']),py(rep['score']),rep['repetition'],COLORS[model],5)
        for row in settings:
            if row['pair_test_score'] is not None:
                c.marker(px(row['mean_usd']),py(row['pair_test_score']),row['effort'],COLORS[model],7)
    low=next(r for r in rows if r['model']=='gpt-6.1-sol' and r['effort']=='low')
    medium=next(r for r in rows if r['model']=='gpt-6.1-sol' and r['effort']=='medium')
    high=next(r for r in rows if r['model']=='gpt-6.1-sol' and r['effort']=='high')
    c.line([(px(low['mean_usd']),py(low['pair_test_score'])+10),(246,421),(499,421)],COLORS['gpt-6.1-sol'],1.3)
    c.text(259,408,f"Sol 6.1 Low: ${low['mean_usd']:.3f} - 9/12 defects",16,COLORS['gpt-6.1-sol'],'bold')
    c.line([(px(medium['mean_usd'])+10,py(medium['pair_test_score'])),(466,299),(741,299)],COLORS['gpt-6.1-sol'],1.3)
    c.text(478,285,f"Medium: ${medium['mean_usd']:.3f} - 11/12",16,COLORS['gpt-6.1-sol'],'bold')
    c.line([(px(high['mean_usd']),py(high['pair_test_score'])-10),(392,208),(661,208)],COLORS['gpt-6.1-sol'],1.3)
    c.text(405,195,f"High: ${high['mean_usd']:.3f} - 12/12",16,COLORS['gpt-6.1-sol'],'bold')
    c.text(54,689,'Sol 6 Max: N/A as a pair (1/2 usable). The r1 observation remains visible; no mean is imputed.',16,MUTED)
    c.rect(54,713,1092,105,'#F2F6FA',radius=9)
    pct=(medium['mean_usd']/low['mean_usd']-1)*100
    c.text(74,742,f'Sol 6.1 Low -> Medium: {pct:.1f}% more money; 9 -> 11 of 12 fixed-defect checks detected.',18,INK,'bold')
    c.text(74,770,'Upper-left is preferable for this measured testing criterion. Lines follow effort order.',16,MUTED)
    c.text(74,796,'These 36 observations assess candidate-written tests on MVCC, not overall intelligence or code quality.',15,MUTED)
    c.save('mvcc-test-value',png)


def mvcc_matrix(insights,png):
    data=insights['mvcc']; rows=ordered(data['settings']); defects=data['defects']
    c=Canvas(1200,1380,'Which MVCC defects did candidate-written tests detect?',
        'All thirty-six MVCC attempts and all six fixed injected defects. Detected means an executed assertion failure on a mutant with passing candidate and reference controls. Missed means the valid tests passed the mutant. No-added-tests is a scored zero with no submitted tests. Unavailable is an unusable evaluation and has no numerical score. Sol 6 Max repetition two is unavailable. Colors and cell labels distinguish all four states.')
    c.text(54,44,'Which MVCC defects did the tests detect?',28,INK,'bold')
    c.text(54,75,'Every attempt is shown; each column is one frozen contract mutation',17,MUTED)
    styles={'detected':('#128C7E','yes',PAPER),'missed':('#F5DBD5','no',INK),'no_added_tests':('#E5EAF0','none',MUTED),'unavailable':('#596C80','N/A',PAPER)}
    legend=[('detected','Detected'),('missed','Missed'),('no_added_tests','No added tests'),('unavailable','Unavailable')]
    for i,(state,label) in enumerate(legend):
        x=54+i*280;fill,text,fg=styles[state]
        c.rect(x,96,35,25,fill,radius=3);c.text(x+17.5,114,text,12,fg,'bold','middle');c.text(x+46,114,label,16,INK)
    start,colw=369,128
    c.text(54,164,'Model / effort / attempt',17,INK,'bold')
    c.text(321,164,'Found',17,INK,'bold','middle')
    headers=[('Snapshot','reads'),('Write','skew'),('Phantom','reads'),('Rollback','reads'),('Recovery', ''),('Checkpoint','aliasing')]
    for i,(a,b) in enumerate(headers):
        x=start+i*colw+colw/2
        c.text(x,151,a,16,INK,'bold','middle')
        if b:c.text(x,174,b,16,INK,'bold','middle')
    y0,rowh=190,29
    counter=0
    for setting in rows:
        for rep in sorted(setting['repetitions'],key=lambda r:r['repetition']):
            y=y0+counter*rowh
            if counter%12==0:c.line([(54,y-3),(1137,y-3)],COLORS[setting['model']],2)
            c.rect(54,y,283,rowh-2,'#F5F8FB' if counter%2==0 else PAPER)
            c.rect(54,y,4,rowh-2,COLORS[setting['model']])
            label=f"{LABELS[setting['model']]} {setting['effort'].title()} - r{rep['repetition']}"
            c.text(65,y+20,label,16.5,INK)
            count='N/A' if rep['killed_count'] is None else f"{rep['killed_count']}/6"
            c.text(321,y+20,count,16,MUTED,'normal','middle')
            for i,defect in enumerate(defects):
                state=rep['defect_states'][defect['id']]; fill,text,fg=styles[state]
                x=start+i*colw
                c.rect(x+3,y,colw-6,rowh-2,fill,radius=2)
                c.text(x+colw/2,y+20,text,16,fg,'bold','middle')
            counter+=1
    assert counter==36
    c.rect(54,1262,1092,92,'#F2F6FA',radius=8)
    c.text(72,1289,'Detected: candidate tests pass the implementation and positive control, then fail a mutant by assertion.',15,MUTED)
    c.text(72,1313,'No added tests: numerical zero sensitivity. Unavailable: no numerical score; it is never replaced by zero.',15,MUTED)
    c.text(72,1337,'Six fixed defects are a limited testing sample. Two attempts per setting show observations, not confidence intervals.',15,MUTED)
    c.save('mvcc-defect-matrix',png)


def dag_efficiency(insights,png):
    rows=ordered(insights['dag']['settings'])
    c=Canvas(1200,890,'Same functional correctness, different DAG execution efficiency',
        'Two cost-versus-performance panels for all eighteen model and effort settings on the fixed 1500-task dependency-chain workload with three worker limit. X is API-equivalent generation cost per DAG attempt. Y is generated-code median runtime in milliseconds in the first panel and peak Python allocation in KiB in the second. Hollow points are candidate repetitions, filled points are means of two candidates. All DAG main scores are one hundred. This is generated-code performance, not model latency.')
    c.text(54,44,'Correct DAG code can have different execution efficiency',26,INK,'bold')
    c.text(54,75,'Fixed workload: 1,500-task dependency chain; three worker limit',17,MUTED)
    model_legend(c,110);effort_legend(c,110);actual_legend(c,145)
    for i,(field,repfield,ymax,ticks,label) in enumerate([
        ('mean_runtime_ms','runtime_ms',100,[0,20,40,60,80,100],'Generated-code runtime (ms) - lower is better'),
        ('mean_peak_kib','peak_kib',1200,[0,300,600,900,1200],'Peak Python allocation (KiB) - lower is better'),
    ]):
        left=92+i*574;right=left+459;top,bottom=219,598
        px,py=grid(c,left,top,right,bottom,.65,ymax,[0,.15,.3,.45,.6],ticks,lambda x:f'${x:.2f}',lambda y:f'{y:g}')
        c.text(left,190,label,16,INK,'bold')
        c.text((left+right)/2,650,'API-equivalent USD / DAG attempt',16,INK,'bold',anchor='middle')
        for model in MODELS:
            group=[r for r in rows if r['model']==model]
            for a,b in zip(group,group[1:]):
                c.line([(px(a['mean_usd']),py(a[field])),(px(b['mean_usd']),py(b[field]))],COLORS[model],2.3)
            for row in group:
                for rep in row['repetitions']:actual(c,px(rep['usd']),py(rep[repfield]),rep['repetition'],COLORS[model],3.5)
            for row in group:c.marker(px(row['mean_usd']),py(row[field]),row['effort'],COLORS[model],5.5)
        low=next(r for r in rows if r['model']=='gpt-6.1-sol' and r['effort']=='low')
        ultra=next(r for r in rows if r['model']=='gpt-6.1-sol' and r['effort']=='ultra')
        if i==0:
            for row,name,dy in [(low,'Low',-17),(ultra,'Ultra',22)]:
                c.text(px(row['mean_usd'])+9,py(row[field])+dy,name,14,COLORS['gpt-6.1-sol'],'bold')
    low=next(r for r in rows if r['model']=='gpt-6.1-sol' and r['effort']=='low')
    ultra=next(r for r in rows if r['model']=='gpt-6.1-sol' and r['effort']=='ultra')
    c.rect(54,699,1092,164,'#F2F6FA',radius=8)
    c.text(73,730,f"Sol 6.1 Low -> Ultra: {ultra['mean_usd']/low['mean_usd']:.2f}x generation cost; all four main grades are 100/100.",18,INK,'bold')
    c.text(73,762,f"Runtime: {low['mean_runtime_ms']:.1f} -> {ultra['mean_runtime_ms']:.1f} ms. Peak Python allocation: {low['mean_peak_kib']:.1f} -> {ultra['mean_peak_kib']:.1f} KiB.",17,COLORS['gpt-6.1-sol'],'bold')
    c.text(73,796,'Runtime uses the median of three timings per candidate, then the mean of two candidate medians.',15,MUTED)
    c.text(73,821,'Memory is tracemalloc peak Python allocation, not total process memory. One workload on one machine.',15,MUTED)
    c.text(73,846,'Both repeated candidates support the runtime direction; no general performance ranking follows from this task.',15,MUTED)
    c.save('dag-code-efficiency',png)


def effort_value(insights,png):
    rows=ordered(insights['effort']['settings'])
    c=Canvas(1200,890,'What does more effort buy across the six tasks?',
        'All eighteen model and effort settings. Horizontal bars show mean frozen API-equivalent dollars per attempt across six tasks and two repetitions. A rightward arrow marks each observed cost lower bound where complete cost is unknown. Columns show accepted completed delivery count of twelve, the main functional macro score out of one hundred, and secondary agent generation time in seconds. Different quality criteria are not combined into a new aggregate score.')
    c.text(54,44,'What does more effort buy across the six tasks?',28,INK,'bold')
    c.text(54,75,'Cost first; delivery, main functional score, and agent generation time kept as separate measures',16,MUTED)
    model_legend(c,111)
    left,right=259,731;y0,rowh=178,30
    px=lambda v:left+v/1.2*(right-left)
    c.text(left,151,'API-equivalent USD / attempt',16,INK,'bold')
    for x,title in [(875,'Accepted'),(978,'Main /100'),(1100,'Agent sec')]:c.text(x,151,title,16,INK,'bold','middle')
    for val in [0,.3,.6,.9,1.2]:
        x=px(val);c.line([(x,y0-7),(x,y0+18*rowh+1)],GRID);c.text(x,y0+18*rowh+27,f'${val:.2f}',15,MUTED,anchor='middle')
    for i,row in enumerate(rows):
        y=y0+i*rowh
        if i%6==0:c.line([(54,y-2),(1146,y-2)],COLORS[row['model']],1.5)
        c.text(54,y+20,f"{LABELS[row['model']]} {row['effort'].title()}",17,INK)
        bound=row['mean_usd'] is None
        value=row['mean_usd_low'] if bound else row['mean_usd']
        c.rect(left,y+4,px(value)-left,18,COLORS[row['model']],radius=3)
        if bound:c.arrow(px(value),y+13,COLORS[row['model']],'right')
        label=('>=$' + f'{math.floor(value*1000)/1000:.3f}') if bound else f'${value:.3f}'
        c.text(839,y+20,label,16,COLORS[row['model']],'bold','end')
        c.text(886,y+20,f"{row['accepted']}/12",17,INK,'bold','middle')
        c.text(991,y+20,f"{row['main_macro']:.2f}",17,INK,'normal','middle')
        c.text(1103,y+20,f"{row['mean_agent_seconds']:.0f}",17,MUTED,'normal','middle')
    low=next(r for r in rows if r['model']=='gpt-6.1-sol' and r['effort']=='low')
    maxrow=next(r for r in rows if r['model']=='gpt-6.1-sol' and r['effort']=='max')
    c.rect(54,776,1092,88,'#F2F6FA',radius=8)
    c.text(73,805,f"Sol 6.1 Max costs {maxrow['mean_usd']/low['mean_usd']:.2f}x Low; both have 12/12 accepted deliveries and 100/100 main score.",17,INK,'bold')
    c.text(73,830,'The MVCC and DAG figures examine additional measured properties when the main functional score is at its ceiling.',15,MUTED)
    c.text(73,851,'>=$ and rightward arrows denote observed lower bounds. Agent seconds measure generation time, not code runtime.',14,MUTED)
    c.save('effort-value',png)


def repeat_cost(insights,png):
    data=insights['repetition_cost'];pairs=data['pairs']
    exact=[p for p in pairs if p['ratio'] is not None]
    highlighted=sorted((p for p in exact if p['ratio']>1.5),key=lambda p:(-p['ratio'],p['model'],p['effort'],p['task']))
    partial=data['excluded_pairs']
    c=Canvas(1200,1080,'Same functional score, different repeated spending',
        'The top panel counts all one hundred six pairs with complete API-equivalent cost in five ratio bins. The bottom dumbbells show every one of the twenty pairs whose expensive-to-cheap cost ratio exceeds one point five; each endpoint is an actual repetition and is labeled with its cost. All one hundred eight repeated task pairs are accounted for, including two incomplete-cost pairs listed separately. There are only two repetitions per setting, so no confidence intervals are asserted.')
    c.text(54,44,'The same functional score can cost different amounts',27,INK,'bold')
    c.text(54,75,f"{data['identical_main_pairs']}/{data['exact_pairs']} complete-cost pairs have identical main grades; {data['over_1_5_pairs']} cost ratios exceed 1.5x",17,MUTED)
    categories=[('1.00-1.10x',lambda r:r<=1.1),('1.10-1.25x',lambda r:1.1<r<=1.25),('1.25-1.50x',lambda r:1.25<r<=1.5),('1.50-2.00x',lambda r:1.5<r<=2),('>2.00x',lambda r:r>2)]
    counts=[sum(test(p['ratio']) for p in exact) for _,test in categories]
    assert sum(counts)==data['exact_pairs']
    c.text(54,120,'All complete-cost pairs: larger / smaller actual repetition cost',17,INK,'bold')
    base,top,maxcount=307,150,max(50,max(counts))
    for i,((label,_),count) in enumerate(zip(categories,counts)):
        x=140+i*210;h=count/maxcount*(base-top)
        color='#D75B3B' if i>=3 else '#536F8B'
        c.rect(x,base-h,132,h,color,radius=4)
        c.text(x+66,base-h-10,str(count),18,color,'bold','middle')
        c.text(x+66,base+26,label,15,MUTED,'normal','middle')
    c.text(54,372,'Every pair above 1.5x - endpoints are actual costs, sorted by ratio',18,INK,'bold')
    # Leave endpoint labels a clear gutter before the fixed ratio column.
    left,right=410,915
    xmax=max(rep['usd'] for pair in highlighted for rep in pair['runs'])
    xmax=math.ceil(xmax*10)/10+.1
    px=lambda v:left+v/xmax*(right-left)
    y0,rh=412,25
    for value in [i*xmax/4 for i in range(5)]:
        x=px(value);c.line([(x,y0-20),(x,y0+(len(highlighted)-1)*rh+14)],GRID)
        c.text(x,y0-29,f'${value:.2f}',14,MUTED,anchor='middle')
    c.text(1023,385,'Ratio',15,INK,'bold','middle')
    c.text(1110,385,'Main grade',15,INK,'bold','middle')
    tasknames={'01-easy':'Intervals','02-medium':'TTL','03-hard':'DAG','04-components':'SQLite','05-optimizer':'Optimizer','06-transactions':'MVCC'}
    for i,pair in enumerate(highlighted):
        y=y0+i*rh
        r1,r2=sorted(pair['runs'],key=lambda r:r['repetition'])
        color=COLORS[pair['model']]
        c.text(54,y+5,f"{LABELS[pair['model']]} {pair['effort'].title()} / {tasknames[pair['task']]}",16,INK)
        c.line([(px(r1['usd']),y),(px(r2['usd']),y)],color,2.5)
        c.circle(px(r1['usd']),y,4.5,color,PAPER,1)
        c.rect(px(r2['usd'])-4.5,y-4.5,9,9,color,PAPER)
        cheapest,expensive=sorted([r1,r2],key=lambda r:r['usd'])
        c.text(px(cheapest['usd'])-8,y+5,f"${cheapest['usd']:.3f}",13,MUTED,anchor='end')
        c.text(px(expensive['usd'])+8,y+5,f"${expensive['usd']:.3f}",13,MUTED)
        c.text(1023,y+5,f"{pair['ratio']:.3f}x",15,color,'bold','middle')
        same=r1['main_score']==r2['main_score']
        score=f"{r1['main_score']:g}" if same else f"{r1['main_score']:.1f}/{r2['main_score']:.0f}"
        c.text(1110,y+5,score,16,INK,'normal','middle')
    c.rect(54,944,1092,112,'#F2F6FA',radius=8)
    c.text(73,971,'Two pairs have incomplete cost, so no exact spending ratio is calculated:',16,INK,'bold')
    for i,pair in enumerate(partial):
        c.text(73,996+i*22,f"{LABELS[pair['model']]} {pair['effort'].title()} / {tasknames[pair['task']]} - recorded observations remain in the archive.",15,MUTED)
    c.text(73,1043,'Circles: r1. Squares: r2. Two observations describe this sample; no confidence intervals or forecast are claimed.',14,MUTED)
    c.save('repeat-cost',png)


def pricing_bridge(insights,png):
    data=insights['pricing_bridge']
    c=Canvas(1200,835,'Cheaper model pricing or less spending at common prices?',
        'Three aggregate API-equivalent cost bars for seventy matched completed task repetitions: Sol 6 at its own frozen prices, the recorded Sol 6 usage repriced at Sol 6.1 prices, and Sol 6.1 at its own prices. The counterfactual separates the tariff change from a common-price usage difference. These estimates are not Codex bills, and the decomposition is descriptive rather than a causal experiment.')
    c.text(54,44,'Cheaper pricing, different token spending, or both?',28,INK,'bold')
    c.text(54,75,f"{data['matched_n']} matched task / effort / repetition observations - frozen API-equivalent USD",17,MUTED)
    left,top,right,bottom=100,163,1136,596
    ymax=24
    py=lambda v:bottom-v/ymax*(bottom-top)
    for value in [0,5,10,15,20]:
        y=py(value);c.line([(left,y),(right,y)],GRID);c.text(left-13,y+5,f'${value}',15,MUTED,anchor='end')
    c.text(left,132,'Aggregate API-equivalent USD for the same matched sample',16,INK,'bold')
    bars=[(256,data['old_usd'],COLORS['gpt-6-sol'],('Sol 6 usage','at Sol 6 prices')),(605,data['repriced_usd'],pale(COLORS['gpt-6-sol'],.56),('Sol 6 usage','at Sol 6.1 prices')),(954,data['new_usd'],COLORS['gpt-6.1-sol'],('Sol 6.1 usage','at Sol 6.1 prices'))]
    for x,value,color,labels in bars:
        y=py(value);c.rect(x-103,y,206,bottom-y,color,radius=5)
        c.text(x,y-18,f'${value:.5f}',24,color,'bold','middle')
        for j,label in enumerate(labels):c.text(x,bottom+31+j*24,label,18,INK,'bold' if j==0 else 'normal','middle')
    # First change tariffs on the recorded Sol 6 tokens, then compare recorded
    # usage at common Sol 6.1 prices. This is not a causal model experiment.
    tariff=data['tariff_reduction_usd']
    usage=data['usage_reduction_usd']
    c.text(427,115,f'Price difference: -${tariff:.3f}',15,MUTED,'bold','middle')
    c.text(835,115,f'Common-price difference: -${usage:.3f}',15,MUTED,'bold','middle')
    c.rect(54,689,1092,120,'#F2F6FA',radius=8)
    c.text(73,720,f"Observed combined reduction: {data['reduction_pct']:.1f}% (${data['old_usd']-data['new_usd']:.3f}) for these 70 matched observations.",19,INK,'bold')
    common_pct=data['common_rate_reduction_pct']
    c.text(73,749,f"At common Sol 6.1 prices, recorded Sol 6.1 usage costs {common_pct:.1f}% less. The tariff step saves ${tariff:.3f}.",16,MUTED)
    c.text(73,777,'Counterfactual repricing changes the tariff only. It does not simulate how a model would behave at another price.',15,MUTED)
    c.text(73,799,'Cost estimates include recorded input, cached-input and output tokens. They are not actual Codex billing charges.',15,MUTED)
    c.save('pricing-bridge',png)


def generate(insights,png=False):
    """Write the six deterministic SVG figure families (and optional PNGs)."""
    for function in (mvcc_value,mvcc_matrix,dag_efficiency,effort_value,repeat_cost,pricing_bridge):
        function(insights,png)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--png',action='store_true',help='Also render PNG previews with Pillow.')
    args=parser.parse_args()
    records=json.loads((ROOT/'results'/'insights.json').read_text(encoding='utf-8'))
    generate(records,png=args.png)


if __name__=='__main__':
    main()
