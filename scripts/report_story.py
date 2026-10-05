# Executed within the renderer's verified data/chart namespace.
accepted_settings=[s for s in SUM if s['accepted']==12]
cheapest=min([s for s in accepted_settings if cost(s) is not None],key=cost)
fastest=min(accepted_settings,key=lambda s:s['model_seconds_mean'])
old_failures=[(q,t) for q in CODE_QROWS for t in q['extended']['tests'] if not t['passed']]
new_failures=[(r,t) for r in DATA['runs'] for t in r['grading']['acceptance']['tests'] if not t['passed']]
new_accepted=sum(r['grading']['accepted'] for r in DATA['runs'])
completed=[r for r in ROWS if r['status']=='completed']
own_completed=[own_score(r) for r in completed]
usable_own=sum(x is not None for x in own_completed)
no_own=sum(not (TS[r['run_id']]['test_files'] if r['run_id'] in TS else QBY[r['run_id']]['test_effectiveness']['files']) for r in completed)
code_passed=sum(q['extended']['passed'] for q in CODE_QROWS)
code_total=sum(q['extended']['count'] for q in CODE_QROWS)
new_usable=sum(q['score'] is not None for q in TS.values())
new_no_tests=sum(q['status']=='no_added_tests' for q in TS.values())
manual_count=sum(q.get('manual_review') is not None for q in QROWS)

exec((ROOT/'scripts/report_insight_story.py').read_text(encoding='utf-8'))
story += [
 p('Dollars, quality and delivery','h1'),p('Twelve attempts per setting. USD / accepted includes spending on unsuccessful attempts.'),
 table(['Model / effort','USD / attempt','Functional / 100','Accepted','USD / accepted','Mean time, s'],
 [[label(s),cost_display(s),fmt(s['quality_macro_mean'],2),f'{s["accepted"]}/12',
   usd(s['api_usd_total']/s['accepted']) if s['api_usd_total'] is not None else '>= '+usd_bound(s['api_usd_low_observed']/s['accepted'])+'*',
   fmt(s['model_seconds_mean'],1)] for s in SUM],
 [116,79,85,58,91,BODY-429],24),
 p('Sol 6 / Max has a 40-minute reservation-service timeout; its saved code passes the functional and expanded checks. Sol 6 / Ultra has a provider capacity failure on the interval task. Sol 6 / Low completes every attempt but misses one optimizer scale check. These are distinct outcomes.','small'),
 p('Displayed >= costs are observed lower bounds, rounded down. Accepted-cost totals and exact token estimates remain in the public data; incomplete final spending is not assigned zero.','small'),PageBreak(),
 p('Task-level dollars and quality','h1'),p('Each cell shows mean API USD above mean functional score / 100, across two repetitions.'),
 table(['Model / effort']+SHORT_TASKS,
 [[label(s)]+[task_cost_display(s,t['task'])+'<br/>'+fmt(t['quality_mean'],2)+' / 100' for t in s['tasks']] for s in SUM],
 [116]+[(BODY-116)/6]*6,30),
 p('Asterisks mark partial cost telemetry. These are saved-code functional grades; completed acceptance is reported separately. Task-level agent times appear in the secondary time comparison.','small'),PageBreak(),
 p('Complex reasoning: behavioral evidence','h1'),p('Every small optimizer oracle and every MVCC check passed. Only one structured optimizer scale check failed.'),
 table(['Model / effort','Opt V','Opt C','Opt O','Opt S','MV Sem','MV Iso','MV Rec','MV V'],
 [[label(s)]+[fmt(st.mean(r['grading']['acceptance']['categories'][cat]['score'] for r in group(s,task)),1)
    for task,cats in [(TASKS[4],['validation','constraints','optimality','scale']),(TASKS[5],['semantics','isolation','recovery','validation'])] for cat in cats] for s in SUM],
 [122]+[(BODY-122)/8]*8,23),
 p('Optimizer: validation 20%, constraints 25%, optimality 35%, scale 20%. MVCC: semantics 25%, isolation 35%, recovery 25%, validation 15%. Values are category percentages; subcases fail their containing check.','small'),
 p('The optimizer uses an independent exhaustive oracle on 64 seeded small problems per attempt, greedy traps, all tie rules and three structured scale fixtures. MVCC uses 480-step mixed transaction traces, 180-transaction replay traces, every checkpoint/log prefix, ABA, phantoms, write skew and failed-operation atomicity.','small'),
 p('<b>Scale failure:</b> Sol 6 / Low, optimizer repetition 1, exceeded 8 seconds on the positive-bound fixture: 31 affordable projects and one individually infeasible high-value decoy. Its task score is 93.33; the paired task mean is 96.67. All its exact small-case answers passed.','small'),
 p('A separate bounded replay reproduced the 8-second timeout. The positive control and Low repetition 2 completed the same fixture in about 0.20 and 0.22 seconds. The original grades were retained.','small'),
 p('These tasks distinguish tested correctness and scaling behavior. Near-perfect outcomes across most settings leave too little separation to claim which model is generally smarter.','small'),PageBreak(),
 p('Expanded correctness and found defects','h1'),p(f'The first four tasks retain the same quality-v2.1 checks and scores. Completed solutions pass {code_passed}/{code_total} expanded checks.'),
 table(['Model / effort']+TASK_NAMES[:4],
 [[label(s)]+[fmt(mean_complete([q['expanded_score'] for q in group(s,t,True)]),2) for t in TASKS[:4]] for s in QS],
 [130]+[(BODY-130)/4]*4,22),
 p('<b>Boundary defects:</b> Sol 5.6 / Low r2 and Sol 6 / Low r1 cache constructors fail on a finite integer TTL of 10**400 with an integer clock. Applying math.isfinite to that integer raises OverflowError. Their original acceptance tests passed.','small'),
 p('Expanded tests add 1,200 random unions, cache model traces, DAG failure graphs and concurrency barriers, plus reservation stock-model steps, races and persistence/error cases. Properties/boundaries/concurrency weigh 40/30/30, renormalized when a task lacks a category.','small'),
 p('N/A is incomplete completed-code coverage. The provider-failed stub and saved timeout code remain in raw evidence; they are excluded from completed-code means. The new tasks use their own frozen behavioral rubric, without retroactively changing old scores.','small'),PageBreak(),
 p('Do the added tests detect defects?','h1'),p('Per-task mutation sensitivity, averaged across two completed candidates. N/A shows usable-score coverage for that pair.' )]
