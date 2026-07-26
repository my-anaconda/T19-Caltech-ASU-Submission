import re
import json

routed_def_text = open("/tmp/block1_frozen_routed.def").read()
routed_geo = json.load(open("/tmp/routed_geometry_frozen.json"))
net_shapes = json.load(open("/tmp/block1_net_shapes.json"))

PRISTINE_PATH = "/home/defyscience/asu_eval/testcase/asap7/block/layout_script/Block1.py"
text = open(PRISTINE_PATH).read()

LNAME_TO_NUM = {"M2": 20, "V1": 21, "M3": 30, "V2": 25, "M4": 40, "V3": 35, "M5": 50, "V4": 45, "M6": 60}
METAL_LAYER_NUMS = {20, 30, 40, 50, 60}
VIA_LAYER_NUMS = {21, 25, 35, 45}
# via type name -> its via layer number, by prefix match (handles suffixed multi-cut variants too)
VIA_TYPE_PREFIX_TO_LAYER = {"VIA_VIA12": 21, "VIA_VIA23": 25, "VIA_VIA34": 35, "VIA_VIA45": 45, "VIA_VIA56": 55}

# --- Step 1: which nets actually got real new routing? ---
m = re.search(r"\nNETS \d+ ;\n(.*?)\nEND NETS\n", routed_def_text, re.DOTALL)
net_records = re.split(r"\n(?=\s*- net_)", m.group(1))
rerouted_net_ids = set()
for rec in net_records:
    if "+ ROUTED" not in rec:
        continue
    nm = re.match(r"\s*- net_(\d+)", rec)
    if nm:
        rerouted_net_ids.add(int(nm.group(1)))
print(f"Nets with real new routing: {len(rerouted_net_ids)}")

# --- Step 2: collect old-shape bboxes (by layer) for ONLY the rerouted nets ---
old_bboxes_by_layer = {n: [] for n in (METAL_LAYER_NUMS | VIA_LAYER_NUMS)}
for entry in net_shapes:
    if entry["net_id"] not in rerouted_net_ids:
        continue
    for lname, boxes in entry["shapes_by_layer"].items():
        lnum = LNAME_TO_NUM.get(lname)
        if lnum is None:
            continue
        old_bboxes_by_layer[lnum].extend(boxes)

for lnum, boxes in old_bboxes_by_layer.items():
    print(f"  layer {lnum}: {len(boxes)} old shape bbox(es) from rerouted nets")


def bbox_contains_or_overlaps(a, b, tol=0):
    # a, b = [l, b_, r, t]; True if they overlap (with tolerance)
    al, ab, ar, at = a
    bl, bb, br, bt = b
    return not (ar + tol < bl or br + tol < al or at + tol < bb or bt + tol < ab)


def point_in_any_bbox(x, y, boxes, tol=200):
    for (l, b, r, t) in boxes:
        if (l - tol) <= x <= (r + tol) and (b - tol) <= y <= (t + tol):
            return True
    return False


# --- Step 3: strip only the M2-M6 top-level shapes that fall inside a
# rerouted net's old bbox (everything else - unrouted nets, rails,
# power/ground - keeps its original geometry untouched) ---
poly_def_re = re.compile(r"^(p\d+) = pya\.Polygon\(\[(.*?)\]\)\s*$", re.MULTILINE)
insert_re = re.compile(
    r"^cell_Block1\.shapes\(layout\.layer\(pya\.LayerInfo\((\d+), 0\)\)\)\.insert\((p\d+)\)\s*$",
    re.MULTILINE,
)
point_re = re.compile(r"Point\((-?\d+),\s*(-?\d+)\)")

poly_defs = {}
for mm in poly_def_re.finditer(text):
    pts = point_re.findall(mm.group(2))
    if pts:
        xs = [int(p[0]) for p in pts]; ys = [int(p[1]) for p in pts]
        poly_defs[mm.group(1)] = {"span": mm.span(), "bbox": [min(xs), min(ys), max(xs), max(ys)]}

spans_to_remove = []
removed_shapes = 0
for mm in insert_re.finditer(text):
    layer_num, var_name = int(mm.group(1)), mm.group(2)
    if layer_num not in METAL_LAYER_NUMS:
        continue
    pdef = poly_defs.get(var_name)
    if not pdef:
        continue
    if any(bbox_contains_or_overlaps(pdef["bbox"], b) for b in old_bboxes_by_layer[layer_num]):
        spans_to_remove.append(mm.span())
        spans_to_remove.append(pdef["span"])
        removed_shapes += 1

print(f"Stripping {removed_shapes} top-level M2-M6 shape(s) belonging to rerouted nets")

