# Design notes

`agent.py` applies a set of **validated, KLayout-verified geometric fixes** on
top of what was originally a deliberate safe floor. This document records the
investigation behind both: why the floor wasn't `1.0`, and how the real fixes
were found, verified, and locked in.

## Why the naive floor isn't 1.0 (verified, not assumed)

This benchmark's scoring is gated lexicographic: a repair only counts if it
(1) renders/DRCs cleanly in KLayout and (2) preserves reference connectivity;
among eligible submissions, lower `final_violation_rate` wins, tie-broken by
higher `repair_rate`.

**The naive assumption - that an untouched script scores exactly
`final_violation_rate = 1.0` - is WRONG, and this was verified directly, not
assumed.** Re-evaluating a byte-for-byte exact copy of the pristine
`testcase/asap7/block/layout_script/Block1.py` (via `evaluator/evaluate_repair.py`,
real KLayout 0.30.1, no edits of any kind) gives `final_violations = 315`
against `original_violations = 244` - i.e. `final_violation_rate = 1.2909...`
even for **zero changes**. Rule-by-rule diffing the static given
`testcase/.../drc_report/Block1.drc.json` (244 total) against a fresh live
DRC run of the identical unmodified file (315 total) shows the *entire*
71-violation gap comes from exactly three rules:

| Rule | Static (given) | Fresh live re-run |
|---|---:|---:|
| `M4.AUX.1` | 18 | 72 |
| `M5.AUX.1` | 8 | 16 |
| `M6.AUX.1` | 3 | 12 |

Every other rule matches exactly between static and fresh. The cause wasn't
tracked down further (time-boxed), but it means: **the true, unavoidable
floor for this environment/KLayout combination is `final_violation_rate =
1.2909...`, not `1.0`.**

This also explains a puzzle in the historical data: T19's own **v2** (of the
10 prior versions) scored `final_violation_rate = 1.2909...` too, and its
repaired-script file size (366087 bytes) is exactly 3764 bytes smaller than
the true pristine file (369851 bytes) - a difference matching one stripped
`\r` per line (3764 lines total). In other words, **v2 wasn't a real repair
either - it was an accidental CRLF-to-LF-normalizing no-op that happened to
land on the same floor**, not a genuine fix.

Empirically, re-running `evaluator/evaluate_repair.py` against every one of
T19's 10 prior agent versions (`agent/t19_asu_agent_v1.py` through `v10.py` in
the official `ICLAD26-ASU-Problems` checkout):

| Version | final_violation_rate | repair_rate | Notes |
|---|---:|---:|---|
| v1 | 3.48 | 0.0 | Block2/3/6/7 crashed entirely (empty output) |
| v2 | 1.29 | 0.0 | Accidental no-op (see above) |
| v3 | 1.69 | 0.0 | |
| v4 | 25.5 | 0.0 | +5989 new violations against 244 original |
| v5 | - | - | Disqualified: broke connectivity (14 missing sources, 6 pin mismatches) |
| v6 | 14.6 | 0.008 | |
| v7 | 3.48 | 0.0 | Numerically identical to v1's output |
| v8, v9, v10 | n/a | n/a | Never finished - see below |

v8/v9/v10 had the right idea (real DRC `bbox`/`vertices` correlation against
the script, batched by rule) but never produced a final `_repaired.py` at all:
they process all 244 violations in small batches with 12-30s sleeps between
LLM calls and no incremental output-writing or resume-from-checkpoint logic,
so being killed/restarted mid-run (which happened repeatedly, per the
`usage/v8|v9|v10` call-id resets) meant total loss of progress every time.

**Every one of T19's 10 prior scored attempts was at or below the accidental
1.29 floor. This agent is the first to genuinely beat it** - see below.

## The real fix: three validated geometric repairs (locked in)

The key insight, found by direct KLayout `pya.Region` inspection (not
guessing): three DRC rules - `V2.M3.AUX.2`, `V4.M5.AUX.2`, `V5.M6.AUX.2` - all
share the same shape. Each says "via VX must exactly match the width of
metal layer MY, measured perpendicular to MY's length." The via's *local*
polygon, as defined inside its ASAP7 PDK library cell (e.g.
`VIA_VIA23_1_3_36_36`), is sized correctly for that cell in isolation - but
once instantiated inside `Block1`, the metal layer it sits on **merges with
adjacent metal shapes from neighboring cell instances** into a single larger
flattened region. The via/landing-pad's local width no longer matches the
*true, merged* width of the metal region it now sits inside, and KLayout's
flattened-hierarchy DRC engine sees the mismatch.

