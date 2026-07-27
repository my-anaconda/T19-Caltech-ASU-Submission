#!/usr/bin/env python3
"""
T19 (Caltech) - ASU ICLAD 2026 Block-Repair Submission
=========================================================
See NOTES.md for the full investigation. Short version of what this agent
does and why:

This benchmark's scoring is gated lexicographic - a repaired script only
counts if it (1) renders/DRCs cleanly in KLayout and (2) preserves reference
connectivity; among eligible submissions, lower final_violation_rate wins,
tie-broken by higher repair_rate. Every one of T19's 10 prior agent attempts
scored WORSE than the untouched original script (which itself measures
final_violation_rate=1.29 on live re-evaluation, not 1.0 - see NOTES.md for
why the static given DRC report undercounts 3 specific grid rules).

Real, KLayout-validated progress beyond that floor came from a specific,
narrow pattern: several DRC rules ("VX must exactly match the width of MY
perpendicular to MY's length", checked via KLayout's `.ongrid`/edge-coincidence
DRC primitives) are violated because a via's LOCAL polygon in its ASAP7 PDK
library cell definition doesn't span the full width of the FLATTENED, merged
metal region it actually sits inside once instantiated (which can be wider
than the via cell's own isolated local shapes, since metal regions merge
across adjacent cell instances). Growing the via (and its enclosing metal
layer, to keep the via "inside" it) to match the TRUE merged extent - verified
per-fix via a real KLayout DRC re-run, not assumed - fixed 144 of Block1's 244
violations (V2.M3.AUX.2, V4.M5.AUX.2, V5.M6.AUX.2) with connectivity fully
preserved, taking final_violation_rate from 1.29 down to 0.93 and repair_rate
from 0.0 to 0.59 - the first genuine repairs recorded against this benchmark.

These are the ASAP7 PDK's own standard via library cells (VIA_VIA23_1_3_36_36,
VIA_VIA45_1_2_58_58, VIA_VIA56_2_2_66_58) - not Block1-specific - so the same
edits are attempted on every case. They match STRUCTURALLY (by via-cell name,
GDS layer, and occurrence-index within that layer), not by literal source
text: each block's script defines these same cells with byte-identical local
geometry but different auto-generated variable names, so a naive exact-string
match (v1 of this agent) silently no-op'd on every block except the one it
was written against - see NOTES.md's "Generalizing beyond Block1" section.
Structural matching applies automatically wherever the expected
{layer: shape-count} structure is found, and is inert (skipped, logged)
wherever it isn't, degrading gracefully to the safe floor rather than
guessing. A DIFFERENT via cell family (VIA_VIA12, used far more ubiquitously
throughout the design for base M1<->M2 vias) was attempted with the same
technique and made things dramatically worse instead (see NOTES.md) - a
reminder that "the same family of fix" is not automatically safe to
generalize, which is why it is NOT included here.

A second, independent fix (apply_grid_alignment_fixes) targets M4.AUX.1
(M4 grid-alignment): VIA_VIA45_1_2_58_58 (M4<->M5 via) and
VIA_VIA34_1_2_58_52 (M3<->M4 via) are always co-located at the identical
placement vector, so their M4 pads merge into one shape. Shifting only one
of the pair breaks 4 OTHER rules (confirmed via real KLayout re-run, not
assumed) - they must move together. Even shifted together, only rows that
also land on a legal M4.AUX.2 "track" position (a sparser grid than the raw
24nm one M4.AUX.1 checks) are safe; unsafe rows and two other off-grid
residue classes (12/18, which broke other rules when tested) are left
untouched and logged - see NOTES.md's "M4 grid alignment" section.

Verified via real KLayout DRC re-run + connectivity check against all 5
available blocks (Block1/2/3/6/7), each beating its own true pristine floor.

Everything above is fully deterministic - the model is never consulted.
M4.S.5 ("parallel run length" spacing) is fixed differently, on purpose:
candidate generation and safety-checking are still pure stdlib arithmetic,
but which candidate ships (if any) is a genuine model call whose answer
actually reaches the repaired script - not the pre-existing "DRC analysis"
call further down, whose output is logged only. A bad or unparseable model
response is re-validated against the real candidate list and can only
degrade to a safe no-op, never an unsafe edit. See apply_llm_m4s5_fix() and
NOTES.md's "M4.S.5" section for the full derivation, including why 3 of
Block1's 4 instances are deliberately left untouched (their limiting edge
belongs to a high-reuse via macro, the same blast-radius class that made
blind VIA_VIA12 edits catastrophic above).

Run via the benchmark runner:
  python3 scripts/run_block_benchmark.py --case Block1 --agent-path agent.py --run-id t19-v3
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}


# ---------------------------------------------------------------------------
# Validated fixes, applied structurally rather than by literal-text match.
#
# Originally (v1) these were exact-string edits keyed to Block1's specific
# variable names (p101, p103, ...). That worked for Block1 but silently
# no-op'd on every other block: each block's script defines the SAME PDK via
# cells with the SAME local polygon coordinates, but auto-generated variable
# names differ per block (e.g. Block1's p101 is Block2's p75) - confirmed by
# direct diffing of Block1.py/Block2.py's cell definitions. So the fix now
# matches structurally: for each target via cell, find its constituent
# pXXX = pya.Polygon(...) / cell_<NAME>.shapes(...).insert(pXXX) statement
# pairs, group them by GDS layer (preserving each layer's own appearance
# order), and require the found (layer -> shape count) structure to exactly
# match what was validated on Block1 before touching anything - otherwise the
# whole cell is skipped and logged, never guessed at.
#
# V4.M5.AUX.2 (VIA_VIA45_1_2_58_58) and V5.M6.AUX.2 (VIA_VIA56_2_2_66_58)
# below use FIXED target extents (480/640) - confirmed by direct diffing of
# Block1/2/3/6/7 that every violating shape for these 2 rules has
# byte-identical local dimensions in every block that has them, and that the
# grid-alignment fix further down never changes these two rules' true merged
# extents (cross-checked across all 5 blocks: 0 instances ever need a
# non-default target - see NOTES.md). V2.M3.AUX.2 (VIA_VIA23_1_3_36_36) is
# handled separately, dynamically, below: growing a co-located VIA_VIA34
# instance's M3 pad (see the grid-alignment section) CAN change the true
# merged M3 extent a NEARBY, un-shifted VIA_VIA23 instance must match - a
# fixed target silently breaks for those instances (confirmed via real
# KLayout DRC re-run - see NOTES.md's "Merge-aware via-growth" section).
# ---------------------------------------------------------------------------

def _grow_y(half):
    """Keep the shape's X range, set its Y range to +/-half."""
    def _t(x0, y0, x1, y1, x2, y2, x3, y3):
        return (x0, -half, x1, half, x2, half, x3, -half)
    return _t


def _grow_x(half):
    """Keep the shape's Y range, set its X range to +/-half."""
    def _t(x0, y0, x1, y1, x2, y2, x3, y3):
        return (-half, y0, -half, y1, half, y2, half, y3)
    return _t


def _set_y_range(y0, y1):
    """Keep the shape's X range, set its Y range to exactly [y0, y1] -
    unlike _grow_y, not necessarily symmetric about 0 (the merge-aware
    fixes below can need an asymmetric range)."""
    def _t(x0, oy0, x1, oy1, x2, oy2, x3, oy3):
        return (x0, y0, x1, y1, x2, y1, x3, y0)
    return _t


# cell name -> { layer: [transform_or_None, ...] } in each layer's own
# file-appearance order. `None` means "found here, but intentionally left
# unchanged" (either already correct, or a deliberately-deferred edit - see
# NOTES.md's "Fixes that didn't work" for why not every shape in a matched
# cell gets touched).
CELL_FIX_SPECS = {
    # V4.M5.AUX.2 / V4.M4.EN.1: M4 pad X half-extent 208 -> 284 (keeps V4
    # enclosed per V4.M4.EN.1); both V4 vias -> X +/-240 (M5's true merged
    # perpendicular extent is 480).
    "VIA_VIA45_1_2_58_58": {
        50: [None],
        40: [_grow_x(284)],                       # M4 landing pad
        45: [_grow_x(240), _grow_x(240)],          # 2x V4 via
    },
    # V5.M6.AUX.2: 2 of 4 V5 vias -> Y +/-320 (M6's true merged perpendicular
    # extent is 640). The other 2 are left unchanged - they become a
    # harmless subset of the newly-grown pair at the same X range.
    "VIA_VIA56_2_2_66_58": {
        50: [None],
        60: [None],                                # already at the correct extent
        55: [_grow_y(320), _grow_y(320), None, None],  # 4x V5 via, only first 2 grown
    },
}

_POLY_INSERT_RE_TMPL = (
    r"(?P<var>p\w+) = (?P<rhs>pya\.Polygon\(\[pya\.Point\((?P<x0>-?\d+), (?P<y0>-?\d+)\), "
    r"pya\.Point\((?P<x1>-?\d+), (?P<y1>-?\d+)\), pya\.Point\((?P<x2>-?\d+), (?P<y2>-?\d+)\), "
    r"pya\.Point\((?P<x3>-?\d+), (?P<y3>-?\d+)\)\]\))\r?\n"
    r"cell_{cell}\.shapes\(layout\.layer\(pya\.LayerInfo\((?P<layer>\d+), 0\)\)\)\.insert\((?P=var)\)"
)


def _apply_cell_fix_specs(script_text, cell_fix_specs):
    """Applies each fix in `cell_fix_specs` structurally (see module docstring
    and the comment block above). Returns (patched_text, applied_list, skipped_list)."""
    applied = []
    skipped = []

    for cell_name, layer_spec in cell_fix_specs.items():
        pattern = re.compile(_POLY_INSERT_RE_TMPL.format(cell=re.escape(cell_name)))
        matches = list(pattern.finditer(script_text))
        if not matches:
            skipped.append((cell_name, "cell not present in this case"))
            continue

        by_layer = defaultdict(list)
        for m in matches:
            by_layer[int(m.group("layer"))].append(m)

        extra_layers = set(by_layer) - set(layer_spec)
        structure_ok = not extra_layers
        if structure_ok:
            for layer, transforms in layer_spec.items():
                if len(by_layer.get(layer, [])) != len(transforms):
                    structure_ok = False
                    break

        if not structure_ok:
            skipped.append((cell_name, f"structure mismatch: found layers={dict((l, len(v)) for l, v in by_layer.items())}"))
            continue

        # Build (start, end, replacement) edits, applied back-to-front so
        # earlier offsets stay valid.
        edits = []
        for layer, transforms in layer_spec.items():
            for match, transform in zip(by_layer[layer], transforms):
                if transform is None:
                    continue
                coords = tuple(int(match.group(g)) for g in
                                ("x0", "y0", "x1", "y1", "x2", "y2", "x3", "y3"))
                nx0, ny0, nx1, ny1, nx2, ny2, nx3, ny3 = transform(*coords)
                var = match.group("var")
                new_poly = (
                    f"{var} = pya.Polygon([pya.Point({nx0}, {ny0}), "
                    f"pya.Point({nx1}, {ny1}), pya.Point({nx2}, {ny2}), "
                    f"pya.Point({nx3}, {ny3})])"
                )
                edits.append((match.start("var"), match.end("rhs"), new_poly))
                applied.append(f"{cell_name}:layer{layer}:{var}")

        for start, end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
            script_text = script_text[:start] + replacement + script_text[end:]

    return script_text, applied, skipped


def apply_validated_fixes(script_text):
    """Applies the fixed-target via-growth fixes (V4.M5.AUX.2, V5.M6.AUX.2).
    See apply_dynamic_v2m3_fix() for V2.M3.AUX.2, which needs a
    merge-aware, per-instance target instead of a fixed one."""
    return _apply_cell_fix_specs(script_text, CELL_FIX_SPECS)


# ---------------------------------------------------------------------------
# General script-geometry engine, shared by the grid-alignment fix and the
# merge-aware V2.M3.AUX.2 fix below. Pure stdlib (re + collections) - no pya
# dependency at grading time. Every piece of this was cross-validated against
# real KLayout (pya.Region/Box.transformed) before being trusted - see
# NOTES.md's "Merge-aware via-growth" section:
#   - transform_bbox()'s rotation/mirror formula reproduces pya.Trans exactly
#     (tested all 8 rot/mirror combinations against pya.Box.transformed()).
#   - merge_rects() reproduces pya.Region.merged() for the 3 known via-growth
#     targets exactly (136/480/640, matching NOTES.md's original derivation).
# ---------------------------------------------------------------------------

