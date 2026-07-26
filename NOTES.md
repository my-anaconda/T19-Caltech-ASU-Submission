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

## M4 grid alignment (v3) - the first deferred rule family tackled

`M4.AUX.1`/`M5.AUX.1`/`M6.AUX.1` (M4/M5/M6 must land on a 24/24/32nm grid,
checked on the *merged* metal region) were flagged above as the single
largest remaining violation bucket - and initially looked unfixable. This
section records both the failed attempt and the fix that followed it,
because the failure is what made the real fix findable.

**First attempt: shift one off-grid via pad. Failed badly.** Investigated
visually in the KLayout GUI (loading the pristine GDS with `-m` to overlay
DRC markers) plus direct `pya.RecursiveShapeIterator` queries to trace a
specific `M4.AUX.1` violation back to its source shape: the M4 landing pad
inside `VIA_VIA45_1_2_58_58` (already touched by the width-only fix above).
Shifting *only* that one instance's placement Y by -6nm (to the nearest
24nm grid line) did clear the grid violation, but broke **4 other rules**
at the same spot: `M4.AUX.2` (+2, track-position alignment), `M4.AUX.3`
(+4, "M4 may not bend"), `M4.S.4` (+2, spacing), `V3.M4.AUX.2` (+2, the pad
no longer matches the via *below* it). Net: +8 violations, not -2 - real
KLayout re-run, not assumed.

**Root cause, found via direct `pya` shape queries plus GUI inspection:**
`VIA_VIA45_1_2_58_58` (M4↔M5 via) and `VIA_VIA34_1_2_58_52` (M3↔M4 via) are
always placed at the **exact same instance-placement vector**, so their M4
pads fully overlap into a single merged shape - confirmed by grepping both
cells' placement lines in `Block1.py` and finding identical
`pya.Vector(X, Y)` arguments. The failed attempt moved only one of the two,
desyncing a merge that's supposed to stay coincident - that's the "bend"
and the other 3 new violations. GUI screenshots of the resulting layout
showed the mismatch directly: a thin orphaned sliver where the two pads no
longer lined up.

