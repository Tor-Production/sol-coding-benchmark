# Limits of the conclusions

The archive supports a comparison of **specified Python programming workloads, USD estimates and end-to-end agent delivery**. It does not establish a general intelligence ranking. Most settings reach the main functional ceiling, so the tasks provide too little separation to say which model is universally smarter.

## Sample and workload limits

There are only two repetitions per model/effort/task combination. The report shows means, paired ratios and observed spread; it does not present confidence intervals, significance tests or an estimate of rare production failure rates. Repetitions are fresh runs but are not a randomized sample of all programming tasks. Model service load, tool behavior and host scheduling can affect time and token use.

The suite is six deliberately specified Python tasks using standard-library components. It covers transformation, state, concurrency, multiple interfaces, exact search and transactional invariants. It does not cover large existing repositories, unfamiliar dependency stacks, UI design, real deployments, long interactive debugging or other programming languages. A result on these tasks should not be presented as a universal developer-productivity result.

The optimizer's 64 oracle problems have 4–11 projects; its three larger fixtures are structured 32-project instances. Passing them does not prove practical performance on arbitrary NP-hard instances. MVCC tests use deterministic sequential call interleavings, not actual multithreaded execution. Its recovery checks logical exported-data replay, not physical disk durability.

## Quality evidence has different scopes

The original main contracts and checks are retained, with one disclosed reservation ambiguity policy applied consistently to inherited and new results. The original four tasks' expanded quality-v2.1 checks were added post-hoc and are exploratory. Candidates did not see them. The two complex tasks use their own behavioral categories and fixtures frozen before those runs; the old 30/35/20/15 composite is not extended to those tasks.

Mutation sensitivity concerns fixed per-task defect sets, with different historical/new detection rules documented in [scoring](scoring.md). A 100% result means all those mutants were detected, not that the tests detect every plausible bug. Reference portability and timeouts make some scores unavailable. Optimizer positive-control timeouts particularly reduce Sol 6.1's usable coverage; N/A cannot be interpreted as bad code or ignored to create a favorable mean. No recognized added tests is a zero only for this criterion.

Manual clarity, design and failure-maintenance review is pending. Static counts and attractive explanations do not replace source-based review. There is no complete overall code-quality score. Generated-code runtime/allocation profiles cover only the original four tasks, so an all-six-task performance or memory ranking would be unsupported.

## Failure types should remain distinct

| Archived outcome | Interpretation |
| --- | --- |
| Sol 6 / Low optimizer r1 exceeds an 8-second fixture limit | Candidate scaling failure on that declared fixture; small exact answers pass |
| Sol 6 / Ultra interval r1 fails because of provider capacity | Delivery/service outcome; not a demonstrated algorithmic error |
| Sol 6 / Max reservation r2 times out at 40 minutes | Unfinished delivery; saved code passes main and expanded checks |
| Two Low cache candidates fail huge-integer TTL expansion | A reproduced candidate boundary defect despite passing original main tests |

All remain in the archive. No successful inference rerun replaces a failure. The separate optimizer replay was a bounded diagnostic, not a new benchmark observation.

## Cost and time are measured proxies

USD is a counterfactual Standard API token estimate from Codex usage under a historical frozen schedule. It is not actual billed money, an API benchmark run or a guarantee of current prices. Token estimates omit hosted-tool fees and regional premiums. Two partial observations prevent a complete experiment cost total. Shared account balances cannot attribute billed credits to one run.

Agent time includes model reasoning, commands, self-tests and waits during the turn. It is not pure inference latency. Limits can censor completion time: the 40-minute timeout gives an observed interrupted duration, not the time the agent would eventually have needed. Generated-code runtime is measured separately; allocation is Python `tracemalloc`, not total process or native memory.

## Isolation, audit and publication

Fresh workspaces and sessions reduce carry-over. Network is disabled for candidates, and instructions forbid parent directories, other attempts and evaluators. **Read isolation is instruction-based, not a filesystem security boundary.** Command scans found no flagged outside reads in the audited evidence, but scans cannot prove that every possible access was impossible or visible.

SHA-256 manifests establish file identity and detect later changes. They do not independently prove that a model generated a file or that every semantic requirement was tested. The [provenance record](provenance.md) preserves the measured source identities and explains the public export.

Now that task contracts, hidden evaluators, controls and candidate outputs are public, future exposure or training contamination is possible. A new run of this public suite can still be useful, but should be labeled as a fresh public-task experiment rather than assumed to have the same blindness as the archived collection. Changing tasks, rates, rubric or runtime requires a new documented experiment; archived outcomes should remain intact.