# Matches pya.Polygon([...]) with ANY number of points (>= 3), not just the
# 4-point rectangles this engine originally assumed. Confirmed necessary by
# direct inspection: unrelated standard cells (e.g. BUFx2_ASAP7_75t_R) define
# non-rectangular M1 shapes with 8-12 points (real jogged routing shapes) -
# a fixed 4-point pattern silently skips these entirely, which is exactly
# what broke the first M1-safety check attempt (it found zero obstacles
# where real KLayout pya.Region found a very real one - see NOTES.md's
# "M1 safety" section). Bbox-only is still safe to use here: every consumer
# either (a) treats OTHER cells' shapes as keep-out obstacles, where a
# bounding box is a conservative (never unsafe) over-approximation, or
# (b) operates on the validated via/pad cells this engine already confirmed
# are plain 4-point rectangles (via the separate, still-4-point-specific
# _POLY_INSERT_RE_TMPL used for the known fixed-target edits).
_POINT_RE = re.compile(r"pya\.Point\((-?\d+), (-?\d+)\)")
_ANY_POLY_INSERT_RE = re.compile(
    r"(?P<var>p\w+) = pya\.Polygon\(\[(?P<points>pya\.Point\(-?\d+, -?\d+\)"
    r"(?:, pya\.Point\(-?\d+, -?\d+\))*)\]\)\r?\n"
    r"cell_(?P<cellvar>\w+)\.shapes\(layout\.layer\(pya\.LayerInfo\((?P<layer>\d+), 0\)\)\)\.insert\((?P=var)\)"
)
_ANY_INST_RE = re.compile(
    r"cell_(?P<topvar>\w+)\.insert\(pya\.CellInstArray\(cell_(?P<subvar>\w+)\.cell_index\(\), "
    r"pya\.Trans\((?P<rot>-?\d+), (?P<mirror>True|False), pya\.Vector\((?P<x>-?\d+), (?P<y>-?\d+)\)\)\)\)"
)


def _poly_bbox(pts):
    xs = pts[0::2]
    ys = pts[1::2]
    return (min(xs), min(ys), max(xs), max(ys))


def _parse_all_shapes(script_text):
    """Returns {cellvar: {layer: [bbox, ...]}} for every pXXX=Polygon(...)/
    .insert(pXXX) statement in the script (any cell, any layer, any number of
    points >= 3, file order). Shapes are reduced to their bounding box - see
    _ANY_POLY_INSERT_RE's comment for why that's safe for every current use."""
    shapes = defaultdict(lambda: defaultdict(list))
    for m in _ANY_POLY_INSERT_RE.finditer(script_text):
        cellvar = m.group("cellvar")
        layer = int(m.group("layer"))
        coords = _POINT_RE.findall(m.group("points"))
        pts = tuple(int(v) for pair in coords for v in pair)
        shapes[cellvar][layer].append(_poly_bbox(pts))
    return shapes


def _parse_all_instances(script_text):
    """Returns list of (topvar, subvar, rot, mirror, x, y) for every instance
    placement in the script, regardless of which cell."""
    return [
        (m.group("topvar"), m.group("subvar"), int(m.group("rot")),
         m.group("mirror") == "True", int(m.group("x")), int(m.group("y")))
        for m in _ANY_INST_RE.finditer(script_text)
    ]


def _transform_bbox(bbox, rot, mirror, dx, dy):
    # pya.Trans(rot, mirror, x, y) semantics, determined empirically against
    # pya.Box.transformed() (all 8 rot/mirror combinations): mirror negates Y
    # first, then rotate by (rot % 4) * 90deg CCW, then translate. This
    # design only ever uses rot % 4 in {0, 2} (0deg/180deg) for cells that
    # touch layers 20/25/30/35/40/45/50/55/60 - true 90/270 rotation is only
    # used by standard cells on M1, which this engine never needs to
    # flatten. Assert rather than silently mishandle a case never observed.
    quarter = rot % 4
    assert quarter in (0, 2), f"unexpected 90/270 rotation rot={rot} on a merge-relevant layer"
    x0, y0, x1, y1 = bbox
    if mirror:
        y0, y1 = -y1, -y0
    if quarter == 2:
        x0, y0, x1, y1 = -x1, -y1, -x0, -y0
    return (x0 + dx, y0 + dy, x1 + dx, y1 + dy)


def _flatten_layer(topcell_var, layer, shapes, instances):
    """Absolute bboxes for `layer` under `topcell_var`: shapes placed
    directly in the top cell (already absolute) plus shapes inside any named
    sub-cell instantiated under it (transformed per-instance)."""
    boxes = list(shapes.get(topcell_var, {}).get(layer, []))
    for topvar, subvar, rot, mirror, x, y in instances:
        if topvar != topcell_var:
            continue
        for bbox in shapes.get(subvar, {}).get(layer, []):
            boxes.append(_transform_bbox(bbox, rot, mirror, x, y))
    return boxes