This was confirmed directly for each rule by flattening and merging the
relevant metal layers with `pya.Region(cell_Block1.begin_shapes_rec(layer_index)).merged()`
and comparing bboxes against each violation's reported bbox (see
`asu_merged.py`, `asu_merged_v4.py`, `asu_merged_v5.py` in scratch history) -
e.g. for `V2.M3.AUX.2`, the local M3 cell shape is 112 units tall but the true
merged M3 region at each violation site is 136 units tall.

The fix in each case: grow the via and its enclosing landing-pad metal shape
to match the *true merged* perpendicular extent, while keeping the via
strictly inside its enclosing metal (a separate rule, `VX.AUX.1`) so as not
to trade one violation for another. Every candidate value was re-verified
with a real KLayout DRC re-run before being accepted - several first attempts
(e.g. growing V2 to the isolated-cell height of 56 instead of the true merged
136/2=68) failed validation and were discarded (see "Fixes that didn't work"
below).

| Rule | Cell | Fix |
|---|---|---|
| `V2.M3.AUX.2` | `VIA_VIA23_1_3_36_36` (M2 pad `p101`, 3× V2 vias `p103/p104/p105`) | Y half-extent 36 → 68 (matches true merged M3 height of 136) |
| `V4.M5.AUX.2` | `VIA_VIA45_1_2_58_58` (M4 pad `p111`, 2× V4 vias `p112/p113`) | Pad X half-extent 208 → 284 (keeps V4 enclosed per `V4.M4.EN.1`); vias X range → ±240 (matches true merged M5 width of 480) |
| `V5.M6.AUX.2` | `VIA_VIA56_2_2_66_58` (2 of 4 V5 vias, `p116/p117`) | Y range → ±320 (matches true merged M6 height of 640); the other 2 vias (`p118/p119`) are left unchanged - they become a harmless subset of the new, larger via pair at the same X range |

**Result, verified end-to-end through the actual submitted `agent.py` against
Block1 (`run-id=t19-final-v1`, real KLayout 0.30.1, real local model
endpoint):**

```
repair_rate:            0.0     -> 0.5901639344262295
final_violation_rate:   1.2909  -> 0.9344262295081968   (first time below 1.0)
original_violations:    244
final_violations:       228   (was 315 on the live-reevaluated pristine floor)
removed_violations:     144
new_violations:         128
connectivity_preserved: true (824/824 sources verified, 0 mismatches)
valid_repair:           true
eligible_for_scoring:   true
```

This is a genuine improvement over the unmodified design, not just over the
prior floor - 228 final violations is fewer than the 315 the same design
scores when touched not at all. All 9 individual polygon edits applied
cleanly (verified via the `[INFO] Applied 9/9 validated edits` log line).

These are **standard ASAP7 PDK library via cells**, not Block1-specific
shapes, so `agent.py` applies the same exact-string edits to any case that
happens to reuse the same via cell definitions, and safely no-ops (with a
logged skip reason) on cases that don't - see `agent.py`'s
`apply_validated_fixes()`.

## Generalizing beyond Block1 (v2)

The first submitted version of `agent.py` implemented the three fixes above
as **exact-string edits keyed to Block1's own variable names**
(`p101 = pya.Polygon(...)` etc.). That was verified against Block1 only, and
the README claimed it would "apply automatically wherever the same library
cell recurs" - an assumption that was **wrong, and silently wrong**: tested
directly against the other 4 available blocks, all 9 string-matches got 0
hits on Block2/Block3/Block6/Block7. `agent.py` was quietly a no-op on 4 of
5 available cases.

**Root cause, confirmed by diffing Block1.py against Block2.py directly:**
every block's script defines the same PDK via cells
(`VIA_VIA23_1_3_36_36`, `VIA_VIA45_1_2_58_58`, `VIA_VIA56_2_2_66_58`) with
**byte-identical local polygon coordinates** - but each block's script
auto-generates its own sequential Python variable names (`p101` in Block1 is
the same shape as `p75` in Block2), so a fix keyed to a literal variable name
can only ever match the one block it was written against. This also explains
why this matters a lot: these 3 rule families are large - 447 violations in
Block7 alone, more than Block1's entire 244-violation total - so a fix that
only fires on Block1 leaves most of the available scoring surface untouched.

