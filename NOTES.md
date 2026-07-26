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
blocks, current locked-in state (v4 grid formula + v5 merge-aware V2.M3.AUX.2
+ the original 3 fixed-target fixes, v1m2 NOT applied):**

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
- `M5.AUX.1`/`M6.AUX.1`: not yet investigated at all - may share the same
  co-located-pair mechanism as `M4.AUX.1` (both via cells also carry M5/M6
  shapes - see `VIA_VIA45_1_2_58_58`'s `p110` and `VIA_VIA56_2_2_66_58`'s
  `p114`/`p115`), or may need their own investigation.
- `V0.M1.AUX.3` (37 violations): spread across multiple different standard
  logic cells (`BUFx2`, `INVx2`, `INVx3`, `BUFx3`, `FAx1`, `BUFx6f`) - likely
  as reuse-sensitive as `VIA_VIA12` above; needs per-cell-family
  instance-count and per-instance-context checking before attempting.
  Deferred, not attempted.
- `V1.M1.EN.1` / `V1.M2.AUX.2`: see the "`V1.M2.AUX.2` cascade" section above -
  the mechanism and a working per-via safe-range computation are both built
  (`apply_dynamic_v1m2_fix()`), but it's deliberately not called from
  `main()` because it breaks connectivity (grows the M1 pad into unrelated
  nearby M1 shapes never checked for safety). Needs an M1-side safe-range
  check intersected with the existing M2-side one before it can ship.
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