def _touch_or_overlap(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return ax0 <= bx1 and bx0 <= ax1 and ay0 <= by1 and by0 <= ay1


def _merged_group_members_containing(boxes, target_box):
    """Connected-component merge of axis-aligned bboxes that touch or
    overlap (matches KLayout Region.merged() for simple rectangles);
    returns the list of member boxes for whichever group contains
    `target_box` (or None if target_box isn't found)."""
    n = len(boxes)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        for j in range(i + 1, n):
            if _touch_or_overlap(boxes[i], boxes[j]):
                union(i, j)

    try:
        target_idx = boxes.index(target_box)
    except ValueError:
        return None
    root = find(target_idx)
    return [boxes[i] for i in range(n) if find(i) == root]


def _safe_y_range_for_x_range(group_members, x0, x1):
    """Within a single already-identified connected group of bboxes, finds
    the Y range continuously covered by SOME member box at every X point
    across [x0, x1] (the "vertical slice intersection" as X sweeps the
    range) - i.e. the largest Y-extent a shape spanning exactly [x0, x1]
    could grow to while staying strictly inside the group's true (possibly
    non-rectangular) shape. Returns (ymin, ymax), or None if there's a gap
    anywhere in [x0, x1] (shouldn't happen for a shape already confirmed to
    be part of this group, but checked rather than assumed).

    Scoped to a pre-identified group's members (not the whole layer) - a
    naive whole-layer version would incorrectly pick up unrelated, far-away
    shapes that happen to share an X coordinate."""
    breakpoints = {x0, x1}
    for (bx0, by0, bx1, by1) in group_members:
        if x0 < bx0 < x1:
            breakpoints.add(bx0)
        if x0 < bx1 < x1:
            breakpoints.add(bx1)
    breakpoints = sorted(breakpoints)

    overall_ymin, overall_ymax = None, None
    for i in range(len(breakpoints) - 1):
        mid = (breakpoints[i] + breakpoints[i + 1]) / 2
        covering = [b for b in group_members if b[0] <= mid <= b[2]]
        if not covering:
            return None
        ymin = min(b[1] for b in covering)
        ymax = max(b[3] for b in covering)
        if overall_ymin is None:
            overall_ymin, overall_ymax = ymin, ymax
        else:
            overall_ymin = max(overall_ymin, ymin)
            overall_ymax = min(overall_ymax, ymax)
    return (overall_ymin, overall_ymax)


# ---------------------------------------------------------------------------
# M4.AUX.1 / M4.AUX.2 grid-alignment fix (the "deferred" rule family from
# NOTES.md).
#
# VIA_VIA45_1_2_58_58 (M4<->M5 via) and VIA_VIA34_1_2_58_52 (M3<->M4 via) are
# always placed at the exact same (X, Y) instance-placement vector, so their
# M4 pads fully overlap into one merged shape. That pad's local geometry
# (established from VIA_VIA45's p111, unaffected by the width-only fix
# above) is symmetric about the placement Y (local Y range -48..+48 raw
# units), so the merged pad's centerline in the design equals the placement
# Y exactly. Confirmed via real KLayout DRC re-run: shifting ONLY one of the
# pair breaks 4 other rules (M4.AUX.2/3, M4.S.4, V3.M4.AUX.2) - the pads
# must move together.
#
# The exact legality condition below is not a guess: `asap7.lydrc` (the
# design rule deck) defines M4.AUX.2 via a custom `offgrid_cl(:y, 192, 48,
# 96)` Ruby method, whose source (read directly, not inferred) is:
#   - only shapes already on the base_dbu=96 grid are checked at all
#     (this is exactly M4.AUX.1's 24nm grid, in raw dbu: 96 raw units)
#   - among those, a shape's Y-centerline `cl` must satisfy
#     (cl - offset_dbu) % pitch_dbu == 0, i.e. (cl - 48) % 192 == 0
# Since our pad's centerline equals the raw placement Y, and 192 = 2x96,
# satisfying (Y - 48) % 192 == 0 automatically satisfies the M4.AUX.1 grid
# condition too - so searching for the nearest Y with that one property
# fixes both rules at once. This formula was cross-checked against 10 real
# KLayout DRC re-runs (5 rows x 2 directions each) with zero mismatches
# before being trusted - see NOTES.md's "M4 grid alignment" section.
#
# Combining this with the via-growth fixes surfaced a further, subtler
# problem (see NOTES.md's "Merge-aware via-growth" section): shifting
# VIA_VIA34's M3 pad can change the TRUE merged M3 extent a nearby,
# un-shifted VIA_VIA23_1_3_36_36 instance must match for V2.M3.AUX.2 - so the
# grid shifts computed here are also fed into apply_dynamic_v2m3_fix()
# below, which recomputes that fix's target per-instance against the
# POST-shift geometry rather than assuming the original fixed constant still
# applies everywhere.
# ---------------------------------------------------------------------------

_M4_GRID_RAW = 96     # 24nm, in raw dbu (dbu=0.25nm) - M4.AUX.1's grid pitch
_M4_TRACK_PITCH_RAW = 192   # offgrid_cl's pitch_dbu
_M4_TRACK_OFFSET_RAW = 48   # offgrid_cl's offset_dbu


def _is_m4_track_legal(y_raw):
    return (y_raw - _M4_TRACK_OFFSET_RAW) % _M4_TRACK_PITCH_RAW == 0


def compute_grid_shifts(instances):
    """Decides which co-located VIA_VIA45/VIA_VIA34 instance pairs should
    move, and to what new Y, WITHOUT touching any text - a pure data
    computation so the result can be used both for the actual placement
    edit and for recomputing merge-aware via-growth targets against the
    resulting (virtual) post-shift geometry.

    Returns (shift_map, applied_list, skipped_list) where shift_map is
    {(topvar, subvar, x, y): new_y} for every instance that should move
    (both members of each qualifying pair)."""
    via45 = {(t, x, y): True for (t, s, r, m, x, y) in instances if s == "VIA_VIA45_1_2_58_58"}
    via34 = {(t, x, y): True for (t, s, r, m, x, y) in instances if s == "VIA_VIA34_1_2_58_52"}

    shift_map = {}
    applied = []
    skipped = []

    for (t, x, y) in via45:
        if (t, x, y) not in via34:
            skipped.append((f"VIA_VIA45@({x},{y})", "no co-located VIA_VIA34 pair"))
            continue

        # M4.AUX.1's grid check is on the pad's EDGES, not its centerline:
        # the local pad is offset -48 raw units from the placement Y, so the
        # grid condition is (y - _M4_TRACK_OFFSET_RAW) % _M4_GRID_RAW == 0,
        # not y % _M4_GRID_RAW == 0.
        residue = (y - _M4_TRACK_OFFSET_RAW) % _M4_GRID_RAW
        if residue == 0:
            # NOTE: rows already on the 24nm grid but off the coarser 192nm
            # track (M4.AUX.2) are a real, confirmed gap (20 instances across
            # all 7 blocks) - but a real KLayout re-run of a candidate fix
            # (shift by a full 96 raw / one grid step, checked only for M4
            # spacing safety) broke connectivity on 2 of 7 blocks (26 pin +
            # 7 routing endpoint mismatches on Block1 alone). A 96-unit shift
            # is much larger than the typical off-grid correction below and
            # likely needs its own M3/M2/M1 cascade re-validation, the same
            # way the original off-grid fix did - not yet built. Left
            # unfixed rather than shipping something that broke connectivity
            # when actually tested. See NOTES.md.
            continue  # already on the M4.AUX.1 grid, nothing to do here

        up = y + (_M4_GRID_RAW - residue)
        down = y - residue
        up_legal = _is_m4_track_legal(up)
        down_legal = _is_m4_track_legal(down)

        if up_legal and not down_legal:
            new_y = up
        elif down_legal and not up_legal:
            new_y = down
        else:
            # Neither (or both) legal - not the expected case; skip rather
            # than guess.
            skipped.append((f"VIA_VIA45+VIA_VIA34@({x},{y})",
                             f"residue={residue}, no single confirmed-safe direction "
                             f"(up_legal={up_legal}, down_legal={down_legal})"))
            continue

        shift_map[(t, "VIA_VIA45_1_2_58_58", x, y)] = new_y
        shift_map[(t, "VIA_VIA34_1_2_58_52", x, y)] = new_y
        applied.append(f"VIA_VIA45+VIA_VIA34@({x},{y})->y={new_y}")

    return shift_map, applied, skipped


_PLACEMENT_RE_TMPL = (
    r"cell_{topcell}\.insert\(pya\.CellInstArray\(cell_{cell}\.cell_index\(\), "
    r"pya\.Trans\(0, False, pya\.Vector\({x}, {y}\)\)\)\)"
)


def apply_grid_alignment_fixes(script_text, shift_map):
    """Applies the placement-vector edits decided by compute_grid_shifts()."""
    edits = []
    for (topcell, subvar, x, y), new_y in shift_map.items():
        pattern = re.compile(_PLACEMENT_RE_TMPL.format(
            topcell=re.escape(topcell), cell=re.escape(subvar), x=x, y=y))
        matches = list(pattern.finditer(script_text))
        if len(matches) != 1:
            continue  # shouldn't happen; leave untouched rather than guess
        m = matches[0]
        new_line = m.group(0).replace(f"Vector({x}, {y})", f"Vector({x}, {new_y})")
        edits.append((m.start(), m.end(), new_line))

    for start, end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
        script_text = script_text[:start] + replacement + script_text[end:]
    return script_text


# ---------------------------------------------------------------------------
# M5.AUX.1 grid-alignment fix (M5 rail vertical edges off the 24nm grid).
# Fully deterministic - unlike M4.S.5 above, there is no ambiguous choice
# between candidates for a model to make: the safety check below either
# finds a real, provably-safe edit or it doesn't.
#
# M6.AUX.1 (the same kind of rule, one metal layer up) is NOT included here
# despite sharing the same mechanism and the same stub-check machinery below
# - see apply_grid_rail_fix()'s docstring for why: it looked net-neutral on
# Block1 alone but turned out to be a real regression on Block7 once
# actually cross-block-verified. Deferred, not abandoned.
#
# Earlier investigation (see NOTES.md) concluded this rule was intractable
# because the violating M5 shapes are genuine, block-spanning top-level
# rails (not small via-cell pads) - moving one seemed to risk the same
# high-reuse blast radius as VIA_VIA12. Revisited because growing a rail's
# edge OUTWARD (never shrinking) to the nearest grid line is a much
# smaller, more local change than "move the rail" - and because this is a
# single top-level polygon, not a shared library cell, so the only real
# risk is a different top-level shape (or a via-cell pad) that happens to
# rely on the rail's EXACT current edge position.
#
# That risk turned out to be real and precisely characterizable: a
# "stub" bug, caught by real KLayout re-run before shipping (see
# NOTES.md's "Fixes that didn't work" precedent for why this matters) -
# some via-cell M5 pads (e.g. VIA_VIA45_1_2_58_58) extend slightly BEYOND
# the rail's own bbox in Y at the exact edge being snapped. Growing only
# the rail's edge (not the via's own unrelated local pad) leaves that via's
# unchanged extension as a new, still-off-grid sliver - the fix appears to
# apply but the target violation count doesn't move, plus new M5.AUX.3/
# M5.S.4/M5.W.3 violations appear. The fix below detects this via a static
# "stub check" (does any OTHER shape on this layer, at this rail's own
# X-range, extend past the rail's Y-range at the edge being moved?) using
# the same _flatten_layer()/_transform_bbox() nested-instance machinery
# already validated elsewhere in this file - no live KLayout needed at
# grading time. A stubbed edge is skipped, not guessed at.
#
# Verified end-to-end (real CLI entrypoint, real evaluate_repair.py) across
# all 7 blocks: every block with a stub-free candidate shows a strict
# improvement (Block1 153->147, Block2 35->32, Block3 64->58, Block4 78->72,
# Block6 161->152, Block7 492->477 final_violations - repair_rate improves
# or holds steady in every case, connectivity_preserved stays true
# throughout). Block5 correctly produces zero candidates - every one of its
# off-grid M5 rails is stub-blocked. A small M5.W.3 (width) collateral (1-5
# instances) appears alongside the fix in every improved block - net
# positive every time, same "net win, not can never be worse" honesty as
# the rest of this file's fixes.
# ---------------------------------------------------------------------------

_M5_GRID_RAW = 96    # 24nm - M5.AUX.1's vertical-edge grid pitch
_M6_GRID_RAW = 128   # 32nm - M6.AUX.1's horizontal-edge grid pitch
_MIN_RAIL_LEN_RAW = 5000  # 1.25um - distinguishes a rail from a small via pad


def _snap_outward(value, grid, grow_positive):
    if value % grid == 0:
        return value
    return ((value // grid) + 1) * grid if grow_positive else (value // grid) * grid


def find_grid_rail_candidates(script_text, top_cell_var, layer_num, grid_raw, axis):
    """Finds every top-level rail-like rectangle on `layer_num` whose edge
    along `axis` ("x" for M5's vertical edges, "y" for M6's horizontal
    edges) is off the given grid, and is safe to snap outward to the
    nearest grid line - i.e. no other shape on the same layer, within this
    rail's own span on the OTHER axis, extends past the rail's own range on
    `axis` (the "stub" check - see module comment above). Returns a list of
    {var, span, side, old_val, new_val} - fully self-contained, ready to
    apply without any further judgment call."""
    shapes = _parse_all_shapes(script_text)
    instances = _parse_all_instances(script_text)
    top_level = shapes.get(top_cell_var, {}).get(layer_num, [])
    all_boxes = _flatten_layer(top_cell_var, layer_num, shapes, instances)

    insert_re = re.compile(
        rf"^cell_{re.escape(top_cell_var)}\.shapes\(layout\.layer\(pya\.LayerInfo\({layer_num}, 0\)\)\)"
        rf"\.insert\((p\d+)\)\s*$", re.MULTILINE)
    poly_def_re = re.compile(r"^(p\d+) = pya\.Polygon\(\[(.*?)\]\)\s*$", re.MULTILINE)
    poly_info = {}
    for m in poly_def_re.finditer(script_text):
        pts = tuple(int(v) for pair in _POINT_RE.findall(m.group(2)) for v in pair)
        if len(pts) != 8:
            continue
        bbox = _poly_bbox(pts)
        if len(set(pts[0::2])) != 2 or len(set(pts[1::2])) != 2:
            continue
        poly_info[m.group(1)] = {"bbox": bbox, "span": (m.start(2), m.end(2))}
    top_level_vars = {m.group(1) for m in insert_re.finditer(script_text)}
    var_by_bbox = {info["bbox"]: {"var": v, "span": info["span"]}
                   for v, info in poly_info.items() if v in top_level_vars}

    candidates = []
    for bbox in top_level:
        l, b, r, t = bbox
        rail_len = (t - b) if axis == "x" else (r - l)
        if rail_len < _MIN_RAIL_LEN_RAW:
            continue
        info = var_by_bbox.get(bbox)
        if not info:
            continue

        if axis == "x":
            edges = [("left", l, False), ("right", r, True)]
        else:
            edges = [("bottom", b, False), ("top", t, True)]

        for side, old_val, grow_positive in edges:
            if old_val % grid_raw == 0:
                continue
            new_val = _snap_outward(old_val, grid_raw, grow_positive)
            stub = False
            for ob in all_boxes:
                if ob == bbox:
                    continue
                obl, obb, obr, obt = ob
                if axis == "x":
                    if obr <= l - 4 or obl >= r + 4:
                        continue
                    # A genuine "stub" requires the obstacle to actually
                    # OVERLAP the rail's own Y-range (i.e. be part of the
                    # same merged region) AND extend beyond it - not merely
                    # sit somewhere further along X with no real contact.
                    # Caught by real DRC re-run: a candidate initially
                    # flagged here (Block6/Block7) turned out to be a
                    # completely separate, non-touching top-level shape (a
                    # real 340-raw-unit Y gap between it and the rail) -
                    # applying the edit was actually safe, proving the
                    # overlap check below is necessary, not redundant.
                    overlaps_y = not (obt <= b or obb >= t)
                    if overlaps_y and (obb < b or obt > t):
                        stub = True
                        break
                else:
                    if obt <= b - 4 or obb >= t + 4:
                        continue
                    overlaps_x = not (obr <= l or obl >= r)
                    if overlaps_x and (obl < l or obr > r):
                        stub = True
                        break
            if stub:
                continue
            candidates.append({
                "var": info["var"], "span": info["span"], "side": side,
                "old_val": old_val, "new_val": new_val,
            })
    return candidates


def apply_grid_rail_fix(script_text, top_cell_var):
    """Applies find_grid_rail_candidates() for M5 (layer 50, X-axis grid)
    only. Fully deterministic - see module comment above.

    M6 (layer 60, Y-axis grid, M6.AUX.1) is deliberately NOT included here
    despite the stub-check finding candidates for it too: cross-block
    verification (not just Block1) showed the M6.AUX.1 fix is not reliably
    net-neutral the way it first appeared. On Block1 it traded 1-for-1
    against a new V5.M6.AUX.2 violation per instance (net zero, harmless).
    On Block7 the SAME fix traded 24 M6.AUX.1 fixes for 36 new
    V5.M6.AUX.2 violations (net +12, a real regression) - the ratio isn't
    fixed 1:1 the way M5's stub-check assumed, and a wrong assumption here
    was only caught by re-running the real evaluator across every block,
    not by trusting the single-block result. M6.AUX.1 is deferred, not
    abandoned - the V5.M6.AUX.2 cascade needs its own investigation before
    this is safe to ship, the same lesson as M2.S.7/M3.S.2 above.
    Returns (patched_text, applied_list, skipped_list)."""
    applied, skipped = [], []
    all_candidates = find_grid_rail_candidates(script_text, top_cell_var, 50, _M5_GRID_RAW, "x")
    if not all_candidates:
        return script_text, applied, skipped

    by_var = {}
    for c in all_candidates:
        by_var.setdefault(c["var"], {"span": c["span"], "changes": []})
        by_var[c["var"]]["changes"].append((c["old_val"], c["new_val"], c["side"]))

    for var, info in sorted(by_var.items(), key=lambda kv: -kv[1]["span"][0]):
        start, end = info["span"]
        seg = script_text[start:end]
        for old_val, new_val, side in info["changes"]:
            if side in ("left", "right"):
                new_seg = re.sub(rf"pya\.Point\({old_val}, ", f"pya.Point({new_val}, ", seg)
            else:
                new_seg = re.sub(rf", {old_val}\)", f", {new_val})", seg)
            assert new_seg != seg, f"expected coordinate not found for {var}"
            seg = new_seg
        script_text = script_text[:start] + seg + script_text[end:]
        applied.append(f"{var}: {info['changes']}")

    return script_text, applied, skipped


# ---------------------------------------------------------------------------
# VIA_VIA45_1_2_58_58 stub fix - unblocks M5.AUX.1 rail edges that
# find_grid_rail_candidates() above correctly refuses to touch on their own,
# because this specific via's local M5 pad extends past the rail's own Y
# range at one end (see the module comment above apply_grid_rail_fix - the
# "stub" mechanism). Discovered via a genuine multi-round LLM-assisted
# investigation, each round checked against a real KLayout DRC re-run before
# trusting it - not a single-shot guess:
#
#   Round 1: shift both V4 vias by the same offset as the rail, keeping them
#   narrow and separate. Real DRC: M5.AUX.1 improved, but 2 NEW V4.M5.AUX.2
#   ("V4 must exactly match M5's width") and 1 new V4.S.1 (via-to-via
#   spacing) appeared - net zero.
#   Round 2: re-read .lydrc's actual V4.M5.AUX.2 check
#   (v4_aux2_coinc = v4_aux2_in.edges.and(m5.edges) - literal edge
#   coincidence, not proximity) and this codebase's own already-working fix
#   for the same rule (CELL_FIX_SPECS's _grow_x(240): makes each V4 via a
#   full-width duplicate of M5's pad, not a narrower offset copy). Applied
#   the same principle to the new width instead - real DRC: V4.M5.AUX.2 and
#   V4.S.1 fully resolved, but a NEW V4.M4.EN.1 (M4 must enclose V4 by
#   >=11nm) appeared, because M4's pad was left the same width as V4 with
#   zero margin.
#   Round 3: widen M4's pad past V4 by the required 44 raw units (11nm) on
#   each side. Real DRC: fully clean - only the same small M5.W.3 collateral
#   every other M5.AUX.1 fix in this file already produces.
#
# The converged formula turned out to have no ambiguous choice left in it -
# once "V4 must span M5's exact new width, M4 must enclose V4 by >=11nm" is
# known, there is exactly one answer, so this ships fully deterministic
# (unlike M4.S.5) even though an LLM helped find it.
#
# Verified end-to-end (real CLI entrypoint, real evaluate_repair.py) across
# every block with a matching stub: Block1 (2 instances), Block2, Block4,
# Block5 (1 each) - every one improves with only the expected M5.W.3
# collateral, zero other regressions, connectivity_preserved stays true.
# Combined with apply_grid_rail_fix() above, M5.AUX.1 is now FULLY resolved
# (0 remaining) in 6 of 7 blocks.
# ---------------------------------------------------------------------------

_VIA45_LOCAL_M5 = (-240, -92, 240, 92)     # layer 50
_VIA45_LOCAL_M4 = (-208, -48, 208, 48)     # layer 40
_VIA45_LOCAL_V4A = (68, -48, 164, 48)      # layer 45
_VIA45_LOCAL_V4B = (-164, -48, -68, 48)    # layer 45
_V4_M4_ENCLOSURE_RAW = 44  # 11nm, V4.M4.EN.1's own floor


def find_via_stub_candidates(script_text, top_cell_var):
    """Finds every off-grid M5 rail whose stub is caused SPECIFICALLY by a
    VIA_VIA45_1_2_58_58 instance with byte-identical local geometry to
    _VIA45_LOCAL_* above (confirmed unchanged across every block that has
    this via) extending past the rail's own Y-range at one end. Returns a
    list of {rail_var, rail_span, rail_old, rail_new, via_x, via_y,
    instance_count_for_naming}."""
    shapes = _parse_all_shapes(script_text)
    instances = _parse_all_instances(script_text)
    top_level = shapes.get(top_cell_var, {}).get(50, [])
    all_boxes = _flatten_layer(top_cell_var, 50, shapes, instances)

    via_local_m5 = shapes.get("VIA_VIA45_1_2_58_58", {}).get(50, [])
    if via_local_m5 != [_VIA45_LOCAL_M5]:
        return []  # not present, or local geometry differs - don't guess

    rect_re = re.compile(r"^(p\d+) = pya\.Polygon\(\[(.*?)\]\)\s*$", re.MULTILINE)
    poly_info = {}
    for m in rect_re.finditer(script_text):
        pts = tuple(int(v) for pair in _POINT_RE.findall(m.group(2)) for v in pair)
        if len(pts) != 8:
            continue
        bbox = _poly_bbox(pts)
        if len(set(pts[0::2])) == 2 and len(set(pts[1::2])) == 2:
            poly_info[bbox] = (m.group(1), (m.start(2), m.end(2)))

    candidates = []
    for bbox in top_level:
        l, b, r, t = bbox
        if (t - b) < _MIN_RAIL_LEN_RAW:
            continue
        if l % _M5_GRID_RAW == 0 and r % _M5_GRID_RAW == 0:
            continue
        rail_info = poly_info.get(bbox)
        if not rail_info:
            continue

        for topvar, subvar, rot, mirror, x, y in instances:
            if topvar != top_cell_var or subvar != "VIA_VIA45_1_2_58_58":
                continue
            via_bbox = _transform_bbox(_VIA45_LOCAL_M5, rot, mirror, x, y)
            vl, vb, vr, vt = via_bbox
            if vr <= l - 4 or vl >= r + 4:
                continue
            overlaps_y = not (vt <= b or vb >= t)
            if not (overlaps_y and (vb < b or vt > t)):
                continue
            new_l = l if l % _M5_GRID_RAW == 0 else _snap_outward(l, _M5_GRID_RAW, False)
            new_r = r if r % _M5_GRID_RAW == 0 else _snap_outward(r, _M5_GRID_RAW, True)
            candidates.append({
                "rail_var": rail_info[0], "rail_span": rail_info[1],
                "rail_old": (l, r), "rail_new": (new_l, new_r),
                "via_x": x, "via_y": y,
            })
            break  # at most one candidate per rail - every verified case has
                   # exactly one stub-causing via; don't double-process a rail
                   # if more than one instance happens to match.
    return candidates


def apply_via_stub_fix(script_text, top_cell_var):
    """Applies find_via_stub_candidates(): for each matching stub, creates a
    per-instance custom cell (VIA_VIA45_1_2_58_58_C<N>, following this
    file's established custom-cell convention) with the converged formula
    above, redirects only that one instance, and snaps the rail's own edges
    - all three moves together, since none is individually valid without
    the others. Returns (patched_text, applied_list, skipped_list)."""
    candidates = find_via_stub_candidates(script_text, top_cell_var)
    applied, skipped = [], []
    if not candidates:
        return script_text, applied, skipped

    base_create_re = re.compile(
        r'^cell_VIA_VIA45_1_2_58_58 = layout\.create_cell\("VIA_VIA45_1_2_58_58"\)\s*$',
        re.MULTILINE)
    m = base_create_re.search(script_text)
    if not m:
        return script_text, applied, [("VIA_VIA45_1_2_58_58", "base cell not found")]

    # Pass 1: confirm each candidate's instance placement is uniquely
    # findable before committing to anything - never partially apply.
    valid = []
    for c in candidates:
        inst_re = re.compile(
            rf"cell_{re.escape(top_cell_var)}\.insert\(pya\.CellInstArray\("
            rf"cell_VIA_VIA45_1_2_58_58\.cell_index\(\), pya\.Trans\((\d+), (True|False), "
            rf"pya\.Vector\({c['via_x']}, {c['via_y']}\)\)\)\)"
        )
        if len(list(inst_re.finditer(script_text))) != 1:
            skipped.append((f"VIA_VIA45_1_2_58_58@({c['via_x']},{c['via_y']})",
                             "instance placement not uniquely found - safe no-op"))
            continue
        valid.append((c, f"VIA_VIA45_1_2_58_58_C{len(valid)}"))

    if not valid:
        return script_text, applied, skipped

    # Pass 2: build and insert every custom cell declaration in one shot.
    var_id = 900000
    custom_decls = []
    for c, custom_name in valid:
        old_l, old_r = c["rail_old"]
        new_l, new_r = c["rail_new"]
        local_l, local_r = new_l - c["via_x"], new_r - c["via_x"]
        lines = [f'cell_{custom_name} = layout.create_cell("{custom_name}")']
        shapes_to_emit = [
            (50, local_l, _VIA45_LOCAL_M5[1], local_r, _VIA45_LOCAL_M5[3]),
            (40, local_l - _V4_M4_ENCLOSURE_RAW, _VIA45_LOCAL_M4[1],
                 local_r + _V4_M4_ENCLOSURE_RAW, _VIA45_LOCAL_M4[3]),
            (45, local_l, _VIA45_LOCAL_V4A[1], local_r, _VIA45_LOCAL_V4A[3]),
            (45, local_l, _VIA45_LOCAL_V4B[1], local_r, _VIA45_LOCAL_V4B[3]),
        ]
        for layer, x0, y0, x1, y1 in shapes_to_emit:
            var_id += 1
            var = f"p{var_id}"
            lines.append(f"{var} = pya.Polygon([pya.Point({x0}, {y0}), pya.Point({x0}, {y1}), "
                         f"pya.Point({x1}, {y1}), pya.Point({x1}, {y0})])")
            lines.append(f"cell_{custom_name}.shapes(layout.layer(pya.LayerInfo({layer}, 0))).insert({var})")
        custom_decls.append("\n".join(lines))
        applied.append(f"{custom_name}@({c['via_x']},{c['via_y']}): rail {old_l}->{new_l} / {old_r}->{new_r}")

    insert_at = m.end() + 1
    script_text = script_text[:insert_at] + "\n".join(custom_decls) + "\n" + script_text[insert_at:]

    # Pass 3: redirect each instance to its custom cell (re-search each time
    # since every prior edit shifts later offsets).
    for c, custom_name in valid:
        inst_re = re.compile(
            rf"cell_{re.escape(top_cell_var)}\.insert\(pya\.CellInstArray\("
            rf"cell_VIA_VIA45_1_2_58_58\.cell_index\(\), pya\.Trans\((\d+), (True|False), "
            rf"pya\.Vector\({c['via_x']}, {c['via_y']}\)\)\)\)"
        )
        im = inst_re.search(script_text)
        assert im, f"instance for {custom_name} vanished unexpectedly"
        new_line = im.group(0).replace(
            "cell_VIA_VIA45_1_2_58_58.cell_index()", f"cell_{custom_name}.cell_index()")
        script_text = script_text[:im.start()] + new_line + script_text[im.end():]

    # Pass 4: snap each rail's own edges.
    rect_re = re.compile(r"^(p\d+) = pya\.Polygon\(\[(.*?)\]\)\s*$", re.MULTILINE)
    for c, custom_name in valid:
        old_l, old_r = c["rail_old"]
        new_l, new_r = c["rail_new"]
        for m2 in rect_re.finditer(script_text):
            if m2.group(1) != c["rail_var"]:
                continue
            seg = m2.group(2)
            new_seg = re.sub(rf"pya\.Point\({old_l}, ", f"pya.Point({new_l}, ", seg)
            new_seg = re.sub(rf"pya\.Point\({old_r}, ", f"pya.Point({new_r}, ", new_seg)
            assert new_seg != seg, f"rail edge not found for {custom_name}"
            script_text = script_text[:m2.start(2)] + new_seg + script_text[m2.end(2):]
            break

    return script_text, applied, skipped


# ---------------------------------------------------------------------------
# Merge-aware, shape-aware V2.M3.AUX.2 fix.
#
# VIA_VIA23_1_3_36_36's 3 V2 vias must each match the TRUE merged M3 extent
# AT THEIR OWN LOCATION (V2.M3.AUX.2's rule text: "V2 must exactly be the
# same width as M3 ... perpendicular to M3's length"), but that extent is no
# longer a uniform 136 everywhere once the grid-alignment fix (above) has
# run: VIA_VIA23 itself never moves, but its M3 shape can share a merge
# group with a nearby VIA_VIA34 instance's M3 pad - and if THAT moved, the
# merge group's shape changes.
#
# A first version of this fix computed only the merge group's overall
# bounding-box HEIGHT and grew every via in the cell to that one value
# uniformly. Verified via real KLayout re-run that this is wrong whenever
# the merge group isn't a simple rectangle: direct pya.Region inspection
# showed the actual merged shape is a STEPPED polygon - full width for the
# original 136-height "core" (this instance's own contribution, always
# present), narrower for whatever additional height a shifted neighbor
# contributes. A via whose own X-range only partially overlaps the
# narrower step sticks out of the true shape if grown to the full bounding
# height - which is exactly what caused new V2.AUX.1 ("V2 must be inside M2
# and M3" - a literal containment check, read from asap7.lydrc) and
# V2.M3.EN.2 (enclosure) violations in that first version.
#
# The fix: for EACH of the 3 vias INDEPENDENTLY (not the cell as a whole),
# find the Y range that is continuously covered by the merge group at
# EVERY X point across that specific via's own X-span (the "vertical slice
# intersection", computed by _safe_y_range_for_x_range against the group's
# actual member rectangles, not just its bounding box) - the largest range
# the via can grow to while staying strictly inside the true shape. This
# range is not necessarily centered on the via's original position -
# growing asymmetrically (matching both real M3 edges exactly) is exactly
# what "match M3's width" requires, whichever direction the true extent
# lies in. The M2 pad is grown to the union of all 3 (possibly different)
# via ranges, to keep every via enclosed by it (V2.M2.EN.1).
#
# Because different vias - even within the same cell instance - can need
# different ranges, a single shared-cell-definition edit can't express this
# (editing the cell definition once affects every placement of it
# identically). Instances whose full computed result (pad + all 3 vias)
# matches the original default exactly keep referencing the existing shared
# cell definition, edited exactly as before. Any instance whose result
# differs gets its own new, uniquely named cell definition (inserted right
# after the original one) with the computed per-via ranges applied, and
# only THAT instance's own placement line is repointed to it.
# ---------------------------------------------------------------------------

_V2M3_CELL_NAME = "VIA_VIA23_1_3_36_36"
_V2M3_LAYER_SPEC = {20: 1, 30: 1, 25: 3}  # layer -> expected shape count
_V2M3_REF_LAYER = 30    # M3 - the layer we compute merged extents against
_V2M3_VIA_LAYER = 25    # V2 - grows to match the reference layer
_V2M3_PAD_LAYER = 20    # M2 - grows to enclose the vias
_V2M3_DEFAULT_RANGE = (-68, 68)  # matches the original fixed target (136 / 2 each way)

# V1.M2.AUX.2's analogous cascade, one level down: VIA_VIA23's M2 pad
# growing (to enclose its now-asymmetric V2 vias) can merge with this
# cell's own M1/M2 rail pair, inflating the rail's LOCAL merged M2 height
# beyond what its own (untouched) V1 taps can match - confirmed via direct
# pya inspection, not assumed (see NOTES.md's "V1.M2.AUX.2" section). Same
# mechanism, same fix, one layer down: M1=pad (encloses V1), M2=reference
# (what V1 must match), V1=vias (87 of them, tapping a shared M1/M2 rail).
# Unlike VIA_VIA23_1_3_36_36 (a literal PDK cell name, identical in every
# block), this cell's name encodes block-specific dimensions - confirmed by
# direct diffing: Block1/6 "VIA_via1_2_3132_18_1_87_36_36" (87 taps),
# Block2 "..._2160_18_1_60_36_36" (60 taps), Block3 "..._2322_..._64_..."
# (64), Block7 "..._6750_..._187_..." (187) - so the exact name (and via
# count) must be discovered per script, not hardcoded.
_V1M2_NAME_RE = re.compile(r'VIA_via1_2_\d+_18_1_\d+_36_36')
_V1M2_REF_LAYER = 20    # M2
_V1M2_VIA_LAYER = 21    # V1
_V1M2_PAD_LAYER = 19    # M1
_V1M2_DEFAULT_RANGE = (-36, 36)  # this cell's own unmodified V1/M1/M2 Y half-extent

_CELL_DEF_RE_TMPL = r'cell_{cell}\s*=\s*layout\.create_cell\("{cell}"\)'


def _find_cell_name_matching(script_text, name_pattern):
    """Finds a cell name (from a `create_cell("...")` call) matching
    `name_pattern`, for cells whose exact name varies per script/block.
    Returns the name, or None if no match is found."""
    for m in re.finditer(r'layout\.create_cell\("([^"]+)"\)', script_text):
        if name_pattern.fullmatch(m.group(1)):
            return m.group(1)
    return None


def _apply_dynamic_merge_aware_fix(script_text, shift_map, *, cell_name, layer_spec,
                                    ref_layer, pad_layer, via_layer, default_range,
                                    var_id_start):
    """General merge-aware, per-via, shape-aware fix: grows every via in
    `cell_name` (on `via_layer`) to match the TRUE merged extent of
    `ref_layer` at its own location (computed per-via, not per-cell, to
    stay correct when that merged region isn't a simple rectangle - see
    the module-level comment above `apply_dynamic_v2m3_fix`), and grows
    the enclosing `pad_layer` shape to the union of all resulting via
    ranges. Used for both V2.M3.AUX.2 (VIA_VIA23) and V1.M2.AUX.2
    (VIA_via1_2_3132...), which are the exact same mechanism one layer
    apart.

    `layer_spec` maps layer -> expected shape count, or None to accept any
    count >= 1 (used for cells like VIA_via1_2_3132... whose via count
    varies per block - see NOTES.md). Returns (patched_text, applied_list,
    skipped_list)."""
    shapes = _parse_all_shapes(script_text)
    instances = _parse_all_instances(script_text)
    effective_instances = [
        (t, s, r, m, x, shift_map.get((t, s, x, y), y))
        for (t, s, r, m, x, y) in instances
    ]

    applied = []
    skipped = []

    local_by_layer = shapes.get(cell_name, {})
    if not local_by_layer:
        return script_text, applied, [(cell_name, "cell not present in this case")]

    structure_ok = (set(local_by_layer) <= set(layer_spec) and
                     all((count is None and len(local_by_layer.get(layer, [])) >= 1) or
                         len(local_by_layer.get(layer, [])) == count
                         for layer, count in layer_spec.items()))
    if not structure_ok:
        found = {l: len(v) for l, v in local_by_layer.items()}
        return script_text, applied, [(cell_name, f"structure mismatch: found layers={found}")]

    local_ref_bbox = local_by_layer[ref_layer][0]
    local_via_bboxes = local_by_layer[via_layer]  # in file order
    local_pad_bbox = local_by_layer[pad_layer][0]
    n_vias = len(local_via_bboxes)

    # For each instance, compute the per-via safe Y range (in LOCAL
    # coordinates, relative to the instance's own placement) and the pad's
    # resulting range (union of all via ranges), using the ORIGINAL
    # (unshifted, since neither of these cells ever moves) instance
    # position for both its own placement and its merge-group lookup, but
    # the EFFECTIVE (post-grid-shift) geometry for the merge computation.
    by_result = defaultdict(list)  # (pad_range, via_ranges_tuple) -> [(topvar, x, y), ...]
    for (topvar, subvar, rot, mirror, x, y) in instances:
        if subvar != cell_name:
            continue
        abs_ref_bbox = _transform_bbox(local_ref_bbox, rot, mirror, x, y)
        all_ref_boxes = _flatten_layer(topvar, ref_layer, shapes, effective_instances)
        group_members = _merged_group_members_containing(all_ref_boxes, abs_ref_bbox)
        if group_members is None:
            skipped.append((f"{cell_name}@({x},{y})", "instance's own reference-layer shape not found in flattened layer - skipped"))
            continue

        via_ranges = []
        ok = True
        for via_bbox in local_via_bboxes:
            vx0, vy0, vx1, vy1 = via_bbox
            abs_x0, abs_x1 = x + vx0, x + vx1
            safe = _safe_y_range_for_x_range(group_members, abs_x0, abs_x1)
            if safe is None:
                ok = False
                break
            via_ranges.append((safe[0] - y, safe[1] - y))  # back to local coords
        if not ok:
            skipped.append((f"{cell_name}@({x},{y})", "could not compute a safe range for one of its vias - skipped"))
            continue

        pad_range = (min(r[0] for r in via_ranges), max(r[1] for r in via_ranges))
        by_result[(pad_range, tuple(via_ranges))].append((topvar, x, y))

    if not by_result:
        return script_text, applied, skipped

    # Exactly one group must keep using the ORIGINAL shared cell definition
    # (edited in place, same as the old fixed-target fix did) - never zero.
    # If every instance needs a non-default range, the shared definition
    # would otherwise end up completely unreferenced (every instance
    # repointed to a new custom cell), which makes KLayout see it as an
    # extra, parentless "top cell" and abort DRC outright ("the layout has
    # multiple top cells") - confirmed via real KLayout re-run, not assumed
    # (this is exactly what happened on Block7's larger via-tap cell,
    # where NO instance happened to need the plain default range). The
    # default range is preferred when present (keeps the diff minimal and
    # matches prior versions exactly); otherwise the largest group (most
    # instances) is chosen, to minimize how many new custom cells are
    # needed overall.
    default_key = (default_range, (default_range,) * n_vias)
    if default_key in by_result:
        base_key = default_key
    else:
        base_key = max(by_result, key=lambda k: len(by_result[k]))
    base_group = by_result.pop(base_key)
    base_pad_range, base_via_ranges = base_key

    patched, applied_base, skipped_base = _apply_cell_fix_specs(
        script_text, {cell_name: {
            pad_layer: [_set_y_range(*base_pad_range)],
            ref_layer: [None],
            via_layer: [_set_y_range(*r) for r in base_via_ranges],
        }})
    script_text = patched
    tag = "default" if base_key == default_key else "base (largest non-default group)"
    applied.extend(f"{a} ({tag}, {len(base_group)} instance(s))" for a in applied_base)
    skipped.extend(skipped_base)

    if by_result:
        edits = []  # (start, end, replacement)
        block_pattern = re.compile(_POLY_INSERT_RE_TMPL.format(cell=re.escape(cell_name)))
        block_matches = list(block_pattern.finditer(script_text))
        shapes_insert_at = max(m.end() for m in block_matches)

        # The new cell's OWN `create_cell(...)` line must be inserted
        # separately, right after the ORIGINAL cell's create_cell() line -
        # NOT alongside its shape definitions further down. Reason: the
        # official evaluator's connectivity checker identifies the script's
        # top cell by scanning for create_cell() calls and keeping the
        # LAST one found (see check_connectivity.py's parse_block_script) -
        # this only correctly picks BlockN's own top cell because, in every
        # available block, BlockN's create_cell() line happens to be the
        # last one in the file's initial "declarations" section (confirmed
        # across all 5 blocks). A new create_cell() call placed anywhere
        # AFTER that line - which is exactly where this cell's shape
        # definitions live - would make our new cell look like "the last
        # one", so the checker would flatten from OUR cell instead of the
        # real top cell, collapsing every connectivity path to nothing.
        # Confirmed via real check_connectivity.py re-run, not assumed - see
        # NOTES.md.
        cell_def_match = re.search(_CELL_DEF_RE_TMPL.format(cell=re.escape(cell_name)), script_text)
        if cell_def_match is None:
            skipped.append((cell_name, "could not find cell definition line to anchor new custom cell declarations"))
            return script_text, applied, skipped
        create_cell_insert_at = cell_def_match.end()

        new_cell_decls = []
        new_cells_text = []
        # New polygon variable names MUST match p\d+ (digits only after "p"):
        # the official evaluator's connectivity checker (check_connectivity.py)
        # parses polygon definitions with a HARDCODED regex requiring exactly
        # that pattern - anything else (e.g. p_MyCell_1) is silently invisible
        # to it, which breaks connectivity tracing for the ENTIRE script, not
        # just locally (confirmed: modified_paths_count dropped to 0 with a
        # non-matching name, back to matching the golden count once fixed -
        # see NOTES.md). Use a var-id space far past any realistic existing
        # p<N> count in these scripts to guarantee no collision - and a
        # DIFFERENT range per caller of this function, so the V2.M3 and
        # V1.M2 fixes (both potentially applied to the same script) never
        # generate the same variable name as each other either.
        _new_var_counter = [var_id_start]

        def _new_var():
            _new_var_counter[0] += 1
            return f"p{_new_var_counter[0]}"

        for cell_idx, ((pad_range, via_ranges), insts) in enumerate(sorted(by_result.items())):
            custom_name = f"{cell_name}_C{cell_idx}"
            new_cell_decls.append(f'cell_{custom_name} = layout.create_cell("{custom_name}")')
            lines = []

            def _emit(layer, x0, ny0, x1, ny1):
                var = _new_var()
                lines.append(
                    f"{var} = pya.Polygon([pya.Point({x0}, {ny0}), "
                    f"pya.Point({x0}, {ny1}), pya.Point({x1}, {ny1}), "
                    f"pya.Point({x1}, {ny0})])"
                )
                lines.append(
                    f"cell_{custom_name}.shapes(layout.layer(pya.LayerInfo({layer}, 0))).insert({var})"
                )

            px0, _, px1, _ = local_pad_bbox
            _emit(pad_layer, px0, pad_range[0], px1, pad_range[1])
            rx0, ry0, rx1, ry1 = local_ref_bbox
            _emit(ref_layer, rx0, ry0, rx1, ry1)  # unchanged
            for via_bbox, via_range in zip(local_via_bboxes, via_ranges):
                vx0, _, vx1, _ = via_bbox
                _emit(via_layer, vx0, via_range[0], vx1, via_range[1])

            new_cells_text.append("\n".join(lines))
            applied.append(f"{cell_name}:custom_cell={custom_name} "
                            f"pad={pad_range} vias={via_ranges} ({len(insts)} instance(s))")

            for (topvar, x, y) in insts:
                old_line_re = re.compile(_PLACEMENT_RE_TMPL.format(
                    topcell=re.escape(topvar), cell=re.escape(cell_name), x=x, y=y))
                inst_matches = list(old_line_re.finditer(script_text))
                if len(inst_matches) != 1:
                    skipped.append((f"{cell_name}@({x},{y})",
                                     "could not uniquely locate this instance's placement line - skipped"))
                    continue
                im = inst_matches[0]
                new_line = im.group(0).replace(f"cell_{cell_name}.cell_index()",
                                                f"cell_{custom_name}.cell_index()")
                edits.append((im.start(), im.end(), new_line))

        for start, end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
            script_text = script_text[:start] + replacement + script_text[end:]

        # Insert the shape definitions first (further down in the text) so
        # the earlier create_cell() insertion point's offset isn't shifted
        # by this one.
        script_text = script_text[:shapes_insert_at] + "\n" + "\n".join(new_cells_text) + script_text[shapes_insert_at:]
        script_text = script_text[:create_cell_insert_at] + "\n" + "\n".join(new_cell_decls) + script_text[create_cell_insert_at:]

    return script_text, applied, skipped


def apply_dynamic_v2m3_fix(script_text, shift_map):
    """Merge-aware, per-via, shape-aware version of the old fixed-target
    V2.M3.AUX.2 fix. See the module-level comment above. Returns
    (patched_text, applied_list, skipped_list)."""
    return _apply_dynamic_merge_aware_fix(
        script_text, shift_map,
        cell_name=_V2M3_CELL_NAME, layer_spec=_V2M3_LAYER_SPEC,
        ref_layer=_V2M3_REF_LAYER, pad_layer=_V2M3_PAD_LAYER, via_layer=_V2M3_VIA_LAYER,
        default_range=_V2M3_DEFAULT_RANGE, var_id_start=900000)


# ---------------------------------------------------------------------------
# V1.M2.AUX.2 local-patch fix.
#
# The whole-pad growth apply_dynamic_v2m3_fix() uses (safe for VIA_VIA23,
# whose M2 pad only spans its own 3 vias) is NOT safe for this rail cell:
# its M1 pad spans the ENTIRE row (thousands of raw units), so growing it as
# one rectangle to satisfy even a single via's M2-driven target reaches
# across the whole row and can silently merge into UNRELATED standard-cell
# M1 shapes anywhere along that row. This was tried and confirmed broken via
# real KLayout connectivity re-run (460 pin-endpoint + 381 routing-endpoint
# mismatches, modified_paths_count 8260 vs golden 1350) - root-caused via
# direct pya.Region probing to the pad growing into another standard cell's
# M1 pin it had no relationship to. See NOTES.md's "V1.M2.AUX.2 cascade"
# section for the full account.
#
# The fix: grow ONLY the specific via that needs it, and add a small LOCAL
# M1 patch (that via's own X span, +/- a fixed enclosure margin) instead of
# resizing the whole row-wide pad - every edit stays local to where it's
# actually needed, and the untouched pad/other vias are left completely
# alone.
#
# A second, independent safety check beyond the existing M2-merge-topology
# one is required: growing M1 can come close to OTHER, unrelated M1 shapes
# nearby, some of them non-rectangular (confirmed directly: unrelated
# standard cells like BUFx2_ASAP7_75t_R define M1 routing as 8-12 point
# jogged polygons, not simple rectangles - _ANY_POLY_INSERT_RE was
# generalized to see these; a 4-point-only parser silently missed them,
# which is exactly what made an earlier version of this M1-safety check
# find "no obstacle" where a real one existed). Each via's final growth is
# the INTERSECTION of what the M2 side allows AND what the M1 side allows
# (nearest foreign M1 obstacle, kept at least _M1_SPACING_CUSHION_RAW away) -
# never just one or the other, and never less than the original default
# range either direction.
# ---------------------------------------------------------------------------

_M1_ENCLOSURE_MARGIN_RAW = 24   # 6nm - comfortably above V1.M1.EN.1's 5nm/2nm requirement
_M1_SPACING_CUSHION_RAW = 144   # 36nm - comfortably above every M1.S.* rule's max threshold (31nm)


def _m1_safe_range_for_patch(foreign_m1_boxes, patch_x0, patch_x1, default_y0, default_y1, cushion):
    """Given FOREIGN (unrelated) M1 boxes, finds how far a NEW M1 patch
    spanning [patch_x0, patch_x1] can grow in Y from [default_y0, default_y1]
    (its floor - it can only ever grow, never shrink) without coming within
    `cushion` of any foreign M1 shape.

    A first version only considered foreign shapes whose X range directly
    overlapped the patch's - real KLayout DRC re-run showed this misses
    CORNER-to-corner proximity (M1.S.3/S.4/S.6, all corner/tip spacing
    rules): a foreign shape sitting just outside the patch's X range but
    close enough diagonally can still violate those without ever overlapping
    in X. Fixed by treating any foreign shape within `cushion` of the patch's
    X range (not just directly overlapping it) as relevant, and requiring
    the same Y cushion from it - this guarantees at least `cushion` of true
    Euclidean separation in the worst case (a foreign shape right at that
    X-cushion boundary), not just Y-only clearance for X-overlapping shapes.
    Returns (safe_y0, safe_y1)."""
    above = [b for b in foreign_m1_boxes
             if b[0] < patch_x1 + cushion and b[2] > patch_x0 - cushion and b[1] >= default_y1]
    below = [b for b in foreign_m1_boxes
             if b[0] < patch_x1 + cushion and b[2] > patch_x0 - cushion and b[3] <= default_y0]
    upper = min((b[1] for b in above), default=None)
    lower = max((b[3] for b in below), default=None)
    safe_y1 = max(default_y1, upper - cushion) if upper is not None else default_y1 + 10**9
    safe_y0 = min(default_y0, lower + cushion) if lower is not None else default_y0 - 10**9
    return (safe_y0, safe_y1)


_V0_LAYER = 18   # V0 (active/poly -> M1 contact) - checked as a third safety
                 # constraint, NOT modified by this fix


def _v0_safe_range_for_via(foreign_v0_boxes, x0, x1, default_y0, default_y1):
    """A V0 contact that sits FLUSH against the default M1 edge (by design -
    confirmed via real KLayout GUI inspection, multiple examples, all the
    same mechanism: a step/corner in M1's edge lining up with a V0's own
    edge) depends on that exact alignment for V0.M1.AUX.3 ("V0 must exactly
    match M1's width perpendicular to M1's length" - the same rule family as
    V1.M2.AUX.2/V2.M3.AUX.2, one layer further down). Moving M1's edge away
    from such a V0 - even though it's a real, otherwise-safe direction to
    grow M1 in - breaks that flush relationship and creates a NEW
    V0.M1.AUX.3 violation. A first version of this fix didn't check this at
    all: real KLayout DRC re-run showed an almost exact 1-for-1 trade (every
    via whose V1.M2.AUX.2 got fixed cost one new V0.M1.AUX.3 violation
    nearby). Caps growth in whichever direction has a flush V0 at this via's
    location; the other direction (if no flush V0 there) is unrestricted."""
    overlapping = [b for b in foreign_v0_boxes if b[0] < x1 and b[2] > x0]
    blocked_down = any(b[1] == default_y0 for b in overlapping)
    blocked_up = any(b[3] == default_y1 for b in overlapping)
    safe_y0 = default_y0 if blocked_down else default_y0 - 10**9
    safe_y1 = default_y1 if blocked_up else default_y1 + 10**9
    return (safe_y0, safe_y1)


def _insert_extra_shapes(script_text, cell_name, extra_shapes, var_id_start):
    """Appends new pXXX=Polygon(...)/.insert(pXXX) statement pairs directly
    into an EXISTING cell definition, right after its last existing shape -
    used to add new M1 enclosure patches to the shared/base cell in place,
    without creating a whole new cell. `extra_shapes` is a list of
    (layer, x0, y0, x1, y1). Numeric-only p{N} variable names, per
    check_connectivity.py's hardcoded p\\d+ parsing requirement (see the
    module comment above apply_dynamic_v2m3_fix). Returns
    (patched_text, applied_list)."""
    pattern = re.compile(_POLY_INSERT_RE_TMPL.format(cell=re.escape(cell_name)))
    matches = list(pattern.finditer(script_text))
    insert_at = max(m.end() for m in matches)
    lines = []
    applied = []
    var_id = var_id_start
    for layer, x0, y0, x1, y1 in extra_shapes:
        var_id += 1
        var = f"p{var_id}"
        lines.append(
            f"{var} = pya.Polygon([pya.Point({x0}, {y0}), pya.Point({x0}, {y1}), "
            f"pya.Point({x1}, {y1}), pya.Point({x1}, {y0})])"
        )
        lines.append(f"cell_{cell_name}.shapes(layout.layer(pya.LayerInfo({layer}, 0))).insert({var})")
        applied.append(f"{cell_name}:layer{layer}:{var}(new M1 enclosure patch)")
    script_text = script_text[:insert_at] + "\n" + "\n".join(lines) + script_text[insert_at:]
    return script_text, applied


def _apply_v1m2_local_patch_fix(script_text, shift_map, *, cell_name, var_id_start):
    """Merge-aware, per-via, LOCAL-PATCH fix for V1.M2.AUX.2 - see the
    module comment above. Returns (patched_text, applied_list, skipped_list)."""
    shapes = _parse_all_shapes(script_text)
    instances = _parse_all_instances(script_text)
    effective_instances = [
        (t, s, r, m, x, shift_map.get((t, s, x, y), y))
        for (t, s, r, m, x, y) in instances
    ]

    applied = []
    skipped = []

    local_by_layer = shapes.get(cell_name, {})
    if not local_by_layer:
        return script_text, applied, [(cell_name, "cell not present in this case")]

    layer_spec = {_V1M2_PAD_LAYER: 1, _V1M2_REF_LAYER: 1, _V1M2_VIA_LAYER: None}
    structure_ok = (set(local_by_layer) <= set(layer_spec) and
                     all((count is None and len(local_by_layer.get(layer, [])) >= 1) or
                         len(local_by_layer.get(layer, [])) == count
                         for layer, count in layer_spec.items()))
    if not structure_ok:
        found = {l: len(v) for l, v in local_by_layer.items()}
        return script_text, applied, [(cell_name, f"structure mismatch: found layers={found}")]

    local_ref_bbox = local_by_layer[_V1M2_REF_LAYER][0]
    local_via_bboxes = local_by_layer[_V1M2_VIA_LAYER]
    local_pad_bbox = local_by_layer[_V1M2_PAD_LAYER][0]
    n_vias = len(local_via_bboxes)
    default_range = _V1M2_DEFAULT_RANGE

    by_result = defaultdict(list)  # via_ranges_tuple -> [(topvar, x, y), ...]
    for (topvar, subvar, rot, mirror, x, y) in instances:
        if subvar != cell_name:
            continue
        abs_ref_bbox = _transform_bbox(local_ref_bbox, rot, mirror, x, y)
        all_ref_boxes = _flatten_layer(topvar, _V1M2_REF_LAYER, shapes, effective_instances)
        group_members = _merged_group_members_containing(all_ref_boxes, abs_ref_bbox)
        if group_members is None:
            skipped.append((f"{cell_name}@({x},{y})", "instance's own reference-layer shape not found in flattened layer - skipped"))
            continue

        eff_y = shift_map.get((topvar, cell_name, x, y), y)
        foreign_instances = [
            inst for inst in effective_instances
            if not (inst[0] == topvar and inst[1] == cell_name and inst[4] == x and inst[5] == eff_y)
        ]
        foreign_m1_boxes = _flatten_layer(topvar, _V1M2_PAD_LAYER, shapes, foreign_instances)
        foreign_v0_boxes = _flatten_layer(topvar, _V0_LAYER, shapes, effective_instances)
        default_abs_y0, default_abs_y1 = y + default_range[0], y + default_range[1]

        via_ranges = []
        ok = True
        for via_bbox in local_via_bboxes:
            vx0, vy0, vx1, vy1 = via_bbox
            abs_x0, abs_x1 = x + vx0, x + vx1
            m2_safe = _safe_y_range_for_x_range(group_members, abs_x0, abs_x1)
            if m2_safe is None:
                ok = False
                break
            m2_local = (m2_safe[0] - y, m2_safe[1] - y)

            patch_x0, patch_x1 = abs_x0 - _M1_ENCLOSURE_MARGIN_RAW, abs_x1 + _M1_ENCLOSURE_MARGIN_RAW
            m1_safe_abs = _m1_safe_range_for_patch(
                foreign_m1_boxes, patch_x0, patch_x1,
                default_abs_y0, default_abs_y1, _M1_SPACING_CUSHION_RAW)
            m1_local = (m1_safe_abs[0] - y, m1_safe_abs[1] - y)

            v0_safe_abs = _v0_safe_range_for_via(
                foreign_v0_boxes, patch_x0, patch_x1, default_abs_y0, default_abs_y1)
            v0_local = (v0_safe_abs[0] - y, v0_safe_abs[1] - y)

            # intersect everything M2, M1, and V0 each allow, then clamp so
            # this via's range never shrinks past its original default
            final_y0 = min(max(m2_local[0], m1_local[0], v0_local[0]), default_range[0])
            final_y1 = max(min(m2_local[1], m1_local[1], v0_local[1]), default_range[1])
            via_ranges.append((final_y0, final_y1))
        if not ok:
            skipped.append((f"{cell_name}@({x},{y})", "could not compute a safe range for one of its vias - skipped"))
            continue

        by_result[tuple(via_ranges)].append((topvar, x, y))

    if not by_result:
        return script_text, applied, skipped

    default_key = (default_range,) * n_vias

    def _patches_for(via_ranges):
        """Builds (via_edits, extra_patch_specs) for one instance's computed
        per-via ranges. Adjacent vias that both need growth get merged into
        ONE shared patch (rather than one patch each) when the gap between
        their individual patches would be less than the spacing cushion -
        confirmed necessary via real KLayout DRC re-run: two separate,
        closely-spaced patches can trigger M1.S.4 (tip-to-tip spacing)
        AGAINST EACH OTHER, a self-inflicted violation the per-via M1-safety
        check (which only looks for FOREIGN obstacles) doesn't catch, since
        neither patch is foreign to the other's own instance. Merging uses
        the INTERSECTION of the group's individual ranges (never wider than
        any member's own computed-safe range, so never less safe), clamped
        to never shrink past default - see NOTES.md's "V1.M2.AUX.2 cascade"
        section for the real marker coordinates that exposed this."""
        growing = []
        for i, r in enumerate(via_ranges):
            if r == default_range:
                continue
            vx0, _, vx1, _ = local_via_bboxes[i]
            growing.append((vx0, vx1, i, r))
        growing.sort(key=lambda t: t[0])

        clusters = []  # [x0, x1, [(i, r), ...]]
        for vx0, vx1, i, r in growing:
            if clusters and vx0 - clusters[-1][1] < _M1_SPACING_CUSHION_RAW:
                clusters[-1][1] = max(clusters[-1][1], vx1)
                clusters[-1][2].append((i, r))
            else:
                clusters.append([vx0, vx1, [(i, r)]])

        via_edits = {}
        extra = []
        for cx0, cx1, members in clusters:
            merged_y0 = min(max(r[0] for _, r in members), default_range[0])
            merged_y1 = max(min(r[1] for _, r in members), default_range[1])
            for i, _ in members:
                via_edits[i] = (merged_y0, merged_y1)
            if (merged_y0, merged_y1) != default_range:
                extra.append((_V1M2_PAD_LAYER, cx0 - _M1_ENCLOSURE_MARGIN_RAW, merged_y0,
                              cx1 + _M1_ENCLOSURE_MARGIN_RAW, merged_y1))
        return via_edits, extra

    # Exactly one group keeps the shared/base cell definition (preferring the
    # literal default if present, otherwise the largest group) - same
    # orphaned-base-cell reasoning as apply_dynamic_v2m3_fix (see NOTES.md).
    if default_key in by_result:
        base_key = default_key
    else:
        base_key = max(by_result, key=lambda k: len(by_result[k]))
    base_group = by_result.pop(base_key)

    base_edits, base_extra = _patches_for(base_key)
    if base_edits:
        via_transforms = [_set_y_range(*base_edits[i]) if i in base_edits else None
                          for i in range(n_vias)]
        patched, applied_base, skipped_base = _apply_cell_fix_specs(
            script_text, {cell_name: {
                _V1M2_PAD_LAYER: [None],
                _V1M2_REF_LAYER: [None],
                _V1M2_VIA_LAYER: via_transforms,
            }})
        script_text = patched
        skipped.extend(skipped_base)
        if base_extra:
            script_text, applied_patch = _insert_extra_shapes(script_text, cell_name, base_extra, var_id_start)
            applied_base = applied_base + applied_patch
        tag = "default" if base_key == default_key else "base (largest non-default group)"
        applied.extend(f"{a} ({tag}, {len(base_group)} instance(s))" for a in applied_base)
    else:
        applied.append(f"{cell_name}: base group unchanged (default, {len(base_group)} instance(s))")

    if by_result:
        placement_edits = []
        block_pattern = re.compile(_POLY_INSERT_RE_TMPL.format(cell=re.escape(cell_name)))
        block_matches = list(block_pattern.finditer(script_text))
        shapes_insert_at = max(m.end() for m in block_matches)
        cell_def_match = re.search(_CELL_DEF_RE_TMPL.format(cell=re.escape(cell_name)), script_text)
        if cell_def_match is None:
            skipped.append((cell_name, "could not find cell definition line to anchor new custom cell declarations"))
            return script_text, applied, skipped
        create_cell_insert_at = cell_def_match.end()

        new_cell_decls = []
        new_cells_text = []
        _new_var_counter = [var_id_start + 50000]

        def _new_var():
            _new_var_counter[0] += 1
            return f"p{_new_var_counter[0]}"

        for cell_idx, (via_ranges, insts) in enumerate(sorted(by_result.items())):
            custom_name = f"{cell_name}_C{cell_idx}"
            new_cell_decls.append(f'cell_{custom_name} = layout.create_cell("{custom_name}")')
            lines = []

            def _emit(layer, x0, ny0, x1, ny1):
                var = _new_var()
                lines.append(
                    f"{var} = pya.Polygon([pya.Point({x0}, {ny0}), "
                    f"pya.Point({x0}, {ny1}), pya.Point({x1}, {ny1}), "
                    f"pya.Point({x1}, {ny0})])"
                )
                lines.append(
                    f"cell_{custom_name}.shapes(layout.layer(pya.LayerInfo({layer}, 0))).insert({var})"
                )

            px0, py0, px1, py1 = local_pad_bbox
            _emit(_V1M2_PAD_LAYER, px0, py0, px1, py1)  # unchanged, full-row pad
            rx0, ry0, rx1, ry1 = local_ref_bbox
            _emit(_V1M2_REF_LAYER, rx0, ry0, rx1, ry1)  # unchanged
            via_edits, extra = _patches_for(via_ranges)
            for i, via_bbox in enumerate(local_via_bboxes):
                vx0, vy0, vx1, vy1 = via_bbox
                if i in via_edits:
                    _emit(_V1M2_VIA_LAYER, vx0, via_edits[i][0], vx1, via_edits[i][1])
                else:
                    _emit(_V1M2_VIA_LAYER, vx0, vy0, vx1, vy1)  # unchanged
            for layer, x0, y0, x1, y1 in extra:
                _emit(layer, x0, y0, x1, y1)

            new_cells_text.append("\n".join(lines))
            applied.append(f"{cell_name}:custom_cell={custom_name} "
                            f"{len(extra)} local M1 patch(es) ({len(insts)} instance(s))")

            for (topvar, x, y) in insts:
                old_line_re = re.compile(_PLACEMENT_RE_TMPL.format(
                    topcell=re.escape(topvar), cell=re.escape(cell_name), x=x, y=y))
                inst_matches = list(old_line_re.finditer(script_text))
                if len(inst_matches) != 1:
                    skipped.append((f"{cell_name}@({x},{y})",
                                     "could not uniquely locate this instance's placement line - skipped"))
                    continue
                im = inst_matches[0]
                new_line = im.group(0).replace(f"cell_{cell_name}.cell_index()",
                                                f"cell_{custom_name}.cell_index()")
                placement_edits.append((im.start(), im.end(), new_line))

        for start, end, replacement in sorted(placement_edits, key=lambda e: e[0], reverse=True):
            script_text = script_text[:start] + replacement + script_text[end:]

        script_text = script_text[:shapes_insert_at] + "\n" + "\n".join(new_cells_text) + script_text[shapes_insert_at:]
        script_text = script_text[:create_cell_insert_at] + "\n" + "\n".join(new_cell_decls) + script_text[create_cell_insert_at:]

    return script_text, applied, skipped


def apply_dynamic_v1m2_fix(script_text, shift_map):
    """Local-patch, per-via, shape-aware fix for V1.M2.AUX.2. See the
    module-level comment above _apply_v1m2_local_patch_fix. Returns
    (patched_text, applied_list, skipped_list)."""
    cell_name = _find_cell_name_matching(script_text, _V1M2_NAME_RE)
    if cell_name is None:
        return script_text, [], [("VIA_via1_2_*", "no matching cell found in this case")]
    return _apply_v1m2_local_patch_fix(
        script_text, shift_map, cell_name=cell_name, var_id_start=950000)


# ---------------------------------------------------------------------------
# M4.S.5 ("parallel run length" spacing rule) - hybrid deterministic + LLM.
#
# Unlike every fix above, this one is NOT fully deterministic: candidate
# generation and safety-checking are pure stdlib arithmetic (no pya, no
# guessing), but which candidate to ship is a genuine, ship-affecting model
# call - not the decorative "analysis" call in main() below, whose output is
# explicitly logged-only. See NOTES.md for why: the model is asked to choose
# among (or reject) a small set of already-safety-checked options, never to
# invent coordinates itself, so a bad model response degrades to a safe
# no-op rather than a bad edit - the same "model proposes, deterministic
# gates dispose" discipline as the rest of this file, just with the model's
# choice actually reaching the shipped output instead of being discarded.
#
# Rule (from asap7.lydrc): two M4 wires on vertically-adjacent routing
# tracks (24nm Y-gap, satisfying M4.S.1's own minimum) must overlap
# horizontally ("parallel run length") by at least 44nm; the DRC report's own
# violation bbox IS the too-narrow overlap sliver, so vx0/vx1 give exactly
# the two facing edges' positions with no need to re-derive them.
#
# Candidates only exist where the limiting edge belongs to a top-level
# cell_Block1 rectangle - confirmed by direct grep that the bare
# VIA_VIA34/VIA_VIA45 macros (as opposed to the specialized, low-count
# suffixed variants already grown elsewhere in this file) are instantiated
# 50 and 21 times respectively in Block1 alone, the same high-reuse blast-
# radius class that made blind VIA_VIA12 edits catastrophic (see NOTES.md).
# Violations whose limiting edge sits inside one of those shared macros are
# left untouched and logged, not guessed at.
# ---------------------------------------------------------------------------

M4S5_REQUIRED_OVERLAP_RAW = 176  # 44nm in raw KLayout units (4000/um)
M4S5_MARGIN_RAW = 16             # +4nm safety margin beyond the bare floor


def _rects_overlap(a, b):
    al, ab, ar, at = a
    bl, bb, br, bt = b
    return not (ar <= bl or br <= al or at <= bb or bt <= ab)


def find_m4s5_candidates(script_text, violation_bbox):
    """Given one M4.S.5 violation's bbox (from the case's own given DRC
    report), find every top-level cell_Block1 M4 rectangle whose edge
    exactly forms one side of the too-narrow overlap, and compute the
    minimal safe extension to clear the 44nm floor plus margin. Each
    candidate is independently checked for collision against every OTHER
    top-level M4 rectangle. Returns [] if neither limiting edge is a
    top-level shape (i.e. it belongs to a shared via macro - see module
    comment above)."""
    vx0, vy0, vx1, vy1 = violation_bbox
    m4_rects = []
    for m in _ANY_POLY_INSERT_RE.finditer(script_text):
        if m.group("cellvar") != "Block1" or int(m.group("layer")) != 40:
            continue
        coords = _POINT_RE.findall(m.group("points"))
        pts = tuple(int(v) for pair in coords for v in pair)
        if len(pts) != 8:
            continue  # only handle simple 4-point rectangles for this fix
        if len(set(pts[0::2])) != 2 or len(set(pts[1::2])) != 2:
            continue  # not axis-aligned
        m4_rects.append({
            "var": m.group("var"), "bbox": _poly_bbox(pts),
            "points_span": (m.start("points"), m.end("points")), "points": pts,
        })

    candidates = []
    for p in m4_rects:
        pl, pb, pr, pt = p["bbox"]
        if pb == vy1 and pr == vx1:
            target_right = vx0 + M4S5_REQUIRED_OVERLAP_RAW + M4S5_MARGIN_RAW
            ext = target_right - pr
            if ext <= 0:
                continue
            new_bbox = (pl, pb, target_right, pt)
            obstacle = any(_rects_overlap(new_bbox, q["bbox"])
                           for q in m4_rects if q["var"] != p["var"])
            candidates.append({
                "action": "extend_right_edge", "var": p["var"],
                "orig_bbox": p["bbox"], "new_bbox": new_bbox,
                "extension_nm": ext / 4.0, "obstacle_free": not obstacle,
                "points_span": p["points_span"], "points": p["points"],
                "edge_to_move": "max_x",
            })
    for p in m4_rects:
        pl, pb, pr, pt = p["bbox"]
        if pt == vy0 and pl == vx0:
            target_left = vx1 - M4S5_REQUIRED_OVERLAP_RAW - M4S5_MARGIN_RAW
            ext = pl - target_left
            if ext <= 0:
                continue
            new_bbox = (target_left, pb, pr, pt)
            obstacle = any(_rects_overlap(new_bbox, q["bbox"])
                           for q in m4_rects if q["var"] != p["var"])
            candidates.append({
                "action": "extend_left_edge", "var": p["var"],
                "orig_bbox": p["bbox"], "new_bbox": new_bbox,
                "extension_nm": ext / 4.0, "obstacle_free": not obstacle,
                "points_span": p["points_span"], "points": p["points"],
                "edge_to_move": "min_x",
            })
    return candidates


def _apply_m4s5_candidate(script_text, candidate):
    """Rewrites exactly the one X coordinate that needs to change in the
    candidate's polygon point list - same minimal-string-splice discipline
    as every other edit in this file."""
    edge = candidate["edge_to_move"]
    old_val = candidate["orig_bbox"][2] if edge == "max_x" else candidate["orig_bbox"][0]
    new_val = candidate["new_bbox"][2] if edge == "max_x" else candidate["new_bbox"][0]
    start, end = candidate["points_span"]
    old_text = script_text[start:end]
    new_text = re.sub(rf"pya\.Point\({old_val}, ", f"pya.Point({new_val}, ", old_text)
    assert new_text != old_text, "expected coordinate not found in points text"
    return script_text[:start] + new_text + script_text[end:]


def _parse_m4s5_decision(model_text, safe_candidates):
    """Deterministically re-validates the model's JSON response against the
    real candidate list - never trusts free-form output. Returns the chosen
    candidate dict, or None if the response should be treated as a reject
    (unparseable, malformed, or naming a candidate that isn't actually in
    the safe/offered list)."""
    if not model_text:
        return None
    match = re.search(r"\{.*\}", model_text, re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("decision") != "apply":
        return None
    chosen_var = payload.get("candidate_var")
    for c in safe_candidates:
        if c["var"] == chosen_var:
            return c
    return None


def apply_llm_m4s5_fix(script_text, drc_report, endpoint, model_name):
    """Hybrid LLM+deterministic fix for M4.S.5. For each violation in the
    given DRC report, find_m4s5_candidates() deterministically generates
    zero or more independently safety-checked fix candidates; if at least
    one exists, the model is asked to pick which one to apply (or reject
    all) - a genuine, ship-affecting model call. See the module comment
    above for why a bad response can only ever degrade to a safe no-op."""
    rule = (drc_report or {}).get("rules", {}).get("M4.S.5")
    if not rule or not endpoint:
        return script_text, [], []
    rule_desc = rule.get("description", "M4.S.5")
    applied, skipped = [], []

    for i, v in enumerate(rule.get("violations", [])):
        bbox = tuple(v.get("bbox", ()))
        if len(bbox) != 4:
            continue
        candidates = find_m4s5_candidates(script_text, bbox)
        safe_candidates = [c for c in candidates if c["obstacle_free"]]
        if not safe_candidates:
            skipped.append((f"M4.S.5[{i}]",
                             "no obstacle-free top-level candidate (limiting edge "
                             "likely belongs to a high-reuse via macro) - safe no-op"))
            continue

        prompt_candidates = [
            {k: cv for k, cv in c.items() if k not in ("points", "points_span")}
            for c in safe_candidates
        ]
        prompt = (
            "You are a physical design engineer fixing a DRC violation in an "
            "ASAP7 standard-cell block layout.\n\n"
            f"Rule violated: {rule_desc}\n\n"
            f"Violation location (raw KLayout units, 4000/um): bbox {bbox}. "
            "This bbox is the too-narrow region where two M4 wires on "
            "vertically-adjacent routing tracks (24nm apart) fail to overlap "
            "horizontally by the required 44nm (\"parallel run length\").\n\n"
            "Candidate fix(es) found by static analysis (each edits exactly "
            "one top-level rectangle, extending it toward the other wire "
            "until the required overlap plus a small safety margin is met; "
            "each has already been checked for collision against every other "
            "top-level M4 shape in the design):\n\n"
            f"{json.dumps(prompt_candidates, indent=2)}\n\n"
            "Task: decide whether to APPLY exactly one of these candidates "
            "as-is, or REJECT all of them if you see a reason none is safe. "
            "Do not propose your own coordinates - only select from the "
            "given candidate(s) or reject.\n\n"
            "Respond with ONLY a JSON object, no other text:\n"
            '{"decision": "apply" or "reject", "candidate_var": "<var name or null>", '
            '"reason": "<one sentence>"}'
        )

        text, _usage = call_model(endpoint, prompt, model_name, max_tokens=512)
        decision = _parse_m4s5_decision(text, safe_candidates)
        if decision is None:
            skipped.append((f"M4.S.5[{i}]",
                             "model response missing/unparseable/did not select "
                             "a valid safe candidate - safe no-op"))
            continue

        script_text = _apply_m4s5_candidate(script_text, decision)
        applied.append(f"M4.S.5[{i}]:{decision['var']}(+{decision['extension_nm']}nm)")

    return script_text, applied, skipped


def parse_error_payload(error_text):
    try:
        payload = json.loads(error_text)
    except json.JSONDecodeError:
        return {"error": error_text}
    return payload if isinstance(payload, dict) else {"error": error_text}


def should_retry(status_code, payload):
    if payload.get("retryable") is True:
        return True
    return status_code in RETRYABLE_HTTP_STATUS


def call_model(endpoint, prompt, model, max_tokens=2048, max_retries=5):
    """POST to the benchmark model endpoint per AGENT_GUIDE.md's contract."""
    url = endpoint.rstrip("/") + "/generate"
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "max_output_tokens": max_tokens,
    }).encode("utf-8")

    delay = 2
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return payload.get("text") or "", payload.get("usage") or {}

        except urllib.error.HTTPError as exc:
            err_payload = parse_error_payload(exc.read().decode("utf-8", errors="replace"))
            if not should_retry(exc.code, err_payload) or attempt == max_retries:
                print(f"[WARN] Model call failed ({exc.code}): {err_payload}. "
                      f"Continuing without model analysis.", file=sys.stderr)
                return "", {}
            print(f"[WARN] Retryable error {exc.code}. Retry in {delay}s ({attempt}/{max_retries})",
                  file=sys.stderr)
            time.sleep(delay)
            delay = min(delay * 2, 60)

        except urllib.error.URLError as exc:
            if attempt == max_retries:
                print(f"[WARN] Model endpoint unreachable: {exc}. "
                      f"Continuing without model analysis.", file=sys.stderr)
                return "", {}
            print(f"[WARN] Connection error. Retry in {delay}s ({attempt}/{max_retries}) {exc}",
                  file=sys.stderr)
            time.sleep(delay)
            delay = min(delay * 2, 60)

    return "", {}


def main():
    parser = argparse.ArgumentParser(description="T19 ASU block-repair agent")
    parser.add_argument("info_json", help="Path to the benchmark case metadata JSON")
    parser.add_argument("--model", default=None, help="Overrides info.json's model, if given")
    args = parser.parse_args()

    with open(args.info_json, encoding="utf-8") as f:
        info = json.load(f)

    model_name = args.model or info.get("model", "gemini-2.5-flash")
    endpoint = info.get("model_endpoint", "")
    case_name = info.get("case_name", "?")

    print(f"[INFO] T19 ASU agent | case={case_name} | model={model_name}", file=sys.stderr)

    layout_path = Path(info["path_to_layout_script"])
    output_path = Path(info["output_path"])
    temp_dir = Path(info.get("temp_dir", "."))
    temp_dir.mkdir(parents=True, exist_ok=True)

    original_script = layout_path.read_text(encoding="utf-8")

    # Decide the M4 grid-alignment shifts first (data only, no text edit
    # yet) - the merge-aware V2.M3.AUX.2 fix below needs to know the
    # resulting geometry to compute its own targets correctly.
    instances = _parse_all_instances(original_script)
    shift_map, grid_applied, grid_skipped = compute_grid_shifts(instances)

    # Merge-aware V2.M3.AUX.2 fix - per-instance target, computed against
    # the post-grid-shift geometry (see apply_dynamic_v2m3_fix's docstring).
    patched_script, v2m3_applied, v2m3_skipped = apply_dynamic_v2m3_fix(original_script, shift_map)

    # V1.M2.AUX.2 fix - the same cascade mechanism as V2.M3.AUX.2 above, one
    # metal layer down. A first (whole-pad-growth) version broke connectivity
    # outright; the current local-patch version grows only the specific via
    # that needs it (plus a small local M1 patch), checked against THREE
    # independent safety constraints (M2 merge topology, foreign M1 shapes
    # including non-rectangular ones, and V0 contacts flush against the
    # default M1 edge) before ever growing anything - see NOTES.md's
    # "V1.M2.AUX.2 cascade" section for the full derivation, including two
    # earlier versions that were tried and found wanting (real KLayout
    # connectivity/DRC re-run each time, not assumed).
    patched_script, v1m2_applied, v1m2_skipped = apply_dynamic_v1m2_fix(patched_script, shift_map)

    # Fixed-target via-growth fixes (V4.M5.AUX.2, V5.M6.AUX.2).
    patched_script, applied, skipped = apply_validated_fixes(patched_script)
    applied = v2m3_applied + v1m2_applied + applied
    skipped = v2m3_skipped + v1m2_skipped + skipped
    print(f"[INFO] Applied {len(applied)} validated edit(s): {applied}",
          file=sys.stderr)
    if skipped:
        print(f"[INFO] Skipped (cell not present, or structure didn't match "
              f"the validated pattern - safe no-op): {skipped}", file=sys.stderr)

    # Apply the M4 grid-alignment placement shifts decided above.
    patched_script = apply_grid_alignment_fixes(patched_script, shift_map)
    print(f"[INFO] Applied {len(grid_applied)} grid-alignment edit(s): {grid_applied}",
          file=sys.stderr)
    if grid_skipped:
        print(f"[INFO] Skipped grid-alignment (no confirmed-safe shift for this "
              f"pair/row - safe no-op): {grid_skipped}", file=sys.stderr)
    applied = applied + grid_applied

    # VIA_VIA45_1_2_58_58 stub fix - unblocks the M5.AUX.1 rail candidates
    # the plain rail fix below correctly refuses to touch on its own (see
    # the module comment above apply_via_stub_fix() for the 3-round
    # LLM-assisted derivation). Runs FIRST so the plain rail fix doesn't
    # re-attempt these same rails.
    patched_script, stub_applied, stub_skipped = apply_via_stub_fix(patched_script, case_name)
    applied = applied + stub_applied
    skipped = skipped + stub_skipped
    if stub_applied:
        print(f"[INFO] Applied {len(stub_applied)} via-stub fix(es): {stub_applied}",
              file=sys.stderr)

    # M5.AUX.1/M6.AUX.1 grid-rail fix - fully deterministic (see the module
    # comment above apply_grid_rail_fix(): the stub-check already IS the
    # safety verification, so there's no ambiguous choice for a model to
    # make here, unlike M4.S.5 below).
    patched_script, rail_applied, rail_skipped = apply_grid_rail_fix(patched_script, case_name)
    applied = applied + rail_applied
    skipped = skipped + rail_skipped
    if rail_applied:
        print(f"[INFO] Applied {len(rail_applied)} M5/M6 grid-rail edit(s): "
              f"{rail_applied}", file=sys.stderr)

    drc_report_path = Path(info.get("path_to_drc_report", ""))
    drc_report = None
    if drc_report_path.is_file():
        try:
            drc_report = json.loads(drc_report_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[WARN] Could not read DRC report: {e}", file=sys.stderr)

    # M4.S.5 fix - the one genuine, ship-affecting model call in this agent.
    # See the module comment above apply_llm_m4s5_fix(): candidate generation
    # and safety-checking are deterministic; which candidate ships (if any)
    # is a real model decision, re-validated against the candidate list
    # before ever being applied.
    patched_script, m4s5_applied, m4s5_skipped = apply_llm_m4s5_fix(
        patched_script, drc_report, endpoint, model_name)
    applied = applied + m4s5_applied
    skipped = skipped + m4s5_skipped
    if m4s5_applied:
        print(f"[INFO] Applied {len(m4s5_applied)} model-selected M4.S.5 edit(s): "
              f"{m4s5_applied}", file=sys.stderr)
    if m4s5_skipped:
        print(f"[INFO] Skipped M4.S.5 (no safe candidate, or model rejected/"
              f"response invalid - safe no-op): {m4s5_skipped}", file=sys.stderr)

    # Exercise the required model_endpoint interface further: ask for a
    # repair-planning analysis of what else looks fixable. Logged for the
    # next iteration only - its output does NOT affect what gets written to
    # output_path (unlike the M4.S.5 call above). See NOTES.md for why
    # (every attempt so far to auto-generalize THIS analysis into new edits
    # without per-edit KLayout validation has made things worse, not better).
    analysis_text = ""
    if endpoint and drc_report:
        try:
            rules_summary = "\n".join(
                f"- {rule}: {r['violation_count']} violation(s) - {r['description']}"
                for rule, r in drc_report.get("rules", {}).items()
            )
            prompt = (
                f"You are a physical design engineer reviewing a DRC report for an ASAP7 block "
                f"layout ({case_name}). Total violations: {drc_report.get('total_violations')}.\n\n"
                f"Rule breakdown:\n{rules_summary}\n\n"
                f"Already-applied fixes this run: {applied or 'none'}.\n\n"
                f"Briefly describe, in a few sentences, which of the REMAINING violation "
                f"categories look most mechanically fixable versus which require "
                f"cross-referencing paired shapes. This analysis is for planning only - do "
                f"not propose exact coordinate edits."
            )
            analysis_text, _usage = call_model(endpoint, prompt, model_name, max_tokens=1024)
        except Exception as e:
            print(f"[WARN] DRC analysis call skipped: {e}", file=sys.stderr)

    if analysis_text:
        (temp_dir / f"{case_name}_drc_analysis.txt").write_text(analysis_text, encoding="utf-8")
        print(f"[INFO] Saved model DRC analysis to {temp_dir / f'{case_name}_drc_analysis.txt'}",
              file=sys.stderr)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(patched_script, encoding="utf-8")
    print(f"[DONE] Wrote repaired script to {output_path} "
          f"({len(applied)} validated edit(s) applied)", file=sys.stderr)


if __name__ == "__main__":
    main()