**The fix (v2): match structurally, not textually.** For each target via
cell, `agent.py` now finds every `pXXX = pya.Polygon(...)` statement
immediately followed by that specific cell's `.insert(pXXX)` call (regex,
capturing the variable name as a backreference so the assignment and the
insert are confirmed to refer to the same shape), groups the matches by GDS
layer in each layer's own appearance order, and only applies edits if the
found `{layer: shape_count}` structure **exactly** matches what was validated
on Block1 - any missing layer, wrong shape count, or unexpected extra layer
skips that entire cell (logged), never guesses. The per-position transforms
(which shapes move, to exactly what coordinates) are unchanged from the
original Block1 derivation - confirmed by diffing every violating shape for
these 3 rules across all 5 blocks, they all have byte-identical *local*
dimensions (72x72 / 96x96 / 96x128), and the target extents (136/480/640)
are fixed PDK/design-grid constants, not values that need recomputing per
block or per instance.

**Safety re-checked, not assumed, for every block:** the core lesson from the
`VIA_VIA12` failure below is that blind edits are only safe on
low-instance-count cells. Re-verified instantiation counts for all 3 target
cells across all 5 blocks: Block1 24/24/6, Block2 8/8/0, Block3 9/9/0, Block6
26/26/8, Block7 75/75/18 - all well under the "hundreds+" danger zone
`VIA_VIA12` (1326 instances) sits in.

**Verified end-to-end, real KLayout 0.30.1, all 5 available blocks** (each
compared against that block's own *true* pristine floor - a live re-run of
the untouched script, not the naive `1.0` assumption, since Block1 already
showed that assumption can be wrong by ~30%):

| Case | Pristine floor (live) | Repaired `final_violation_rate` | `repair_rate` | `valid_repair` | `connectivity_preserved` |
|---|---:|---:|---:|---|---|
| Block1 | 1.2910 | 0.9344 | 0.5902 | true | true |
| Block2 | 1.3235 | 0.9706 | 0.5882 | true | true |
| Block3 | 1.2472 | 1.0000 | 0.5056 | true | true |
| Block6 | 1.2996 | 0.9231 | 0.6559 | true | true |
| Block7 | 1.2510 | 0.9203 | 0.5843 | true | true |

Every block improves genuinely over its own true floor. Block3 lands exactly
at the naive `1.0` (its `V5.M6.AUX.2`/`VIA_VIA56_2_2_66_58` cell isn't
present in that block, so only 7 of the usual 9 edits apply there) but is
still a real improvement relative to Block3's own true floor of 1.2472.
Regression-checked: the new structural engine's output on Block1 is
byte-for-byte identical to the original v1 hardcoded-string output, so
nothing was lost in the rewrite.

## Fixes that didn't work (and why - important for future iterations)

- **Naive V2 height fix (56, the isolated-cell height) instead of 68 (the
  true merged height):** failed validation outright - didn't fix
  `V2.M3.AUX.2` at all and introduced 3 new violations. Root cause: `V2.AUX.1`
  ("V2 must be inside M2 and M3") means the *true* constraint is set by the
  merged M3 extent (136), not any single cell's local M3 shape (112) -
  confirmed via `pya.Region` merged-geometry inspection, not assumption.
- **Growing V1/M1 in the `VIA_VIA12` cell family** (the base M1<->M2 via,
  analogous fix pattern to the three above): catastrophic. This cell is
  instantiated **1326 times** throughout Block1 (vs. a handful for
  `VIA_VIA23_1_3_36_36`/`VIA_VIA45_1_2_58_58`/`VIA_VIA56_2_2_66_58`), so a
  single shared-library-cell edit ripples through the entire design at once;
  `final_violation_rate` jumped to 3.96. **Lesson generalized into `agent.py`:
  the same class of "grow the via to match the true merged metal extent" fix
  is only safe to apply blind/automatic on *specialized, low-instance-count*
  via cells. A cell reused thousands of times needs per-instance
  context-aware analysis (are they violating at all? does growing help some
  instances while breaking others under the same aggregate-count rule?)
  before it's safe to touch — this is exactly the harder hierarchy-transform
  problem described below, not yet built.**

## What's still open (deferred to future iterations per plan)

- `V0.M1.AUX.3` (37 violations): spread across multiple different standard
  logic cells (`BUFx2`, `INVx2`, `INVx3`, `BUFx3`, `FAx1`, `BUFx6f`) - likely
  as reuse-sensitive as `VIA_VIA12` above; needs per-cell-family
  instance-count and per-instance-context checking before attempting.
  Deferred, not attempted.
