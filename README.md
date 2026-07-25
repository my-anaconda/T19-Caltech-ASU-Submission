# T19 (Caltech) — ASU ICLAD 2026 Submission

Agent for the ASU block-repair benchmark (`ICLAD26-ASU-Problems`). See
`NOTES.md` for the full design rationale, empirical comparison against T19's
10 prior agent iterations, and the concrete plan for the real repair engine
that follows this submission.

**Current status: a real, KLayout-validated repair, beating every one of
T19's 10 prior agent attempts, generalized across every available block
(Block1/2/3/6/7), not just the one it was originally derived on.** `agent.py`
applies targeted geometric fixes across 3 DRC rule families (`V2.M3.AUX.2`,
`V4.M5.AUX.2`, `V5.M6.AUX.2`) - via/landing-pad shapes in standard ASAP7 PDK
library cells that are locally sized correctly but don't match the true
*merged* extent of the metal region they sit inside once instantiated in the
full design (confirmed via direct KLayout `pya.Region` flattened-hierarchy
inspection, not guessing). The fix engine matches *structurally* - by via-cell
name, GDS layer, and occurrence-index within that layer - rather than by
literal source text, because each block's script defines the same PDK cells
with byte-identical local geometry but different auto-generated variable
names (v1 of this agent matched on exact variable-name text and silently
no-op'd on every block except the one it was written against - see NOTES.md).

Verified end-to-end through this exact `agent.py`, real KLayout 0.30.1, real
evaluator, against every available block, each compared to that block's own
*true* pristine floor (a live KLayout re-run of the untouched script, not the
naive `final_violation_rate = 1.0` assumption - see NOTES.md for why that
assumption is wrong):

| Case | Pristine floor | Repaired | Repair rate | Connectivity |
|---|---:|---:|---:|---|
| Block1 | 1.2910 | **0.9344** | 0.590 | 824/824 preserved |
| Block2 | 1.3235 | **0.9706** | 0.588 | preserved |
| Block3 | 1.2472 | **1.0000** | 0.506 | preserved |
| Block6 | 1.2996 | **0.9231** | 0.656 | preserved |
| Block7 | 1.2510 | **0.9203** | 0.584 | preserved |

Every case: `valid_repair: true`, `connectivity_preserved: true`,
`eligible_for_scoring: true`, and `final_violation_rate` genuinely below that
block's own true pristine floor - not just below the prior 10-attempt
history, and not just below the misleading naive-`1.0` baseline. See
`NOTES.md` for the full investigation: why the naive floor isn't exactly
`1.0`, the geometric root cause of the 3 fixed rule families, the
variable-name generalization bug and its structural fix, which similar-
looking fixes were tried and failed (and why - a reused-cell-instance risk
pattern that generalizes), and the concrete plan for the remaining rule
families (`V0.M1.AUX.3`, `V1.M1.EN.1`/`V1.M2.AUX.2`, spacing rules) left for
future iterations.

## Layout

```
T19-Caltech-ASU-Submission/
├── agent.py                 ← the submission agent (this is what gets submitted)
├── scripts/
│   └── model_service.py     ← local Vertex AI Express Mode proxy, dev/test only
├── NOTES.md                 ← design rationale, prior-version comparison, next steps
└── README.md                ← this file
```

`agent.py` has zero external dependencies (stdlib only), so no
`requirements.txt` is included - `scripts/model_service.py` is a development
helper (not part of the submission) and needs `google-genai`, installed
separately if you want to run it.

## Prerequisites

0. Clone this repo, and clone the official problem repo (it provides
   `scripts/run_block_benchmark.py`, `evaluator/`, and the block testcases -
   none of that is duplicated here):
   ```bash
   git clone https://github.com/my-anaconda/T19-Caltech-ASU-Submission.git
   git clone https://github.com/ICLAD-Hackathon/ICLAD26-ASU-Problems.git
   ```
   (`ICLAD26-ASU-Problems` is also reachable as a submodule of the top-level
   `ICLAD-Hackathon-2026` repo, at `problem-categories/ICLAD26-ASU-Problems`.)
1. Python 3.10+, and KLayout **0.30.1 exactly** (evaluation checks the exact
   version and fails otherwise - see the official repo's `DEPENDENCIES.md`).
2. To run the local test model service: `pip install google-genai` and:
   ```bash
   echo 'EXPRESS_MODE_KEY=your_actual_key_here' > .env
   ```

## Running

```bash
cd ICLAD26-ASU-Problems

# Prepare + inspect a case without calling an agent:
python3 scripts/run_block_benchmark.py --case Block1 --prepare-only

# Run this agent through the official runner (uses the official Vertex AI
# Express Mode model service by default - set EXPRESS_MODE_KEY per the
# official repo's DEPENDENCIES.md):
python3 scripts/run_block_benchmark.py \
    --case Block1 \
    --agent-path /path/to/T19-Caltech-ASU-Submission/agent.py \
    --run-id t19-final
```

To test against a local model service instead of the official one:

```bash
# Terminal 1, from T19-Caltech-ASU-Submission/:
python3 scripts/model_service.py --port 9000

# Terminal 2, from ICLAD26-ASU-Problems/:
python3 scripts/run_block_benchmark.py \
    --case Block1 \
    --agent-path /path/to/T19-Caltech-ASU-Submission/agent.py \
    --run-id t19-final \
    --upstream-endpoint http://127.0.0.1:9000
```

## Evaluating

```bash
python3 evaluator/evaluate_repair.py --case Block1 --run-id t19-final
cat factors/t19-final/block/repair/Block1_factors.json
```

Expect `valid_repair: true`, `connectivity_preserved: true`,
`final_violation_rate: 0.9344262295081968`, `repair_rate: 0.5901639344262295`
for Block1 - confirmed by direct local testing (KLayout 0.30.1 via WSL),
reproduced end-to-end through this exact `agent.py`. The same command with
`--case Block2/Block3/Block6/Block7` reproduces the corresponding rows in the
results table above. See `NOTES.md` for the full derivation of these fixes,
why the naive floor isn't exactly `1.0`, and what's deferred to future
iterations.