own_rows=[]
for s in SUM:
    cells=[]
    for task in TASKS:
        pair=[r for r in group(s,task) if r['status']=='completed']; values=[own_score(r) for r in pair]
        coverage=sum(v is not None for v in values)
        cells.append(fmt(mean_complete(values),0)+'%' if coverage==2 else f'N/A ({coverage}/2)')
    own_rows.append([label(s)]+cells)
story += [table(['Model / effort']+SHORT_TASKS,own_rows,[116]+[(BODY-116)/6]*6,24),
 p(f'{usable_own}/{len(completed)} completed candidates have usable scores; {no_own} have no recognized added tests. On the complex tasks, {new_usable}/72 scores are usable and {new_no_tests} candidates have no recognized added tests.','small'),
 p('Discovery uses unittest-compatible tests/test*.py, excluding supplied test_public.py. Tests must pass on both candidate and positive control. No added tests score zero only for this criterion. Import errors, skips, timeouts and reference incompatibility are inconclusive.','small'),
 p('Optimizer test strength is unavailable for 16/36 candidates: twelve positive-control test runs hit the 30-second timeout, three encounter import/execution issues, and one mutant run is inconclusive. These evaluator limitations particularly affect Sol 6.1 optimizer coverage and do not establish a candidate-code defect.','small'),
 p('Fixed sets: 4 interval, 5 cache, 5 DAG, 6 service, 5 optimizer and 6 MVCC defects. The original four-task classifier detects an executed test failure or error after the reference passed; loader failures/timeouts are inconclusive. The complex-task classifier counts assertion failures only and treats execution errors/hangs as unavailable. Interpret sensitivity within each task.','small'),PageBreak(),
 p('Agent time: a secondary comparison','h1'),p('Time remains useful for workflow planning, separate from the primary money-versus-quality comparison. The top curve shows mean elapsed agent minutes; the table reports each task in seconds.'),
 Graphic(BODY,44,legend),Graphic(BODY,180,lambda c,w,h:effort_chart(c,w,h,'time')),
 table(['Model / effort']+SHORT_TASKS,[[label(s)]+[fmt(t['model_seconds_mean'],0) for t in s['tasks']] for s in SUM],[116]+[(BODY-116)/6]*6,21),
 p('Elapsed agent time includes reasoning, commands, self-tests and waits during the turn. Setup, snapshotting and external grading are excluded. Task limits are 10 / 20 / 40 / 40 / 40 / 40 minutes; interrupted durations remain included.','small'),PageBreak(),
 p('Generated-code runtime and allocation','h1'),p('Existing quality-v2.1 runtime / Python-allocation measurements for the first four tasks. Each cell is milliseconds / KiB.'),
 table(['Model / effort']+TASK_NAMES[:4],
 [[label(s)]+[(fmt(mean_complete([q['performance'].get('median_seconds') for q in group(s,t,True)])*1000,2)+' / '+fmt(mean_complete([q['performance'].get('peak_python_bytes') for q in group(s,t,True)])/1024,0))
   if mean_complete([q['performance'].get('median_seconds') for q in group(s,t,True)]) is not None and mean_complete([q['performance'].get('peak_python_bytes') for q in group(s,t,True)]) is not None else 'N/A' for t in TASKS[:4]] for s in QS],
 [130]+[(BODY-130)/4]*4,24),
 p('One warm-up, then the median of three sequential runs. Workloads: 30,000 intervals; cache capacity 1,000 with 1,000 writes and 3,000 reads; 1,500 DAG nodes; 40 reservation cycles. Allocation is a separate tracemalloc measurement, not process RSS or native/SQLite memory.','small'),
 p('These values describe execution after generation, separate from agent completion time. The complex tasks have their scored scale/trace checks; equivalent runtime and memory profiles were not collected for them. Small differences can reflect scheduling noise.','small'),PageBreak(),
 p('Code quality and design review','h1'),p('Behavior, test strength and execution efficiency are reported separately. Manual rubric scoring remains pending.'),
 table(['Evidence layer','Scope','Interpretation'],[
 ['Main functional score','All six tasks','Equal task weights; fixed category weights for the complex tasks.'],
 ['Expanded correctness','First four tasks','Frozen properties, boundaries and concurrency checks.'],
 ['Own-test strength','All six tasks','Sensitivity to fixed per-task contract mutations.'],
 ['Runtime / memory','First four tasks','Descriptive generated-code measurements, unscored.'],
 ['Manual design rubric','Pending','Clarity, design and failure maintenance, with source/line evidence.']],
 [126,94,BODY-220],42,0),p('Manual rubric','h2'),
 table(['Area','Evidence a reviewer should inspect'],[
 ['Clarity','Intent, names, coherent control flow, explanations and useful tests.'],
 ['Design','Interfaces, responsibilities, cohesion, duplication and contract fit.'],
 ['Failure maintenance','Error handling, cleanup, transactions, concurrency and safe changes.']],
 [126,BODY-126],38,0),
 p('The existing four-task quality formula uses 30% original correctness, 35% expanded correctness, 20% own-test strength and 15% manual review. Its normalized 85-point automatic evidence is not a full quality score. It is preserved for those tasks and is not extrapolated to the added tasks.','small'),
 p('Static line/function counts are review signals, not grades. Tests tied to private implementation details can be valid for the candidate while yielding inconclusive cross-implementation mutation evidence.','small'),PageBreak(),
 p('Method, token prices and reproducibility','h1'),
 p('Each attempt uses one turn, a fresh session/workspace, the exact requested model/effort and Standard speed. Task prompt hashes are identical within each task across all 36 attempts. SHA-256 manifests protect the prepared inputs and saved candidate code.'),
 table(['Model','Input','Cached input','Cache write','Output'],
 [[model(m)]+[usd(CFG['pricing'][m]['api_usd_per_million'][k],2) for k in ['input','cached_input','cache_write','output']] for m in MODELS],
 [130]+[(BODY-130)/4]*4,29,0),
 p('Rates are USD per one million tokens, frozen on 30 Sep 2026 and verified on 1 Oct. The same schedule is used throughout for comparability. This report does not assert a newly checked current price. Ultra is a verified Codex effort setting; the USD calculation does not imply an API Ultra option.','small'),
 p('Uncached input, cached reads, cache writes and output are priced without double counting. Reasoning tokens are part of output. Per-request input above 272,000 tokens applies the frozen 2x input/cache and 1.5x output multipliers. Hosted-tool fees and regional premiums are excluded.'),
 p(f'Exact token estimates: {AUDIT["exact_api_estimates"]}/216. Observed total is at least {usd_bound(AUDIT["observed_api_usd_subtotal"],2)}; two partial-usage attempts prevent a complete total. The two complex tasks have exact observed token cost {usd(AUDIT["new_api_usd_total"],2)} across 72 attempts.','small'),
 p(f'Secondary token-based credit estimate: {fmt(AUDIT["observed_credit_estimates_subtotal"],2)} observed credits, including partial usage. Shared account balance changes do not identify billed credits for an individual run.','small'),
 p(f'Pinned runtime: {escape(AUDIT["runtime"]["codex_version"])}; Node {escape(AUDIT["runtime"]["node_version"])}; {escape(AUDIT["runtime"]["python_version"])} on Windows. Shell/tool recovery is included in agent time. Read isolation is instruction-based; automated command scans are diagnostics.','small'),
 p('Sources and evidence','h2'),
 p('<link href="https://developers.openai.com/api/docs/models/gpt-5.6-sol">Sol 5.6 API rates</link> | <link href="https://developers.openai.com/api/docs/models/gpt-6-sol">Sol 6 API rates</link> | <link href="https://developers.openai.com/api/docs/models/gpt-6.1-sol">Sol 6.1 API rates</link><br/><link href="https://learn.chatgpt.com/docs/pricing">Codex credits</link> | <link href="https://learn.chatgpt.com/docs/app-server">Codex app-server</link>','small'),
 p('Public evidence: results/results.json, quality-v2.1.json, complex-test-strength.json, report-audit.json and provenance.json; candidates/ and per-attempt prompt/final evidence. The original local audit verified all 216 snapshots/settings/turns and price calculations. Raw RPC/session logs are not distributed. Six insight views and results/insights.json derive from the same measurements, with no new inference or rescoring.','small'),
 p('Public results SHA-256: '+sha(ROOT/'results/results.json')+'<br/>Original source identities and both prior reports are recorded in results/provenance.json. Current insight presentation: 5 Oct 2026.','small')]

