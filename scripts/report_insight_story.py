# Main report narrative, executed in the verified report namespace.
facts = INSIGHTS['summary']
mvcc61 = [s for s in INSIGHTS['mvcc']['settings'] if s['model']=='gpt-6.1-sol']
dag61 = [s for s in INSIGHTS['dag']['settings'] if s['model']=='gpt-6.1-sol']
dc = facts['dag_sol61_low_ultra']
bc = INSIGHTS['pricing_bridge']
rc = INSIGHTS['repetition_cost']
def defect_pair(s):
    return ' / '.join('N/A' if r['killed_count'] is None else str(r['killed_count'])+'/6' for r in s['repetitions'])

story = [p('Sol coding benchmark','title'),p('What do we buy with more effort?','h1'),
 p('Six tasks. Sol 5.6, Sol 6 and Sol 6.1. Low, Medium, High, Xhigh, Max and Ultra. Two independent attempts per task, Standard speed.'),
 Graphic(BODY,105,cards),Spacer(1,7),p('Six stories in the evidence','h2'),
 p('<b>The cost of better tests:</b> on MVCC, Sol 6.1 Low tests detect 4/6 and 5/6 fixed defects; Medium detects 5/6 and 6/6 for 9.9% more task cost. High detects all six in both attempts at about $0.203 per MVCC attempt.'),
 p('<b>Which defects were anticipated:</b> the matrix preserves every MVCC repetition and distinguishes detection, missed defects, no recognized added tests and unavailable evaluation.'),
 p(f'<b>Implementation efficiency:</b> on the fixed DAG workload, Sol 6.1 Low to Ultra costs {fmt(dc["cost_multiple"])}x as much to generate. Execution time falls {fmt(dc["runtime_reduction_pct"],1)}% and peak Python allocation falls {fmt(dc["python_allocation_reduction_pct"],1)}%.'),
 p(f'<b>Effort premium:</b> Sol 6.1 Max costs {fmt(facts["sol61_max_low"]["cost_multiple"])}x Low across the suite. Both score 100 and deliver 12/12 accepted completions; added tests remain a separate evidence layer.'),
 p(f'<b>Repeat spending:</b> {rc["identical_main_pairs"]}/{rc["exact_pairs"]} exact-cost pairs have identical main grades, but {rc["over_1_5_pairs"]} cost more than 1.5x as much in one repetition.'),
 p(f'<b>Tariff and token usage:</b> Sol 6.1 costs {fmt(bc["reduction_pct"],1)}% less than Sol 6 on {bc["matched_n"]} matched attempts per model. Repricing both usage traces at the Sol 6.1 schedule leaves a {fmt(bc["common_rate_reduction_pct"],1)}% difference.'),
 p('Functional context: 16/18 settings score 100. The report leads with specific test, implementation and spending differences, each with its own definition. Manual code-design review remains pending.','small'),
 p('Money is a frozen Standard API token equivalent for observed Codex usage. It is not an invoice or a current-price quotation.','small'),PageBreak(),

 p('The cost of better tests','h1'),
 p('MVCC is the leading case: all 36 saved implementations pass main acceptance, while their generated tests differ in detecting six fixed defects. X is task USD; Y is generated-test mutation sensitivity.'),
 public_figure('mvcc-test-value',350),p('Sol 6.1 progression','h2'),
 table(['Effort','USD / MVCC attempt','Defects: r1 / r2','Pair sensitivity'],
       [[s['effort'],usd(s['mean_usd']),defect_pair(s),fmt(s['pair_test_score'],1)+'%'] for s in mvcc61],
       [75,144,147,BODY-366],23,0),
 p('Mean points require two available scores; both actual repetitions are also visible. Sol 6 Max has one unavailable evaluation and no pair mean. No added tests is zero for this criterion alone.','small'),
 p('A 100% score means detecting these six seeded defects. This task-specific measure does not combine overall code design, general intelligence or production reliability.','small'),PageBreak(),

 p('Which bugs did the model anticipate?','h1'),
 p('Each row is one MVCC candidate: 3 models x 6 efforts x 2 repetitions. The six columns identify the same controlled defects, preserving outcomes hidden by a pair average.'),
 public_figure('mvcc-defect-matrix',605),
 p('Detection requires an assertion failure against a mutant after candidate and positive-reference tests pass without skips. Errors/hangs are inconclusive. No added tests is explicit zero; unavailable is missing evidence.','small'),PageBreak(),

 p('Same correctness, different efficiency','h1'),
 p('The DAG case separates the cost of generation from the runtime and allocation of saved code. All candidates pass main correctness. Lower runtime and allocation are better for this fixed workload.'),
 public_figure('dag-code-efficiency',350),p('Sol 6.1 on the 1,500-task DAG workload','h2'),
 table(['Effort','Generation USD','Code runtime, ms','Peak Python, KiB'],
       [[s['effort'],usd(s['mean_usd']),fmt(s['mean_runtime_ms'],1),fmt(s['mean_peak_kib'],1)] for s in dag61],
       [75,125,145,BODY-345],23,0),
 p('Each candidate runtime is the median of three sequential archived samples after one warm-up; each setting averages its two candidate medians. Allocation averages two separate tracemalloc peaks and excludes native memory.','small'),
 p('Both Sol 6.1 repetitions are faster at Ultra than Low on this workload. Performance profiles cover the original four tasks only; comparable optimizer/MVCC profiles were not collected.','small'),PageBreak(),

 p('What does more effort buy?','h1'),
 p('Costs include all twelve attempts in each setting. Main functional grades and completed acceptance are shown beside money. Test sensitivity is a separate evidence layer.'),
 public_figure('effort-value',430),p('Sol 6.1 Low and Max: full-suite comparison','h2'),
 table(['Effort','USD / attempt','Main / 100','Accepted','Agent seconds'],
       [[e,cost_display(summary('gpt-6.1-sol',e)),fmt(summary('gpt-6.1-sol',e)['quality_macro_mean'],0),str(summary('gpt-6.1-sol',e)['accepted'])+'/12',fmt(summary('gpt-6.1-sol',e)['model_seconds_mean'],1)] for e in ['low','max']],
       [65,112,106,88,BODY-371],25,0),
 p('Low to Max is a 2.90x observed cost premium and 4.19x mean agent-time multiple. Equal main grades leave room for differences in added tests, runtime and design. Higher effort does not guarantee a higher measured score.','small'),
 p('Sol 6 Max and Ultra costs are incomplete, so bars show lower bounds. Sol 6.1 Ultra costs 14.59% less than Max in this archive; observed cost is not monotonically increasing with effort.','small'),PageBreak(),

 p('Same answer, different spending','h1'),
 p(f'Of 108 repetition pairs, {rc["exact_pairs"]} have two exact USD estimates and {rc["identical_main_pairs"]} of those have identical main grades. The chart shows observed pairs rather than estimated confidence intervals.'),
 public_figure('repeat-cost',470),
 p('Largest exact-cost ratio: Sol 6.1 / optimizer / Medium, $0.150302 versus $0.325499, or 2.166x. Both deliveries were accepted with a main score of 100.','small'),
 p('The two partial-cost pairs remain disclosed and archived. Token use, tools, recovery and service behavior can vary between runs. Two repetitions do not establish a long-run reliability or spending distribution.','small'),PageBreak(),

 p('Tariff or token spending?','h1'),
 p('The same 70 task/effort/repetition keys are compared for Sol 6 and Sol 6.1. The bridge first reprices Sol 6 usage at the frozen Sol 6.1 schedule, then compares observed usage at common prices.'),
 public_figure('pricing-bridge',340),
 table(['Scenario','USD total / 70 attempts'],[
       ['Sol 6 usage at Sol 6 rates',usd(bc['old_usd'],6)],
       ['Same Sol 6 usage at Sol 6.1 rates',usd(bc['repriced_usd'],6)],
       ['Sol 6.1 usage at Sol 6.1 rates',usd(bc['new_usd'],6)]],
       [BODY-160,160],31,0),
 p(f'In this order of repricing, the tariff step reduces USD by {usd(bc["tariff_reduction_usd"],6)}, and the usage step by {usd(bc["usage_reduction_usd"],6)}. Frozen cached-input rates are $0.20 versus $0.10 per million; other rates match.','small'),
 p('Two incomplete Sol 6 observations and their matching Sol 6.1 keys are excluded from this view only. All 216 attempts remain archived. This order-dependent counterfactual does not establish causal attribution or intelligence.','small'),PageBreak()]