**The real fix: shift both co-located instances together.** Re-tested the
same -6nm shift, applied to *both* instances at once: `M4.AUX.1` -4,
zero collateral (no `M4.AUX.2`/`M4.AUX.3`/`M4.S.4`/`V3.M4.AUX.2`), real
KLayout confirmed. Testing the opposite direction (+18nm, up to the *next*
grid line, instead of -6nm down to the previous one) was even better:
`M4.AUX.1` -4 with zero collateral **and** no `M4.AUX.2` either - direction
matters, because `M4.AUX.2`'s legal M4 track positions are a *sparser* grid
than the raw 24nm one `M4.AUX.1` checks (confirmed empirically: the working
shift lands at `abs_Y mod 192 = 96`; failing ones don't hit that pattern).

**Scaling up surfaced a second, row-dependent gate.** Block1 has 24
co-located `VIA_VIA45`/`VIA_VIA34` pairs, in 4 groups of 6 by grid residue
(0 = already on-grid, 6/12/18 = off-grid). Applying the validated "+18nm,
both instances together" fix to all 6 residue=6 pairs at once produced a
surprise: net still improved, but `M4.AUX.2` reappeared (+2) - meaning not
every residue=6 *row* is safe, only some. Bisecting confirmed it precisely:
of the 3 distinct row Y-positions among Block1's 6 residue=6 pairs
(Y=11880, Y=7560, Y=3240), the two rows at Y=11880/Y=3240 are clean and the
row at Y=7560 is not - a **row-level** property (same result at both X
columns for each row), consistent with `M4.AUX.2`'s legal-track period
(192nm) not evenly dividing the ~1080nm row-to-row spacing used here.
Applying the fix only to the confirmed-clean 4 pairs: `M4.AUX.1` -16,
zero collateral, real KLayout confirmed.

**Generalized into `agent.py` as a safety-gated, block-agnostic rule**
(`apply_grid_alignment_fixes`): find every co-located `VIA_VIA45`/`VIA_VIA34`
pair (matched by identical placement vector, regardless of which block's
top-cell name it's under), compute the pad's grid residue, and - **only for
residue=6** - compute the minimal up-shift to the next grid line and check
it lands on a legal `M4.AUX.2` track (`abs_Y mod 192 ∈ {48, 96}`) before
applying it to both instances together. Residue=12/18 are deliberately
**not** attempted: individually testing one instance from each (with the
same "shift both instances, round up to next grid line" logic) still
produced `M4.AUX.2`/`M4.AUX.3`-style collateral, meaning residue=12/18 need
either a different target shift or a genuinely different fix - not yet
derived, and not guessed at here. Verified via real KLayout DRC + connectivity
re-run across all 5 blocks:

| Case | Pristine floor | v2 (via-growth only) | v3 (+ M4 grid alignment) | Connectivity |
|---|---:|---:|---:|---|
| Block1 | 1.2910 | 0.9344 | **0.8852** | preserved |
| Block2 | 1.3235 | 0.9706 | **0.9265** | preserved |
| Block3 | 1.2472 | 1.0000 | **0.9663** | preserved |
| Block6 | 1.2996 | 0.9231 | **0.8745** | preserved |
| Block7 | 1.2510 | 0.9203 | **0.8967** | preserved |

Every case still `valid_repair: true`, `connectivity_preserved: true`. The
`M4.AUX.2` legal-track period (192nm, phases 48/96) was derived from just 2
data points on Block1 and cross-checked against every distinct residue=6 row
Y-value across all 5 blocks (6 distinct rows total) before being trusted as
a general gate - not just fit to Block1 and hoped to generalize.

## M4 grid alignment, residue=12/18 (v4) - exact formula from the rule source

The v3 section above left residue=12/18 rows deliberately unfixed, using a
reverse-engineered `mod-192/{48,96}` approximation. Reading `asap7.lydrc`'s
`offgrid_cl` Ruby method directly (not reverse-engineering further) gives the
exact rule:

```ruby
def offgrid_cl(axis, pitch_dbu, offset_dbu, base_dbu = nil)
  ...
  next if bb.bottom % base_dbu != 0 || bb.top % base_dbu != 0  # M4.AUX.1's own grid
  cl = axis == :y ? (bb.bottom + bb.top) / 2 : (bb.left + bb.right) / 2
  if (cl - offset_dbu) % pitch_dbu != 0 ... # flag as violation
```
called as `m4_1x.offgrid_cl(:y, 192, 48, 96)`. Since the co-located M4 pad's
local shape is symmetric about the placement Y (local range -48..+48 raw
units), its centerline equals the raw placement Y exactly, so the single
condition **`(Y - 48) % 192 == 0`** decides *both* `M4.AUX.1` (the 96-grid
precondition, since 192 = 2×96) and `M4.AUX.2` (the pitch/offset condition)
at once - no separate/approximate check needed. Cross-checked against 10 real
KLayout DRC re-runs (5 off-grid rows × both shift directions): zero
mismatches. `compute_grid_shifts()` now handles every off-grid residue this
way (not just 6), picking whichever of the two nearest 24nm-grid neighbors
satisfies the exact condition (exactly one direction ever does, confirmed
empirically over all tested rows) and skipping (logged) only the genuinely
ambiguous case where neither/both directions qualify.

## Merge-aware, per-via shape-aware `V2.M3.AUX.2` (v5)

The v2 fixed-target growth (M3 → ±68 always) silently broke once the v4 grid
shifts started moving more rows: shifting a `VIA_VIA34_1_2_58_52` instance's
M3 pad can change the *merged* M3 extent a nearby, un-shifted
`VIA_VIA23_1_3_36_36` instance must match - a fixed constant is wrong for
those specific instances (confirmed via real KLayout DRC re-run once v4 was
combined with v2).

Direct `pya.Region`/vertex inspection showed the merged region in the
affected cases is a **stepped, non-rectangular polygon** - full width for the
instance's own 136-height "core", narrower for whatever extra height the
shifted neighbor contributes. Growing a via to the group's overall
bounding-box height (ignoring the step) can stick the via out of the true
shape, causing new `V2.AUX.1` (containment) / `V2.M3.EN.2` (enclosure)
violations - this was seen directly before being fixed.

**The fix:** for each of the 3 vias in `VIA_VIA23_1_3_36_36` independently,
compute the Y range continuously covered by the merge group at *every* X
point across that via's own X-span (the "vertical slice intersection" -
`_safe_y_range_for_x_range()` in `agent.py`), not just the group's bbox
height. This is not necessarily symmetric about the via's original position -
asymmetric growth is exactly what "match M3's true edges" requires whichever
direction they lie in. The M2 pad grows to the union of all 3 (possibly
different) via ranges, to keep every via enclosed (`V2.M2.EN.1`). Instances
whose full computed result matches the original default range keep the
shared cell definition (edited in place); any instance whose result differs
gets its own new, uniquely-named cell definition, with only that instance's
placement line repointed. Verified clean (zero `V2.AUX.1`/`V2.M3.EN.2`
collateral) across all 5 blocks - `V2.M3.AUX.2` itself now goes to 0
everywhere (72/24/27/78/225 violations fixed on Block1/2/3/6/7
respectively).

**Two connectivity-checker parsing conventions this surfaced (both silent,
neither raises an error) - critical for anyone generating new per-instance
cell definitions for this evaluator:**
1. `evaluator/check_connectivity.py` matches polygon variable names with the
   hardcoded regex `^(p\d+)\s*=\s*pya\.Polygon\(...)` - literally "p" plus
   digits only. A custom name like `p_MyCell_1` is silently invisible to the
   tracer, collapsing `modified_paths_count` to 0 for the *entire* script,
   not just locally. `agent.py`'s new-cell generator uses a plain numeric
   `p{N}` scheme for exactly this reason.
2. The same script identifies the script's "top cell" by scanning for
   `create_cell()` calls and keeping the **last one found** in the file, no
   further anchoring. This happens to work for `BlockN`'s own `create_cell()`
   line only because it's always the last one in each script's initial
   declarations section. A new `create_cell()` call for a custom via cell
   must therefore be inserted **before** that point (right after the
   original cell's own `create_cell()` line) - not alongside its shape
   definitions further down, which live after it. Getting this wrong
   collapses connectivity tracing to 0 paths for the whole script (confirmed
   by triggering it, then fixing it, via direct `check_connectivity.py`
   re-runs).

## `V1.M2.AUX.2` cascade - attempted, and reverted (v6 → v7)

Growing `VIA_VIA23`'s M2 pad (v5, above) to enclose its now-asymmetric V2
vias can merge with a large M1/M2 rail cell's own M2 shape nearby (this
cell's name embeds block-specific dimensions - `VIA_via1_2_3132_18_1_87_36_36`
on Block1/6, `..._2160_..._60_...` on Block2, `..._2322_..._64_...` on
Block3, `..._6750_..._187_...` on Block7 - discovered per-script via regex,
not hardcoded), inflating the rail's local merged M2 height beyond what its
own (untouched) V1 taps can match - the same `V1.M2.AUX.2` mechanism as v5,
one metal layer down. `_apply_dynamic_merge_aware_fix()` was generalized to
handle both cases with the same per-via-safe-range logic.

Two structural bugs surfaced and were fixed first (both specific to this
cell having up to 187 vias per instance, unlike `VIA_VIA23`'s fixed 3):
- **Orphaned base cell → KLayout DRC crash.** If *every* instance of the
  rail cell needs a non-default range (happened on Block7 - no instance's
  87-via computation matched the literal default), the original shared cell
  definition becomes completely unreferenced once every instance is
  repointed to its own custom cell. KLayout's DRC macro requires exactly one
  parentless "top" cell; an orphaned definition becomes a second one and
  DRC aborts with `RuntimeError: 'source': The layout has multiple top cells`.
  Fixed by always dedicating exactly one group (the literal default if
  present, otherwise the largest non-default group) to be edited in place on
  the shared cell definition, via a new asymmetric `_set_y_range(y0, y1)`
  transform (unlike `_grow_y`, not required to be symmetric about 0).

**Then, despite the above being individually correct, the fix as a whole was
found to break connectivity - and was reverted.** Real
`check_connectivity.py` re-run against the fully-generalized fix: all 824
sources found (no parsing regression), but 460 pin-endpoint + 381
routing-endpoint mismatches, `modified_paths_count` 8260 vs. golden 1350.

**Root cause, confirmed by direct headless `pya.Region` probing (not
assumed):** the per-via safe-range computation only validates the *via/pad*
growth against the **reference layer's (M2) merge topology** - it never
checks whether growing the **pad layer (M1) itself** stays clear of *other,
unrelated* M1 shapes nearby. On Block1's rail row at placement Y=3240, one
instance's M1 pad legitimately needed to grow (per the M2-safety check) up
to local Y=+140 (absolute Y=3380) - but unrelated M1 shapes (other
standard-cell M1 shapes sharing that same row) already occupy absolute
Y=3348-3380 in that same X range. The grown pad silently merges with them,
creating new, bogus electrical connections that don't exist in the golden
design - exactly matching the observed pin/routing endpoint-count blowup.
Confirmed via `pya.Region` probes directly on both the pristine and repaired
GDS at that exact box: identical shapes below/at the default footprint,
brand-new overlapping shapes only in the grown region.

Fixing this properly would need a *second*, independent safe-range check on
the M1 layer's own neighbors (analogous to `_safe_y_range_for_x_range` but
scoped to M1's own merge/proximity, then intersected with the M2-derived
via range before finalizing) - not yet built. Given the demonstrated risk
(this is now the *third* cascade level, one layer further down each time,
and each level has needed a materially harder safety check than the last),
**`apply_dynamic_v1m2_fix()` is implemented in `agent.py` but deliberately
not called from `main()`.** Confirmed via direct re-run that skipping only
this one fix restores connectivity exactly (1350/1350 paths, 0 mismatches
on Block1; equivalent exact-match results on all 5 blocks). The reintroduced
`V1.M2.AUX.2` violations (48/16/23/66/189 on Block1/2/3/6/7) are the accepted
cost of *not* shipping an unsafe fix, not a regression versus a safer
alternative - they were never fixed to begin with, and the alternative
(shipping the unsafe version) breaks the hard connectivity gate entirely.

**Verified end-to-end, real KLayout 0.30.1 + `check_connectivity.py`, all 5
blocks, this intermediate locked-in state (v4 grid formula + v5 merge-aware
V2.M3.AUX.2 + the original 3 fixed-target fixes, v1m2 NOT applied):**

| Case | Pristine floor (live) | Repaired `final_violation_rate` | `repair_rate` | `connectivity_preserved` |
|---|---:|---:|---:|---|
| Block1 | 1.2910 | **0.6475** | 0.6639 | true |
| Block2 | 1.3235 | **0.6176** | 0.6765 | true |
| Block3 | 1.2472 | **0.7640** | 0.5730 | true |
| Block6 | 1.2996 | **0.6518** | 0.7287 | true |
| Block7 | 1.2510 | **0.6576** | 0.6549 | true |

Every case: `valid_repair: true`, `connectivity_preserved: true`, all
connectivity sources verified with 0 mismatches - a substantial improvement
over the v3 table above (e.g. Block1 0.8852 → 0.6475), driven by the v4 exact
grid formula covering every off-grid row (not just residue=6) and the v5
merge-aware fix resolving `V2.M3.AUX.2` completely (0 remaining, vs. partial
before) instead of trading it for collateral damage.

This was NOT the final state - see the next section, where the same
`V1.M2.AUX.2` cascade was revisited with a redesigned, local-patch approach
that ships correctly.

## `V1.M2.AUX.2` cascade, take 2: local patches (v8) - shipped

Picking the whole-pad-growth attempt back up, three further rounds of real
KLayout validation were needed before it was actually safe to ship. Each
round found a genuinely different failure mode - not variations on the same
bug - which is why this took three passes rather than one:

**Round 1: grow locally, not the whole row.** The v6→v7 revert's root cause
was growing the pad as ONE rectangle spanning the entire row (thousands of
raw units), which reaches far past where growth is actually needed. Traced
the specific violating vias directly: at Block1's row Y=3240, the vias that
actually need growth sit at X≈2772-2988 - nowhere near the X≈6208-6752
standard-cell pin the whole-row growth collided with. Fix: grow ONLY the
specific via that needs it, plus a small local M1 "patch" (that via's own
X span ± a fixed 6nm enclosure margin), leaving the rest of the row-wide
pad completely untouched. Verified via `check_connectivity.py`: connectivity
fully restored (1350/1350 paths, 0 mismatches).

**A parser blind spot surfaced along the way.** The M1-safety check (find
foreign M1 obstacles near a candidate patch) initially found *zero*
obstacles where real KLayout found a very real one. Root cause: `agent.py`'s
shape parser (`_ANY_POLY_INSERT_RE`) only recognized exactly-4-point
(rectangular) polygons - direct inspection showed unrelated standard cells
(e.g. `BUFx2_ASAP7_75t_R`) define M1 routing as genuine 8-12-point jogged
polygons, invisible to a 4-point-only pattern. Considered shelling out to
real KLayout (`pya`) as a subprocess instead (explicitly permitted by
`AGENT_GUIDE.md`), but since every fix in this agent only ever touches M1
and above (never contacts/poly/diffusion/well - confirmed by listing every
layer number touched: 19/20/21/25/30/40/45/50/55/60), a bounding-box
approximation is provably safe for this specific use (an obstacle's bbox is
always a conservative *super*-set of its true footprint, so treating other
cells' shapes as keep-out zones via bbox can only make the check MORE
cautious, never less) - so `_ANY_POLY_INSERT_RE` was generalized to match
any N-point polygon (bbox math already handles any point count) instead,
keeping `agent.py` pure-stdlib. Confirmed all 1540-3727 polygon statements
across all 7 blocks are single-line (no multi-line polygon literals to
worry about) before trusting the regex.

**Round 2: diagonal/corner proximity, not just directly-above/below.** The
per-via M1 safety check only considered foreign shapes whose X range
directly overlapped the candidate patch's - real KLayout DRC re-run showed
this misses CORNER-to-corner spacing rules (`M1.S.3/S.4/S.6`): a foreign
shape sitting just outside the patch's X range but close enough diagonally
still violates them without ever X-overlapping. Fixed by treating any
foreign shape within the spacing cushion (36nm, safely above every `M1.S.*`
threshold) of the patch's X range as relevant, not just directly-overlapping
ones - guarantees true Euclidean separation of at least the cushion in the
worst case, not just Y-only clearance.

**Round 2b: self-inflicted spacing between our OWN adjacent patches.**
Fixing the above still left new `M1.S.4` (tip-to-tip, <24nm edges: 31nm min)
violations - traced via real DRC markers to edges exactly 6nm apart at the
same X (matching the 6nm enclosure margin exactly). Root cause: two
adjacent vias in the SAME instance both needing growth got two SEPARATE
patches, close enough to violate spacing against EACH OTHER (a foreign-
obstacle check can't catch this since neither patch is foreign to the
other). Fixed by merging adjacent patches whose gap would be less than the
spacing cushion into ONE combined patch, using the intersection of the
group's individual safe ranges (never wider than any member's own computed-
safe range, so never less safe).

