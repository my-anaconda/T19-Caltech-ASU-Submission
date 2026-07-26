import re
import json

routed_geo = json.load(open("/tmp/routed_geometry_frozen.json"))

PRISTINE_PATH = "/home/defyscience/asu_eval/testcase/asap7/block/layout_script/Block1.py"
text = open(PRISTINE_PATH).read()

MUTABLE_METAL_LAYERS = {20, 30, 40, 50, 60}  # M2-M6; M1 (19) is the frozen seed layer
MUTABLE_VIA_PREFIXES = ("VIA_VIA12", "VIA_VIA23", "VIA_VIA34", "VIA_VIA45", "VIA_VIA56")  # V1-V5

# --- Strip all top-level M2-M6 polygon inserts + their polygon var defs ---
poly_def_re = re.compile(r"^(p\d+) = pya\.Polygon\(\[.*?\]\)\s*$", re.MULTILINE)
insert_re = re.compile(
    r"^cell_Block1\.shapes\(layout\.layer\(pya\.LayerInfo\((\d+), 0\)\)\)\.insert\((p\d+)\)\s*$",
    re.MULTILINE,
)

poly_def_spans = {mm.group(1): mm.span() for mm in poly_def_re.finditer(text)}

spans_to_remove = []
removed_shapes = 0
removed_vars = set()
for mm in insert_re.finditer(text):
    layer_num, var_name = int(mm.group(1)), mm.group(2)
    if layer_num not in MUTABLE_METAL_LAYERS:
        continue
    spans_to_remove.append(mm.span())
    if var_name in poly_def_spans:
        spans_to_remove.append(poly_def_spans[var_name])
        removed_vars.add(var_name)
    removed_shapes += 1

print(f"Stripping {removed_shapes} top-level M2-M6 shape insert(s) + {len(removed_vars)} polygon def(s)")

# --- Strip top-level via CellInstArray inserts, but ONLY for via types the
# new routing actually reproduces (plain default-via masters like
# VIA_VIA12/23/34 - whatever TritonRoute emits). Leave every other via type
# completely untouched: unique-config multi-cut variants (e.g.
# VIA_VIA23_1_3_36_36) and any plain via type unused by the new routing
# (e.g. VIA_VIA45, VIA_VIA56 in this run) are very likely power/ground-rail
# or fixed-config vias outside the signal reroute's scope - stripping them
# with nothing to replace them left 5 via master cells with zero instances
# anywhere, which KLayout treats as extra top cells ("multiple top cells"
# in DRC/render). Only touch what we're actually going to replace. ---
new_via_types_used = {via_name for (via_name, x, y) in routed_geo["vias"]}

via_inst_re = re.compile(
    r"^cell_Block1\.insert\(pya\.CellInstArray\(cell_(VIA_\w+)\.cell_index\(\), "
    r"pya\.Trans\((\d+), (True|False), pya\.Vector\((-?\d+), (-?\d+)\)\)\)\)\s*$",
    re.MULTILINE,
)
via_removed = 0
for mm in via_inst_re.finditer(text):
    via_type = mm.group(1)
    if via_type not in new_via_types_used:
        continue
    spans_to_remove.append(mm.span())
    via_removed += 1

print(f"Stripping {via_removed} top-level via instance(s) of types the new routing replaces: {sorted(new_via_types_used)}")

for start, end in sorted(spans_to_remove, key=lambda s: s[0], reverse=True):
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end) + 1
    text = text[:line_start] + text[line_end:]

with open("/tmp/Block1_ripped_frozen.py", "w") as f:
    f.write(text)
print(f"Wrote /tmp/Block1_ripped_frozen.py")

# --- Inject new OpenROAD-generated M2-M6 routing + V1-V5 vias ---
WIRE_WIDTH_DEF = 18
half_w_def = WIRE_WIDTH_DEF // 2


def to_kl(v):
    return v * 4


lines = []
lines.append("\n# --- Injected by OpenROAD constrained reroute (M1/placement frozen, M2-M6 only) ---")

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

for (via_name, x, y) in routed_geo["vias"]:
    cell_var = f"cell_{via_name}"
    lines.append(f"cell_Block1.insert(pya.CellInstArray({cell_var}.cell_index(), "
                 f"pya.Trans(0, False, pya.Vector({to_kl(x)}, {to_kl(y)}))))")
    n += 1

injected_code = "\n".join(lines) + "\n"

ripped_text = open("/tmp/Block1_ripped_frozen.py").read()
assert "layout.write(" in ripped_text
idx = ripped_text.rfind("layout.write(")
final_text = ripped_text[:idx] + injected_code + "\n" + ripped_text[idx:]

with open("/tmp/Block1_frozen_final.py", "w") as f:
    f.write(final_text)
print(f"Injected {n} new geometry statements into /tmp/Block1_frozen_final.py")