# --- Step 4: strip only via instances (ANY type) whose position falls
# inside a rerouted net's old via-layer bbox ---
via_inst_re = re.compile(
    r"^cell_Block1\.insert\(pya\.CellInstArray\(cell_(VIA_\w+)\.cell_index\(\), "
    r"pya\.Trans\((\d+), (True|False), pya\.Vector\((-?\d+), (-?\d+)\)\)\)\)\s*$",
    re.MULTILINE,
)
via_removed = 0
remaining_instance_count = {}  # via_type -> count still present after our removal
for mm in via_inst_re.finditer(text):
    via_type, rot, mirror, x, y = mm.group(1), mm.group(2), mm.group(3), int(mm.group(4)), int(mm.group(5))
    lnum = None
    for prefix, ln in VIA_TYPE_PREFIX_TO_LAYER.items():
        if via_type.startswith(prefix):
            lnum = ln
            break
    is_target = lnum is not None and lnum in VIA_LAYER_NUMS and point_in_any_bbox(x, y, old_bboxes_by_layer[lnum])
    if is_target:
        spans_to_remove.append(mm.span())
        via_removed += 1
    else:
        remaining_instance_count[via_type] = remaining_instance_count.get(via_type, 0) + 1

print(f"Stripping {via_removed} top-level via instance(s) belonging to rerouted nets")

for start, end in sorted(spans_to_remove, key=lambda s: s[0], reverse=True):
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end) + 1
    text = text[:line_start] + text[line_end:]

with open("/tmp/Block1_ripped_v2.py", "w") as f:
    f.write(text)
print("Wrote /tmp/Block1_ripped_v2.py")

# --- Step 5: inject new routing (M2-M6 wires + V1-V5 vias for the 38 rerouted nets) ---
WIRE_WIDTH_DEF = 18
half_w_def = WIRE_WIDTH_DEF // 2


def to_kl(v):
    return v * 4


lines = []
lines.append("\n# --- Injected by OpenROAD constrained reroute (M1/placement frozen, per-net surgical strip) ---")
n = 0
for (layer_num, x1, y1, x2, y2) in routed_geo["wires"]:
    if x1 == x2:
        bx0, by0 = x1 - half_w_def, min(y1, y2)
        bx1, by1 = x1 + half_w_def, max(y1, y2)
    elif y1 == y2:
        bx0, by0 = min(x1, x2), y1 - half_w_def
        bx1, by1 = max(x1, x2), y1 + half_w_def
    else:
        continue
    lines.append(f"cell_Block1.shapes(layout.layer(pya.LayerInfo({layer_num}, 0))).insert("
                 f"pya.Box({to_kl(bx0)}, {to_kl(by0)}, {to_kl(bx1)}, {to_kl(by1)}))")
    n += 1

for (layer_num, x0, y0, x1, y1) in routed_geo["pads"]:
    lines.append(f"cell_Block1.shapes(layout.layer(pya.LayerInfo({layer_num}, 0))).insert("
                 f"pya.Box({to_kl(x0)}, {to_kl(y0)}, {to_kl(x1)}, {to_kl(y1)}))")
    n += 1

new_via_counts = {}
for (via_name, x, y) in routed_geo["vias"]:
    cell_var = f"cell_{via_name}"
    lines.append(f"cell_Block1.insert(pya.CellInstArray({cell_var}.cell_index(), "
                 f"pya.Trans(0, False, pya.Vector({to_kl(x)}, {to_kl(y)}))))")
    n += 1
    new_via_counts[via_name] = new_via_counts.get(via_name, 0) + 1

injected_code = "\n".join(lines) + "\n"

ripped_text = open("/tmp/Block1_ripped_v2.py").read()
assert "layout.write(" in ripped_text
idx = ripped_text.rfind("layout.write(")
final_text = ripped_text[:idx] + injected_code + "\n" + ripped_text[idx:]

# --- Step 6: orphan cleanup - drop create_cell() for any via type that now
# has zero instances anywhere (all its old instances were among the ones we
# stripped, and no new instance of that exact type was injected) ---
all_types_after = dict(remaining_instance_count)
for vt, c in new_via_counts.items():
    all_types_after[vt] = all_types_after.get(vt, 0) + c

create_cell_re = re.compile(r'^cell_(VIA_VIA\S*) = layout\.create_cell\("VIA_VIA\S*"\)\s*$', re.MULTILINE)
orphan_spans = []
for mm in create_cell_re.finditer(final_text):
    via_type = mm.group(1)
    if all_types_after.get(via_type, 0) == 0:
        orphan_spans.append(mm.span())
        print(f"  dropping orphaned create_cell for {via_type} (0 instances remain after surgical reroute)")
for start, end in sorted(orphan_spans, key=lambda s: s[0], reverse=True):
    line_start = final_text.rfind("\n", 0, start) + 1
    line_end = final_text.find("\n", end) + 1
    final_text = final_text[:line_start] + final_text[line_end:]

with open("/tmp/Block1_final_v2.py", "w") as f:
    f.write(final_text)
print(f"Injected {n} new geometry statements into /tmp/Block1_final_v2.py")