**Round 3: V0 (contact) flush-alignment, one layer further down.** Even
after rounds 1-2, `V1.M2.AUX.2` improved (48→43 on Block1) but
`V0.M1.AUX.3` got worse by almost the exact same amount (37→42) - an
almost-exact 1-for-1 trade, not a coincidence. Investigated visually in the
KLayout GUI (several examples, both "corner" and "straight-edge" cases -
screenshots confirmed the same mechanism every time): many of these V1 taps
have a V0 contact sitting flush against the *default* M1 edge by design: our
patch moving that edge away from its default position breaks the V0's flush
alignment, which `V0.M1.AUX.3` requires (the identical "VX must exactly
match the layer-below's width" rule family, one more layer down). Fixed by
adding a THIRD independent safety constraint alongside the M2-merge-topology
and foreign-M1 ones: `_v0_safe_range_for_via()` finds any V0 contact flush
against the default edge in the direction growth is being considered, and
simply refuses to grow that direction for that via if one exists (same
treatment as hitting a foreign M1 obstacle with zero clearance) - cheaper
and more robust than trying to "notch" the patch shape around each V0's
footprint, and confirmed via real DRC re-run to eliminate the trade
entirely: `V1.M2.AUX.2` 48→44, every other rule (including `V0.M1.AUX.3`)
**exactly unchanged**, `check_connectivity.py` exact match (1350/1350 paths,
0 mismatches).

**Final architecture:** each via gets grown only as far as the
*intersection* of three independently-computed safe ranges - what the M2
merge topology allows, what nearby foreign M1 shapes (rectangular or not)
allow with a spacing cushion, and what any flush-aligned V0 contact allows -
never wider than any single one of them, and never narrower than the
original default. Adjacent vias needing growth share one merged patch
instead of colliding with each other. The row-wide pad itself is never
touched.

**Verified end-to-end through `agent.py`'s actual CLI entrypoint (not just
direct function calls), real KLayout 0.30.1 + `evaluate_repair.py`, all 7
available blocks (including Block4/Block5, released as this session's hidden
test cases) - this is the final, shipped state:**