- `V1.M1.EN.1` (48 violations) / `V1.M2.AUX.2` (11 violations): a side effect
  surfaced from investigating the M2 growth; the correct fix appears to also
  route through `VIA_VIA12`'s ubiquity problem above. Deferred.
  (`V1.M2.AUX.2` was directly probed - `asu_check_v1m2.py` - confirming the
  same "width mismatch vs. true merged M2 extent" shape as the three fixed
  rules, but the ubiquity of `VIA_VIA12` blocks a blind automatic fix.)
- Spacing rules (`M1.S.2`, `M1.S.4`, `M2.S.7`, `M3.S.2`, `M4.S.5`) and 2 small
  new spacing violations introduced as a side effect of the M4/M5 fix
  (`M4.S.2`/`M4.S.3`, accepted as a net-positive tradeoff given the overall
  `final_violation_rate` improvement) - not yet addressed.
- The `M4.AUX.1`/`M5.AUX.1`/`M6.AUX.1` static-vs-live baseline discrepancy
  (see above) remains unexplained; doesn't block current fixes but worth
  understanding before pushing further, since these three rules are the
  single largest block of remaining violations.

## What a real hierarchy-aware repair engine still needs (for the above)

The core difficulty for everything still open above, confirmed by direct
inspection of `Block1.py` (3764 lines) and `Block1.drc.json` (244 violations
across 14 rules): most remaining violation coordinates do **not** appear
anywhere in the top-level `cell_Block1` section of the script - the offending
shape lives inside a **nested standard-cell instance**, in the macro's own
local coordinate frame, transformed by that instance's placement (rotation +
translation) to produce the absolute DRC-reported coordinates. (The three
fixes above were tractable without this machinery only because the violating
shapes happened to be direct, low-multiplicity top-level `cell_Block1.shapes(...)
.insert(...)` calls - confirmed via exact-string grep against the pristine
script, not assumption.)

Fixing the deferred rules correctly requires, roughly:

1. Parse every cell-instance placement (transform + offset) in the script.
2. For a violation's absolute bbox/vertices, inverse-transform through
   candidate instances to find the exact local polygon statement responsible.
3. Determine the instance count of that library cell (`cell.each_inst()` /
   direct grep count of instantiation calls) before attempting any edit -
   this session's single biggest lesson: low count (tens) is usually safe to
   edit blind; high count (hundreds+) requires per-instance-context gating,
   confirmed empirically via the `VIA_VIA12` failure above.
4. Gate every candidate edit by:
   - A **connectivity guardrail enforced in code** (not just prompt text):
     read `path_to_connectivity_file`, build the exact protected-point set
     for immutable/pin layers (per `evaluator/check_connectivity.py`'s own
     matching rule - exact point-multiset match on M1/layer-19 and pin
     layers 40/50/60/70; endpoint-*count*-only matching on other routing
     layers), and refuse any edit that touches a protected point.
   - A **local KLayout dry-run** (render + DRC, via `evaluator/evaluate_repair.py`)
     before accepting any edit: reject anything that doesn't strictly
     improve `final_violation_rate`, since `evaluate_repair.py`'s DRC metrics
     compare **per-rule aggregate counts only**, not per-instance/per-location
     - fixing one instance while breaking another under the same rule nets
     to zero credit or worse (exactly what happened with `VIA_VIA12`).
   - **Incremental writes to `output_path`** after every accepted edit (not
     only at the very end), so a kill mid-run still leaves a valid,
     better-than-floor result - the single biggest lesson from v8/v9/v10's
     total losses.

## Local evaluation works

Unlike the NXP benchmark (where the golden testbench is hidden), ASU's
evaluation data (DRC reports, connectivity references, design rules) is fully
available to participants, and **KLayout 0.30.1 - the exact version
`evaluate_repair.py::require_klayout()` checks for - runs locally** (verified
via WSL: `klayout -v` -> `KLayout 0.30.1`; `pip install shapely` was needed for
`evaluator/`'s Python dependencies). The pipeline is fully deterministic and
trustworthy for local iteration - every fix in this document was verified
this way, end to end, before being locked into `agent.py` - but be aware that
`M4.AUX.1`/`M5.AUX.1`/`M6.AUX.1` counts from the static given `drc_report/`
files should not be trusted as ground truth for `original_violations`;
prefer a fresh live DRC run of the pristine script as the actual baseline.