out=ROOT/'output/pdf/Sol_Benchmark_Consolidated_EN.pdf'; out.parent.mkdir(parents=True,exist_ok=True)
doc=SimpleDocTemplate(str(out),pagesize=A4,leftMargin=40,rightMargin=40,topMargin=38,bottomMargin=47,
                     title='Sol coding benchmark: the cost of better tests and code',author='Tor Production')
doc.build(story,onFirstPage=footer,onLaterPages=footer)
reader=PdfReader(out); all_text='\n'.join(page.extract_text() or '' for page in reader.pages)
assert len(reader.pages) == 16, 'Inspect unexpected report pagination before publication'
assert not any('\u0400'<=ch<='\u04ff' for ch in all_text)
assert all(k in all_text for k in ['216','MVCC','optimizer','API-equivalent','Code quality'])
audit=dict(pdf=str(out),pdf_sha256=sha(out),pages=len(reader.pages),source_sha256=sha(ROOT/'results/results.json'),
 quality_sha256=sha(ROOT/'results/quality-v2.1.json'),strength_sha256=sha(ROOT/'results/complex-test-strength.json'),
 english_text_verified=True,page_text_lengths=[len(page.extract_text() or '') for page in reader.pages],report_date=REPORT_DATE,timezone='Europe/Kiev',
 primary_axes={'x':'API-equivalent USD per MVCC attempt','y':'MVCC generated-test mutation sensitivity (%)'},
 primary_quality_scope='Six fixed MVCC defects; pair means require 2/2 available scores',
 primary_attempts=36,primary_available_test_scores=35,insights_sha256=sha(ROOT/'results/insights.json'),
 evidence_views=['mvcc-test-value','mvcc-defect-matrix','dag-code-efficiency','effort-value','repeat-cost','pricing-bridge'],
 quality_scale=[0,100],partial_cost_policy='Two incomplete attempts retain lower bounds; arrows follow increasing USD',
 measured_data_changed=False,model_inference_calls=0)
(ROOT/'analysis').mkdir(exist_ok=True)
(ROOT/'analysis/pdf_audit.json').write_text(json.dumps(audit,indent=2)+'\n',encoding='utf-8')
print(json.dumps(audit,indent=2))