| Case | Pristine floor (live) | Repaired `final_violation_rate` | `repair_rate` | `connectivity_preserved` |
|---|---:|---:|---:|---|
| Block1 | 1.2910 | **0.6311** | 0.6639 | true |
| Block2 | 1.3235 | **0.5147** | 0.6765 | true |
| Block3 | 1.2472 | **0.7191** | 0.5730 | true |
| Block4 | 1.2857 | **0.5306** | 0.6803 | true |
| Block5 | 1.2794 | **0.6471** | 0.5882 | true |
| Block6 | 1.2996 | **0.6518** | 0.7287 | true |
| Block7 | 1.2510 | **0.6431** | 0.6549 | true |

Every case: `valid_repair: true`, `connectivity_preserved: true`. Every
block improves over (or, for Block6, exactly matches - see below) the
already-validated no-V1M2 state from the previous section - never a
regression. One small known residual: Block6 shows a net-zero rule-level
trade (`V1.M2.AUX.2` -2, `V1.M2.EN.2` +2, a DIFFERENT enclosure rule than
`V0.M1.AUX.3`) - isolated to 2 instances in 1 block, not a connectivity
issue, and not pursued further given the otherwise-clean result everywhere
else.

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

- `M4.AUX.1` residue=12/18 rows (12 more co-located pairs in Block1 alone):
  the "shift both instances up to the next grid line" logic that works
  cleanly for residue=6 does not transfer as-is - individually testing one
  residue=12 and one residue=18 instance (same both-together shift
  discipline) still produced `M4.AUX.2`-style collateral. Possibly a
  different legal-track phase for these residues, or a genuinely different
  geometry constraint - not yet derived. Next step planned: either derive
  `M4.AUX.2`'s exact legal-track formula from the `.lydrc` rule deck's
  `offgrid_cl(:y, 192, 48, 96)` call directly (rather than the current
  reverse-engineered mod-192/{48,96} approximation, which was cross-checked
  against 6 data points but not derived from the rule's actual semantics),
  or brute-force search nearby candidate shifts per row via real KLayout
  DRC re-run.
- `M5.AUX.1`/`M6.AUX.1`: investigated and found NOT tractable the same way as
  `M4.AUX.1`. `VIA_VIA45_1_2_58_58`'s own local M5 pad (`p110`, 480x184) and
  `VIA_VIA56_2_2_66_58`'s own local M5/M6 pads (`p114`/`p115`) are NOT what
  dominates the grid-alignment check - confirmed via direct `pya` probing:
  a genuine, separate top-level `Block1` shape spans Y=2068-13680 (11612
  raw units - nearly the full block height) on M5, and another spans
  X=1888-13168 (11280 raw units - nearly the full row width) on M6. These
  are real power/signal rails, not owned by any via cell; the via cells'
  own pads are tiny taps into them. Shifting the via cell's placement (the
  M4.AUX.1 trick) wouldn't move the rail - it would just disconnect the via
  from it. Fixing this for real means relocating the rail itself, which
  likely serves many other taps along its entire height/width - the same
  "high-reuse, high-blast-radius" danger class as `VIA_VIA12`, not a small
  contained move. Not pursued further.
- `V1.M1.EN.1` (11 violations): investigated - every violation traced via
  direct `pya` probing lands on `VIA_VIA12` specifically (the base M1<->M2
  via, 150 instances in Block1 alone - the same cell explicitly flagged as
  catastrophic to touch blind in "Fixes that didn't work" above). Not
  pursued further for the same reason.
