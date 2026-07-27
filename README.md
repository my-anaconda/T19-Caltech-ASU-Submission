# T19 (Caltech) — ASU ICLAD 2026 Submission

Agent for the ASU block-repair benchmark (`ICLAD26-ASU-Problems`). See
`NOTES.md` for the full design rationale, empirical comparison against T19's
10 prior agent iterations, and the concrete plan for the real repair engine
that follows this submission.

**Current status: a real, KLayout-validated repair, beating every one of
T19's 10 prior agent attempts, generalized across every available block
(Block1/2/3/4/5/6/7 - including Block4/Block5, released as this session's
hidden test cases), not just the one it was originally derived on.** `agent.py`
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
`V2.M3.AUX.2` specifically is fixed **merge-aware and per-via**: rather than
growing every via to one fixed target, each via's growth is computed against
the true (possibly non-rectangular, stepped) merged extent of the metal
region at its own location, asymmetrically if needed - this fully resolves
`V2.M3.AUX.2` (0 remaining, every block) instead of trading it for collateral
containment/enclosure violations. See NOTES.md's "Merge-aware, per-via
shape-aware V2.M3.AUX.2" section.

On top of that, `agent.py` also fixes `M4.AUX.1`/`M4.AUX.2` (M4 grid-alignment)
violations: `VIA_VIA45_1_2_58_58` (M4↔M5 via) and `VIA_VIA34_1_2_58_52`
(M3↔M4 via) are always co-located at the identical placement vector so their
M4 pads merge into one shape; shifting *both* instances together to whichever
neighboring 24nm grid line satisfies `asap7.lydrc`'s own `offgrid_cl(:y, 192,
48, 96)` condition exactly fixes the grid violation for every off-grid row
(not just a subset) - confirmed by real KLayout re-run per row, not assumed.
See NOTES.md's "M4 grid alignment" sections for the full derivation,
including a shift that looked clean in isolation but broke 4 other rules
until its co-located pair was moved too.

A further cascade (`V2.M3.AUX.2`'s fix can, in turn, require growing a
nearby M1/M2 rail cell's V1 taps - `V1.M2.AUX.2`, one metal layer down) is
also fixed, after three rounds of real-KLayout-validated refinement: growing
the shared rail's M1 pad as one whole rectangle (spanning the entire row)
broke connectivity outright by silently merging into unrelated standard-cell
M1 shapes elsewhere in the row. The shipped version instead grows only the
specific via that needs it, via a small **local** M1 patch, and checks THREE
independent safety constraints before growing anything: the M2 merge
topology, nearby foreign M1 shapes (including non-rectangular ones - real
standard-cell M1 routing isn't always a simple rectangle), and any V0
contact sitting flush against the default M1 edge (moving that edge away
from a flush V0 breaks `V0.M1.AUX.3`, the same "must exactly match" rule
family one layer further down - confirmed via KLayout GUI inspection).
See NOTES.md's "`V1.M2.AUX.2` cascade, take 2: local patches" section for
the full derivation, including two intermediate versions that were tried and
found wanting via real KLayout DRC/connectivity re-runs each time.

All of the above is fully deterministic - the model is never consulted.
`M4.S.5` ("parallel run length" spacing) is the one rule fixed **hybrid**:
candidate generation and safety-checking (does an edit collide with any
other top-level M4 shape? does it touch a shared, high-reuse via macro
instead of a safely-editable single instance?) are pure stdlib arithmetic,
same as everywhere else, but *which* candidate to apply - or whether to
reject all of them - is a genuine model call whose answer actually reaches
the shipped script, not a discarded "for planning only" analysis. A bad or
unparseable model response is re-validated against the real candidate list
and degrades to a safe no-op, never an unsafe edit. See NOTES.md's `M4.S.5`
section for the full derivation, including why 3 of Block1's 4 instances are
deliberately left untouched (their limiting edge belongs to `VIA_VIA34`/
`VIA_VIA45`, instantiated 50/21 times in Block1 alone - the same high-reuse
blast-radius class that made blind `VIA_VIA12` edits catastrophic elsewhere
in this file).

Back to fully deterministic: `M5.AUX.1` (M5 vertical edges must land on a
24nm grid, the same rule family as `M4.AUX.1`) is fixed by growing a
violating rail's edge OUTWARD to the nearest grid line - safe by
construction because it only ever grows (never shrinks past what it
already encloses), gated by a static "stub check" (does any other shape
on the same layer extend past the rail's own range at the edge being
moved?) built on the same nested-instance flattening machinery the
`V1.M2.AUX.2`/`V2.M3.AUX.2` fixes already use. No model call - the stub
check already IS the safety verification, there's no ambiguous choice to
make. This one had a real, cross-block-caught near-miss of its own: an
early version also touched `M6.AUX.1` (the same mechanism, one layer up),
which looked harmless on Block1 (a 1-for-1 trade against a new
`V5.M6.AUX.2` instance) but turned out to be a net +12 regression on
Block7 once actually verified against every block - removed before it
ever reached a commit. See NOTES.md's `M5.AUX.1` section for the full
derivation, including the "topmost group" heuristic that looked right on
Block1 alone and was falsified by Block2/Block5, and why `M6.AUX.1` stays
deferred rather than shipped on an unverified assumption.

Verified end-to-end through this exact `agent.py`'s actual CLI entrypoint,
real KLayout 0.30.1, real evaluator, against every available block, each
compared to that block's own *true* pristine floor (a live KLayout re-run of
the untouched script, not the naive `final_violation_rate = 1.0` assumption -
see NOTES.md for why that assumption is wrong):

| Case | Pristine floor | FVR (`final_violation_rate`) | Repair rate | Connectivity |
|---|---:|---:|---:|---|
| Block1 | 1.2910 | **0.6025** | 0.668 | preserved |
| Block2 | 1.3235 | **0.4706** | 0.677 | preserved |
| Block3 | 1.2472 | **0.6517** | 0.618 | preserved |
| Block4 | 1.2857 | **0.4898** | 0.694 | preserved |
| Block5 | 1.2794 | **0.6471** | 0.588 | preserved |
| Block6 | 1.2996 | **0.6154** | 0.745 | preserved |
| Block7 | 1.2510 | **0.6235** | 0.665 | preserved |

FVR is the benchmark's own primary scoring metric (`final_violation_rate` -
fresh DRC violation count on the repaired script, divided by the original
violation count; lower is better, gates on `valid_repair`/
`connectivity_preserved`). These reflect both the hybrid LLM+deterministic
`M4.S.5` fix (Block1 only, its one safety-verified candidate) and the fully
deterministic `M5.AUX.1` grid-rail fix (every block except Block5, whose
off-grid rails are all stub-blocked). See NOTES.md's `M4.S.5` and
`M5.AUX.1` sections for the full derivation of each.

Every case: `valid_repair: true`, `connectivity_preserved: true`, and
`final_violation_rate` genuinely below that block's own true pristine floor -
not just below the prior 10-attempt history, and not just below the
misleading naive-`1.0` baseline. See `NOTES.md` for the full investigation:
why the naive floor isn't exactly `1.0`, the geometric root cause of the 3
via-enclosure rule families, the variable-name generalization bug and its
structural fix, the exact M4 grid-alignment formula (derived from
`asap7.lydrc`'s own `offgrid_cl` method, including a shift that looked clean
in isolation but broke 4 other rules once tried without moving its paired
via cell too), the merge-aware per-via `V2.M3.AUX.2` fix and the
non-rectangular-merge-region pitfall it had to handle, which similar-looking
fixes were tried and failed (and why - a reused-cell-instance risk pattern
that generalizes), and the full three-round `V1.M2.AUX.2` local-patch
derivation left for future iterations along with `V0.M1.AUX.3` (the
remainder not tied to the V1.M2 cascade) and the remaining spacing rules.

## Layout

```
T19-Caltech-ASU-Submission/
├── agent.py                 ← the submission agent (this is what gets submitted)
├── scripts/
│   └── model_service.py     ← local Gemini Developer API proxy, dev/test only
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
   Use a Gemini API key from [ai.studio](https://ai.studio) (the frictionless
   GCP hackathon account gives a billing-enabled project with much higher
   quota than the free-tier Vertex AI Express Mode this used previously).
   The env var name `EXPRESS_MODE_KEY` is kept for compatibility, but
   `model_service.py`'s client now runs in plain Gemini Developer API mode
   (`vertexai=False`), not Vertex AI Express Mode - a billing-project AI
   Studio key returns 403 `API_KEY_SERVICE_BLOCKED` on
   `aiplatform.googleapis.com` if `vertexai=True` is set. Also note: older
   model names (`gemini-2.5-flash`, `gemini-2.0-flash-exp`) return 404 "no
   longer available to new users" on a fresh project's key -
   `gemini-3.5-flash` is confirmed working and is the only current-generation
   name that still honors `thinking_config`/`thinking_budget=0`.

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

Expect `valid_repair: true`, `connectivity_preserved: true` for Block1 in
every case. The deterministic `M5.AUX.1` grid-rail fix always applies
regardless of model availability; `final_violation_rate`/`repair_rate`
additionally reflect the one `M4.S.5` model call (the only non-deterministic
step in this agent) when it succeeds: `0.6024590...`/`0.6680327...` with the
model reachable and approving the one safe candidate (confirmed via direct
local testing, KLayout 0.30.1 via WSL, real `gemini-3.5-flash` call through
`model_endpoint`), or a slightly smaller improvement if the endpoint is
unreachable or the model rejects/mis-responds - both are real, valid,
connectivity-preserved outcomes, never a worse one. The same command
with `--case Block2/Block3/Block4/Block5/Block6/Block7` reproduces the
corresponding rows in the results table above. See `NOTES.md` for the full
derivation of these fixes, why the naive floor isn't exactly `1.0`, and what's
deferred to future iterations.
