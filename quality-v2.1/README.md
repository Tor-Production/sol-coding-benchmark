# Original four-task quality evaluation

The `suite.py`, `worker.py`, `mutants.py`, and `rubric.json` retain the original v2.1 evaluation criteria. The controller has been adapted to read the public archive at `results/results.json` and `results/candidate-manifests.json`, without original machine paths or credentials. The rubric's historical cohort description is retained as source evidence; the published archive contains 144 attempts for these four tasks, and the controller filters that cohort explicitly.

Original acceptance correctness weighs 30, expanded correctness 35, candidate-added test effectiveness 20, and evidenced manual review 15. Runtime and Python allocation memory are reported separately. The automated evidence score renormalizes 30:35:20 when all three components are available; no overall quality grade is produced without manual review. Group means require all eight original-task measurements. This formula does not apply to the optimizer or MVCC tasks.

Only candidate-added tests are assessed against the same 4/5/5/6 seeded reference defects per task. Skips, import failures, and timeouts are disclosed. No added tests score zero only for test effectiveness. The original four-task classifier allows a previously passing executed test to kill a mutant through an assertion failure or execution error; loader failures and timeouts are inconclusive. The later complex-task classifier counts assertion failures only. Keep these criteria separate when comparing test quality.

## Replay archived code locally

Python 3.14.0 matches the recorded interpreter. Replayed elapsed time and memory depend on the local host and are new observations; archived values remain unchanged. These commands do not contact Codex or run model inference:

```powershell
python -B quality-v2.1/run.py qualify
python -B quality-v2.1/run.py freeze
python -B quality-v2.1/run.py evaluate --run-id 02-medium-6-sol-low-r1
python -B quality-v2.1/run.py report
```

`qualify` checks the corrected positive references and detection of all 20 seeded defects. `freeze` validates public candidate hashes and binds the local replay to source/archive hashes, evaluator files, and Python. `evaluate` copies a candidate into temporary workspaces, runs expanded checks, the fixed runtime workload, and added-test mutation sensitivity. It writes fresh results under `analysis/quality-v2.1/`. It never overwrites `results/quality-v2.1.json` or candidate files. Omit `--run-id` to evaluate the entire original four-task cohort; local test timeouts can make that slow.

For a separate experiment, pass the same `--output analysis/my-replay` to every action. The output directory must be outside published `results/` and `candidates/`. A local freeze prevents reuse after criteria, archive data, or Python changes. `npm test` qualifies this evaluator and tests its scoring policy without needing the published candidates or Codex.