- `V0.M1.AUX.3` (37 violations): investigated exhaustively - four genuinely
  different fix directions tried, each disproven with real measurements
  (not assumptions). Root cause fully understood; no safe fix found.

  **Confirmed context-dependent, not a library defect**: `BUFx2_ASAP7_75t_R`
  checked via the real DRC deck in total isolation (its own shapes copied
  into a standalone top cell - the first attempt at this silently produced
  an EMPTY GDS due to a `Shapes.insert()` misuse, giving a false "0
  violations" read; rebuilt correctly per-shape, confirmed non-empty: 5 M1
  shapes, 15 V0 shapes copied) genuinely gives **zero** `V0.M1.AUX.3`
  violations - confirmed a second, independent way by replicating the
  rule's exact edge logic directly in `pya` (`v0.edges - m1.edges`, split by
  angle, checking corner interaction) against both the isolated cell and
  the full Block1 layout: 0 flagged in isolation, 37 in Block1, matching the
  real DRC tool exactly.

  **Root cause, nailed down to the single erased edge**: for one flagged
  instance (`BUFx2` at (5832,2160)), V0 sits at abs `(6444,2268)-(6516,2340)`.
  Its own cell's internal M1 shape (`p490`, an 8-point NOTCHED polygon, not
  a rectangle) has a deliberate step at local Y=180 that - in isolation -
  keeps this V0's top edge exactly coincident with that step, satisfying
  the rule by design. Edge-by-edge comparison (`pya.Edges`, exact overlap
  check) showed this same top edge is coincident in isolation but NOT in
  the full Block1 layout. Diffing the true merged M1 region (real polygons,
  not bounding boxes) between the two contexts isolated the exact culprit:
  a single extra shape, abs bbox `(6480,2340)-(6552,2384)`, present only in
  Block1 - which is `VIA_VIA12`'s own M1 pad. Checked across 15 sampled
  violations: **100% of them have a `VIA_VIA12` instance sitting right
  there** - not a coincidence, a universal mechanism. `VIA_VIA12` (the
  router's base M1<->M2 via, connecting a standard cell's internal pin up
  to the routing fabric) drops its pad exactly on top of the library cell's
  purpose-built notch, erasing the one edge that kept V0 DRC-clean.

  **Four fix directions tried, all disproven with real data:**
  1. *Grow V0 to match the merged M1* - blocked by diffusion (LISD) size:
     measured across 12 sampled instances, LISD is a fixed ~96 raw units
     wide while the merged M1 needing to be matched is 200-848 (2.1x-8.8x
     too big) - growing V0 that much would blow through `V0.LISD.EN.2`/
     `V0.LISD.EN.3`, rules already in the DRC deck, no LVS needed to see
     this is geometrically impossible in every case checked.
  2. *Add a redundant M1 patch sized to V0, hoping to reintroduce a
     coincident edge* - tested directly with a minimal synthetic GDS
     (baseline vs. baseline+patch, both real DRC runs): **identical**
     violation count in both. KLayout's DRC engine merges same-layer shapes
     into one region before computing edges, so a patch fully contained
     inside the existing merged shape has zero effect on the edge set -
     there is no way to introduce a new coincident edge without genuinely
     changing the merged shape's true boundary.
  3. *Trim a suspected top-level auto-router M1 shape* - disproven directly:
     zero M1 area anywhere near this V0 is owned by `cell_Block1` itself: both
     overlapping M1 polygons are explicitly owned by the `BUFx2` instance
     (one has local bbox `(580,108)-(1008,972)`, an exact match for the
     notched library polygon). There is no top-level shape to trim.
  4. *Shift the specific `VIA_VIA12` instance off the notch* - the most
     promising direction (`VIA_VIA12` is a small, purpose-built via cell,
     the same *category* of thing safely adjusted all session, unlike V0 or
     the standard cell's own geometry). Tested empirically via real
     `evaluate_repair.py`/`check_connectivity.py` re-runs at multiple Y
     shifts (-60 to +60 raw units) of just this one instance's placement:
     `dy=+50` does clear the notch and reduce `V0.M1.AUX.3` (37->36), but
     **breaks connectivity** (`check_connectivity.py`: 2 pin endpoint
     mismatches) and makes 4 other rules worse in the process (`M1.A.1`
     +1 new, `M1.S.2` +1, `V1.M1.EN.1` +1, and a NEW `V1.M2.AUX.2` +1 - the
     very cascade rule this session spent 3 rounds getting right). Root
     cause: `VIA_VIA12`'s own M1 pad is 88 raw units tall, but the "safe"
     un-notched zone below the step is only 72 tall - the pad is physically
     bigger than the space that would avoid the notch entirely, so any
     vertical shift either straddles the notch (no fix) or moves the via
     off its correct landing area onto the wrong part of the net (breaks
     connectivity) - a genuine, hard geometric conflict, not a tuning
     problem.

  **Conclusion (initial)**: the mechanism is fully understood (a via/contact
  losing a purpose-built flush relationship when something else's metal
  merges in - the same general family as the V1.M2.AUX.2 cascade), but every
  concretely-testable fix direction is blocked by a real, measured
  constraint - diffusion size, KLayout's shape-merging behavior, the
  absence of a movable top-level shape, or the connectivity gate itself.

  **Follow-up: a genuinely new, important distinction found - "shrink the
  pad" is NOT the same risk as "shift the via".** Fix direction 4 above
  (shifting `VIA_VIA12`'s whole placement) broke connectivity. Tested a
  different variant of the SAME cell-uniquification technique: clone
  `VIA_VIA12` into a new cell for just this one instance (exactly this
  agent's existing custom-cell architecture, not a new mechanism), but only
  reshape its M1 PAD - never move the via (V1 layer) itself. Three pad
  shapes were tried, each verified with a real DRC + `check_connectivity.py`
  re-run:

  | Pad variant | `V0.M1.AUX.3` | `V1.M1.EN.1` | New violations | Connectivity |
  |---|---|---|---|---|
  | Shrink (cap top at the notch line) | 37->36 (fixed) | 11->12 (worse) | `V1.AUX.1` +1 (part of the via now uncovered) | **preserved** |
  | Widen (match the notch's own width) | 37 (unchanged) | 11 (unchanged) | none | preserved |
  | Stepped (edge aimed at V0's exact corner) | 37 (unchanged) | 11 (unchanged) | none | preserved |

  The critical finding: **`check_connectivity.py` only cares about the via's
  actual position/overlap with the correct net - not full DRC enclosure.**
  Reshaping the pad (while leaving the via itself exactly where it is) never
  broke connectivity in any of the three variants, even the one that
  exposed part of the via. This is a real, useful, previously-unproven
  distinction for any future work on this cell.

  However, precisely engineering a pad shape that both fixes
  `V0.M1.AUX.3` AND avoids new collateral (`V1.M1.EN.1`, `V1.AUX.1`) proved
  harder than expected - the "stepped, aimed at V0's exact corner" attempt
  was hand-derived from tracing BUFx2's notched polygon vertices by hand,
  and still didn't land the intended coincident edge once actually merged
  with the full local topology (verified via the same edge-by-edge `pya`
  check used earlier - the edge still wasn't coincident, meaning some
  additional shape in the true local merge wasn't accounted for by hand).
  Three attempts, three misses on the "fix without collateral" goal.

  **Final proof of infeasibility (this instance), via boolean region check
  rather than more hand-designed shapes.** Reframed as a feasibility test:
  does ANYTHING other than `VIA_VIA12`'s own pad cover the via's own
  physical footprint in the region above the notch line (abs Y>2340, the
  via's own X-span 6480-6552)? Computed directly via `pya.Region` boolean
  subtraction (true polygons, not bounding boxes - an earlier pass at this
  exact check used bboxes and wrongly suggested full coverage, corrected
  once redone with the real notched polygons): **zero** - nothing else in
  the design covers that area at all. The via must be covered by *some* M1
  (a bare physical requirement, not a tunable margin), and only
  `VIA_VIA12`'s own pad provides it there. This means **any** legal pad
  shape - however cleverly stepped - necessarily extends past the notch
  line, because the via's own unmovable body straddles it. This is a
  proven geometric impossibility for this instance, not a shape not yet
  found: as long as the via stays where connectivity requires it, no pad
  shape can simultaneously (a) fully cover the via and (b) avoid erasing
  the notch edge V0.M1.AUX.3 depends on.

  **Extended the same boolean check across all 37 violations, not just
  this one instance** (per-marker: find the nearby `VIA_VIA12`'s V1
  footprint, determine which of V0's edges is coincident vs. not to locate
  the notch line, compute the via's forced-coverage zone on the
  non-coincident side, check true polygon coverage by anything other than
  `VIA_VIA12`'s own pad):

  | Result | Count |
  |---|---:|
  | Provably infeasible (zero coverage from anything else) | 35 |
  | Has genuine slack (some other material already covers the zone) | 0 |
  | Ambiguous (different local structure, not the same simple pattern) | 2 |

  **Verification pass (important given how many hand-computation mistakes
  happened earlier in this same investigation - bbox-vs-true-polygon
  confusion, backwards branch conditions, a shadowed-variable bug that
  silently produced an empty test GDS).** Before trusting the 35/0/2
  result, it was checked two more ways:
  1. *Independent re-derivation, not just re-running the same script.* For
     a spread-sampled subset, listed every cell with ANY M1 shape merely
     *touching* (bbox-level) the forced zone, then computed each one's
     TRUE polygon overlap with the zone directly (`shape.polygon`, not
     bbox). Confirmed e.g. `INVx3_ASAP7_75t_R`'s shape bbox touches a forced
     zone but its true polygon contributes exactly 0 overlap there, while
     `VIA_VIA12` alone contributes the full area - matching the subtraction
     computation exactly, via a completely different code path.
  2. *The 2 "ambiguous" cases were re-examined rather than left as a loose
     end.* Both turned out to have neither top nor bottom edge coincident -
     because their flush edge is on the RIGHT instead (the original check
     only tested the vertical axis). Re-running the identical
     coverage-feasibility logic on the horizontal axis for both: **zero**
     coverage from anything else in both cases too - same mechanism, same
     conclusion, just oriented sideways.

  **Final, verified conclusion: all 37 of 37 violations in Block1 are
  provably geometrically infeasible** by the identical mechanism (`VIA_VIA12`'s
  own via body requires M1 coverage on the far side of a standard-cell
  notch line, and literally nothing else in the design covers that specific
  area - confirmed via true-polygon overlap, not bounding boxes, and
  cross-checked by two independent computations). Zero found with
  exploitable slack. A per-instance shape-solver would have nothing to
  solve - every instance's answer is already proven "no legal pad shape
  exists," not "not yet found." `V0.M1.AUX.3` is closed out for this
  session with high confidence: mechanism understood, fix directions
  exhaustively tested, and the negative result independently
  double-checked after multiple earlier mistakes in the same
  investigation - not taken on faith from the first script that produced
  it.
- `V1.M2.AUX.2`: **shipped** - see "`V1.M2.AUX.2` cascade, take 2: local
  patches (v8)" above for the final, three-safety-constraint local-patch
  fix. `V1.M1.EN.1` itself is still not directly targeted (it's a
  pre-existing violation on vias this fix doesn't touch), and remains open.
- `M4.AUX.2` (2 remaining in Block1, 20 total across all 7 blocks):
  root-caused precisely - these are `VIA_VIA45`/`VIA_VIA34` co-located pairs
  already on the base 24nm grid (`M4.AUX.1` satisfied, residue 0) but not on
  the coarser 192nm track the original grid-alignment logic never even
  checked for in that case (it only ever looked at off-grid rows). Attempted
  a fix (shift by one full 24nm grid step, 96 raw units - both directions
  are guaranteed track-legal here since 192=2x96, so a real M4-spacing
  safety check picks between them) and validated it via a real
  `evaluate_repair.py` + `check_connectivity.py` re-run across all 7 blocks,
  not just DRC: **broke connectivity on 2 of 7 blocks** (26 pin + 7 routing
  endpoint mismatches on Block1 alone), even though the M4 spacing check
  itself passed cleanly. Reverted rather than shipped (confirmed via
  `git diff` against the last shipped commit that the revert is clean - only
  a documentation comment remains, no functional change). Root cause is
  almost certainly the same class of issue as the very first grid-alignment
  attempt: a 96-raw shift is much larger than the typical off-grid
  correction and likely changes M3's merge topology under `VIA_VIA34`,
  needing the same kind of M3/M2/M1 cascade re-validation
  `apply_dynamic_v2m3_fix`/`apply_dynamic_v1m2_fix` already do for the
  off-grid case - just not yet extended to cover this one too. A real,
  understood, and bounded next step (20 instances, known mechanism) for
  whoever picks this up, but not shipped without that cascade handling.
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

## Token-normalized scoring (organizer formula, see README.md/evaluator/README.md)

Final per-block ranking is not raw repair rate - it's net DRC violations fixed,
normalized per million tokens spent:

`net_violations_fixed_per_million_tokens = original_violations * (1 - ε) * 1,000,000 / scoring_tokens`

where `ε` is the final violation rate and `scoring_tokens` is the greater of
`total_tokens` and organizer-set `MINIMUM_SCORING_TOKENS` (not locally
discoverable - `evaluate_repair.py` has no token-scoring code at all; this is
computed organizer-side from real usage logs against the real Vertex
endpoint). Submissions are ranked by this score descending; raw repair rate
(`γ`) is only the tie-breaker, not the primary metric. Practical implication:
`agent.py`'s current design (near-zero LLM calls - see "Local evaluation
works" above and the deterministic-repair approach throughout this document)
is favorable under this formula precisely because `scoring_tokens` stays at
the organizer-set floor rather than growing with usage - a token-heavy agent
with the same repair rate would score lower. `AGENT_GUIDE.md` also states
agents should "prioritize preserving a runnable KLayout script and the
provided connectivity reference before optimizing DRC violation counts" -
i.e. a valid, connectivity-preserving script with a modest repair rate beats
an invalid or connectivity-broken one, regardless of token cost.

## OpenROAD legalizer track: "rip up and reroute" (setup done, integration not started)

Idea (from earlier discussion): rather than only patching individual via/pad
shapes in place, use OpenROAD's real detailed-placement legalizer to move
offending standard cells to legal, DRC-clean positions with minimal
displacement, then re-stitch routing - a fundamentally different, more
powerful approach than this repo's current per-rule geometric patches. This
section records what's confirmed ready vs. what's still unbuilt.

**Confirmed working (2026-07-26):**
- `docker pull openroad/orfs:latest` (OpenROAD Flow Scripts, image digest
  `sha256:3bc303869d5e4caac8f72c854f2b1614c726b2961bbb372f54bc8fbc0e725e71`,
  6.48GB) - chosen specifically because ORFS bundles ASAP7 as one of its own
  reference platforms, confirmed via the upstream repo before pulling.
- `openroad` binary works inside the container after `source
  /OpenROAD-flow-scripts/env.sh` (not on PATH by default) - version
  `26Q3-771-g7cfb2105c9`.
- Real ASAP7 PDK files are bundled at `/OpenROAD-flow-scripts/flow/platforms/asap7/`:
  `lef/` (tech LEF variants `asap7sc7p5t_28_{L,R,SL}_1x_220121a.lef` plus
  per-cell LEFs for DFFs/SRAM/regfile macros), `lib/` (Liberty timing),
  `gds/`, `KLayout/` (tech files), `drc/`. Verified
  `asap7sc7p5t_28_SL_1x_220121a.lef` alone contains 212 real standard-cell
  `MACRO` entries (`AND2x2_ASAP7_75t_SL`, etc.) - this is a genuine,
  complete standard-cell LEF, not just memory/DFF macros.

**Not yet started - the actual integration gap:** our ASU testcase data
(`testcase/asap7/`) is purely KLayout-native (GDS + `.lydrc`/`.lyp`, produced
by `pya`-based layout scripts) - there is no LEF/DEF anywhere in the
provided testcase. OpenROAD's `detailed_placement` legalizer
(`dpl`/OpenDP - see
https://openroad.readthedocs.io/en/latest/main/src/dpl/README.html) operates
on a LEF+DEF design database, not GDS directly. The still-unbuilt bridge:

1. Extract current instance placement (name, cell type, x/y, orientation)
   from a block's generated GDS - probably via `pya`'s
   `top.each_inst()` on the block's own output GDS, not by re-parsing the
   Python layout script.
2. Emit a DEF (or build the design directly via OpenROAD's `odb` Python
   API, skipping a DEF round-trip) using the real ASAP7 LEF cell names
   above, in the ASAP7 tech LEF's DBU/site/row convention.
3. Run `detailed_placement -max_displacement <disp>` (start with a small
   cap - these blocks are small, and large displacement would require
   re-routing far more than the immediate local nets) against the loaded
   ASAP7 LEF, then `check_placement` to confirm legality.
4. Read back legalized positions, diff against step 1's original positions,
   and apply that per-instance delta to the block's own KLayout Python
   script (keeping it as a runnable klayout script, per `AGENT_GUIDE.md`'s
   priority - not replacing it with raw DEF/LEF output).
5. **Hardest unsolved part**: any wire/via touching a moved cell needs
   re-stitching. Full re-route via OpenROAD's global+detailed router would
   need the whole design (not just placement) translated into LEF/DEF
   including routing layers - a much bigger lift than steps 1-4. A cheaper
   alternative worth trying first: only re-stitch the local nets touching
   cells that actually moved, leaving untouched routing alone.

Next session: prototype steps 1-3 against one real block (e.g. Block1) to
confirm `detailed_placement` actually runs and produces a legal result
before investing in steps 4-5.

## Steps 1-3 prototyped and verified against Block1 (2026-07-26) - real result

### Extraction (step 1)

Ran `Block1.py` in KLayout batch mode and walked the resulting `Block1` cell's
instances directly (`top.each_inst()`) rather than re-parsing the script or
round-tripping through the GDS the script itself writes. Of 652 total
instances, 143 are real standard cells matching the bundled ASAP7 LEF's exact
naming (`BUFx3_ASAP7_75t_R`, `FAx1_ASAP7_75t_R`, `TAPCELL_ASAP7_75t_R`, etc. -
all 212 macros in `asap7sc7p5t_28_R_1x_220121a.lef` cover every cell type this
block actually uses); the other 509 are `VIA_*` routing-primitive cells with
no LEF entry (correctly excluded - not legalizer targets).

### Orientation mapping bug caught and fixed (important - would have been silent)

Building the DEF requires mapping `pya.Trans`'s packed rotation code (0-7,
where 4-7 encode rotation + `is_mirror()=True`) to DEF's `ORIENT` strings (N,
S, E, W, FN, FS, FE, FW). My first attempt was a guess based on typical
rotation-then-mirror composition order and was **wrong for all 4 of the
mirrored codes**. Caught by empirically verifying against the real OpenROAD
`odb` API instead of trusting the guess: generated one-instance DEFs for all
8 `ORIENT` strings using a real cell (`BUFx3_ASAP7_75t_R`), read them back via
`ord::get_db_block`, and compared the resulting `VDD`/pin-`A` pin bounding
boxes against the same 8 `pya.Trans` codes applied to the cell's real LEF pin
geometry (re-anchored to the cell's bbox corner, matching DEF's placement
convention). Verified mapping (used in the actual DEF generator):
`{0:N, 1:W, 2:S, 3:E, 4:FS, 5:FW, 6:FN, 7:FE}` - note 4-7 map to
`FS/FW/FN/FE`, not the naively-guessed `FN/FE/FS/FW`.

### DEF generation (step 2) - format gotchas

- KLayout's DBU (0.00025 um/unit) and the ASAP7 tech LEF's DBU
  (`DATABASE MICRONS 1000`, i.e. 0.001 um/unit) differ by exactly 4x; every
  instance coordinate extracted from Block1 converts to an exact integer DEF
  coordinate under `* 0.25` (verified for all 143 instances - no rounding/
  off-grid surprises).
- Row grid: the real site (`asap7sc7p5t`, `SIZE 0.054 BY 0.270`) is 270 DEF
  units tall; Block1's 143 cells only ever land on *even* row-indices of that
  grid (never odd) - odd rows sit empty. Rows are defined at the true site
  pitch (not a doubled pitch) spanning the observed instance bounding box.
- DEF's `ROW` section has **no** `ROWS <n> ;`/`END ROWS` wrapper (unlike
  `COMPONENTS`) - just consecutive `ROW` lines. Got this wrong on the first
  attempt (copied the `COMPONENTS`-style wrapper pattern) and confirmed the
  fix against a real bundled example DEF
  (`flow/designs/nangate45/aes/aes_ng45_fp.def`).

### Legalization (step 3) - real, verified success

Loaded the generated `block1.def` with the real ASAP7 tech + cell LEF and ran
`check_placement` **before** touching anything: it failed with **70 real
overlap violations** (e.g. `inst_104_TAPCELL` at the exact same `(x,y)` as
`inst_105_TAPCELL`, just different orientation; `inst_116_FAx1` - a
0.756um-wide cell, not 0.324um like `BUFx3` - genuinely overlapping the
neighboring `FILLERxp5` by 0.432um). Spot-checked two of these by hand against
each cell's real LEF `SIZE` to confirm they are genuine overlaps, not an
artifact of the DEF conversion.

Ran `detailed_placement -max_displacement 2` (2 sites/rows cap): converged
from 44 violations to 0 in 9 iterations (negotiation legalizer), followed by
`check_placement` passing cleanly. Displacement stayed small: **total 28.7um
across all 143 cells, average 0.2um, max 1.1um**. Concretely, both spot-
checked overlaps were resolved with a clean single-row shift (270 DEF
units = 0.27um = exactly one row height) and zero X movement:
- `inst_104_TAPCELL`: `(432,1620)` -> `(432,1890)` (+1 row); `inst_105`
  (the identical-position duplicate) stayed put.
- `inst_116_FAx1`: `(2160,1080)` -> `(2160,1350)` (+1 row), resolving its
  X-overlap with `inst_115_FILLERxp5` by relocating to a clear row instead
  of shifting X.

**This is a materially different class of violation than the via/pad
shape-merge issues this repo's current geometric patches target** - genuine
standard-cell-level physical overlaps, not sub-cell notch/coverage issues.
The two approaches look complementary rather than overlapping in scope.

### Step 4 done: diffing + patching the KLayout script (2026-07-26)

**Extraction method changed - important correctness fix.** The step-1/2/3
prototype above extracted instances by executing `Block1.py` in KLayout and
walking `top.each_inst()`. Cross-checking that order against a second,
independent extraction (regexing `Block1.py`'s own source text for each
`cell_Block1.insert(pya.CellInstArray(...))` call, in file order) found
**129 of 143 instances in a different order** between the two methods -
`each_inst()`'s iteration order does not reliably follow insertion/source
order. This matters a lot here: patching the script back requires mapping
"the i-th legalized instance" to an exact source location, which is only
possible with the regex-based, source-order extraction. Re-ran the whole
DEF-generation + `detailed_placement` pipeline against the regex-ordered
list (same 143 cells, same die geometry, same class of result: 44
violations converged to 0 in 3 iterations this time, total displacement
28.5um / avg 0.2um / max 1.6um - consistent with the first run modulo
negotiation-order-dependent details).

**Patch mechanism**: the regex extraction captures each match's exact
string span (including the `Vector(X, Y)` sub-groups' character offsets),
so patching is a precise, minimal string replacement - only the two
integers inside each moved instance's `Vector(...)` call are rewritten
(processed in reverse text order so earlier edits don't shift later
offsets), leaving 100% of the rest of the 3764-line script byte-identical.
56 of 143 instances moved (mostly clean single-row shifts, i.e. exactly
+/-1080 KLayout units = +/-270 DEF units = +/-1 row height in Y with X
unchanged; a few larger, up to 5 rows, still within the 2um displacement
cap). The patched script (`Block1_legalized.py`) parses as valid Python and
runs cleanly through `klayout -b -r` (produces a GDS with no errors).

### Step 5 (re-routing): confirmed mandatory, not yet attempted - real numbers

Ran the patched, **placement-only** (no re-routing) script through the real
evaluator (`evaluator/evaluate_repair.py`) to see where things stand before
building any re-stitching logic. Result is unambiguous:

- `connectivity_preserved: false` - 460 missing connectivity sources, 171
  pin-endpoint mismatches, 69 routing-endpoint-count mismatches (out of 824
  connectivity sources checked).
- `original_violations: 244` -> **`final_violations: 1650`** (1406 *new*
  violations introduced, `repair_rate: 0.0`).
- `eligible_for_scoring: false`, `score_exclusion_reason:
  connectivity_not_preserved` - this would be disqualified outright under
  the benchmark's gated scoring policy, not merely scored poorly.

**Conclusion: moving cells without re-stitching the wires/vias that
connected to their old pin locations does not just leave the design
partially unfixed - it actively destroys it.** Re-routing is a hard
prerequisite for this technique to produce any net benefit, not an
optional refinement. This is real, evaluator-verified data, not a
prediction - worth treating as settled going into whatever attempts
re-routing next.

### Re-routing: not attempted this session - options considered, deliberately left open

Two candidate approaches were discussed and neither has been started:

1. **Cheap/partial**: for each moved cell, identify the short wire/via
   shapes that connect *exclusively* to that cell's own pins (by
   proximity to the pin location) and translate just those shapes by the
   same delta as the cell - skipping any wire that also touches a
   stationary cell (which would need real re-routing, not just
   translation). Most of the 56 moves are clean, uniform single-axis
   shifts, which is favorable for this approach, but it will not fix
   violations on shared inter-cell wires, and "which shapes belong
   exclusively to which cell's pins" isn't captured by anything built so
   far - would need to be derived from the per-block `connectivity/*.json`
   reference and/or proximity heuristics against the DRC evaluator's own
   `missing_connectivity_source_details`/`routing_endpoint_count_mismatch_details`
   (both already show exactly which polygons broke, by position - useful
   ground truth for building and checking this against).
2. **Full**: translate the whole design including routing layers into
   LEF/DEF and run OpenROAD's global+detailed router
   (`global_route`/`detailed_route`) to recompute affected routing
   properly, then translate the routed DEF's wire/via geometry back into
   the KLayout script format. More correct, substantially more effort -
   net extraction, matching this PDK's exact via-stack conventions, and a
   DEF-routing-to-KLayout-polygon translator all still need to be built
   from scratch.

Neither was attempted - stopping here per instruction, to keep this
session's work fully verified rather than leaving partially-built,
unverified re-routing code behind.

### Artifacts from this session (all throwaway prototypes, not integrated into `agent.py`)

`extract_instances.py`/`extract_instances2.py` (pya-execution vs.
regex-based extraction - use the regex one), `resolve_orient.py`/
`check_orient*.py`/`check_trans*.py` (orientation mapping verification),
`gen_def.py` (DEF generator), `run_legalize.tcl` (loads LEF+DEF, runs
`detailed_placement`, writes the legalized DEF), `diff_and_patch.py` (diffs
legalized vs. original positions, patches `Block1.py`). None of this lives
in the actual submission yet (`agent.py` is unchanged) - it's all been run
from a scratch WSL directory (`~/asu_eval`) against a copy of `Block1.py`,
proving the pipeline works end-to-end through step 4 before any of it gets
wired into the real agent.
