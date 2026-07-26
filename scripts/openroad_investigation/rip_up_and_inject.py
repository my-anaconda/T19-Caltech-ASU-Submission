import pya
import json
import re

instances = json.load(open("/tmp/block1_instances_regex.json"))  # ORIGINAL (pre-move) positions
moved = json.load(open("/tmp/block1_moved.json"))
routed_geo = json.load(open("/tmp/routed_geometry.json"))
lef_pins = json.load(open("/home/defyscience/asu_eval/lef_pins.json"))

lef_text = open("/home/defyscience/asu_eval/asap7sc7p5t_28_R.lef").read()
size_re = re.compile(r"MACRO (\S+)\s*\n\s*CLASS \S+ ;\s*\n\s*ORIGIN 0 0 ;\s*\n\s*FOREIGN \S+ 0 0 ;\s*\n\s*SIZE ([\d.]+) BY ([\d.]+) ;")
cell_size_um = {m.group(1): (float(m.group(2)), float(m.group(3))) for m in size_re.finditer(lef_text)}

LAYER_NUM = {"M1": 19, "M2": 20, "M3": 30, "M4": 40, "M5": 50, "M6": 60}

def pya_trans_for(rot):
    if rot < 4:
        return pya.Trans(rot, False, pya.Vector(0, 0))
    return pya.Trans(rot - 4, True, pya.Vector(0, 0))

def old_pin_boxes_kl(inst):
    """Old (pre-move) absolute pin boxes in KLayout units, for proximity search."""
    ct = inst["cell_type"]
    if ct not in lef_pins or ct not in cell_size_um:
        return []
    cw, ch = cell_size_um[ct]
    t = pya_trans_for(inst["rot"])
    cw_def, ch_def = int(round(cw * 1000)), int(round(ch * 1000))
    cell_pts = [pya.Point(0, 0), pya.Point(cw_def, 0), pya.Point(cw_def, ch_def), pya.Point(0, ch_def)]
    ctpts = [t * p for p in cell_pts]
    dxa = -min(p.x for p in ctpts); dya = -min(p.y for p in ctpts)
    boxes = []
    for pin_name, rects in lef_pins[ct].items():
        if pin_name in ("VDD", "VSS"):
            continue
        for r in rects:
            x0, y0, x1, y1 = r
            pts = [pya.Point(x0, y0), pya.Point(x1, y0), pya.Point(x1, y1), pya.Point(x0, y1)]
            tpts = [t * p for p in pts]
            xs = [p.x for p in tpts]; ys = [p.y for p in tpts]
            # DEF units -> KLayout units (*4), plus anchor shift (also DEF units) then instance abs position
            xlo = (min(xs) + dxa) * 4 + inst["x"]
            ylo = (min(ys) + dya) * 4 + inst["y"]
            xhi = (max(xs) + dxa) * 4 + inst["x"]
            yhi = (max(ys) + dya) * 4 + inst["y"]
            boxes.append((xlo, ylo, xhi, yhi))
    return boxes

# Collect old pin boxes (KLayout units) for every MOVED instance
moved_pin_boxes = []
for m in moved:
    inst = instances[m["index"]]
    moved_pin_boxes.extend(old_pin_boxes_kl(inst))
print(f"Collected {len(moved_pin_boxes)} old pin boxes across {len(moved)} moved instances")

MARGIN = 400  # KLayout units (~0.1um) search margin around each pin box

def near_any_moved_pin(x0, y0, x1, y1):
    for (bx0, by0, bx1, by1) in moved_pin_boxes:
        if x0 <= bx1 + MARGIN and x1 >= bx0 - MARGIN and y0 <= by1 + MARGIN and y1 >= by0 - MARGIN:
            return True
    return False

# --- Parse Block1_legalized.py to find and remove raw routing shapes near moved pins ---
src_path = "/tmp/Block1_legalized.py"
text = open(src_path).read()

# Raw top-level metal shape inserts: "pNNN = pya.Polygon([...]); cell_Block1.shapes(layout.layer(pya.LayerInfo(L, 0))).insert(pNNN)"
poly_def_re = re.compile(r"^(p\d+) = pya\.Polygon\(\[(.*?)\]\)\s*$", re.MULTILINE)
insert_re = re.compile(r"^cell_Block1\.shapes\(layout\.layer\(pya\.LayerInfo\((\d+), 0\)\)\)\.insert\((p\d+)\)\s*$", re.MULTILINE)

poly_defs = {mm.group(1): mm.group(2) for mm in poly_def_re.finditer(text)}
print(f"Found {len(poly_defs)} polygon variable definitions")

lines_to_remove_spans = []  # (start,end) char spans to blank out
removed_count = 0
for mm in insert_re.finditer(text):
    layer_num, var_name = int(mm.group(1)), mm.group(2)
    if layer_num not in LAYER_NUM.values():
        continue  # not a metal routing layer (e.g. boundary marker 235)
    poly_str = poly_defs.get(var_name)
    if not poly_str:
        continue
    pts = re.findall(r"Point\((-?\d+),\s*(-?\d+)\)", poly_str)
    if not pts:
        continue
    xs = [int(p[0]) for p in pts]; ys = [int(p[1]) for p in pts]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    if near_any_moved_pin(x0, y0, x1, y1):
        removed_count += 1
        # blank out both the polygon definition line and the insert line
        def_match = re.search(rf"^{var_name} = pya\.Polygon\(\[.*?\]\)\s*$", text, re.MULTILINE)
        if def_match:
            lines_to_remove_spans.append(def_match.span())
        lines_to_remove_spans.append(mm.span())

print(f"Marking {removed_count} old routing shape(s) for removal (near a moved cell's old pin)")

# Apply removals in reverse order
for start, end in sorted(lines_to_remove_spans, key=lambda s: s[0], reverse=True):
    # remove the whole line including trailing newline
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end) + 1
    text = text[:line_start] + text[line_end:]

# --- Also remove old VIA_* instance placements near a moved cell's old pin ---
via_inst_re = re.compile(
    r"^cell_Block1\.insert\(pya\.CellInstArray\(cell_(VIA_\w+)\.cell_index\(\), "
    r"pya\.Trans\((\d+), (True|False), pya\.Vector\((-?\d+), (-?\d+)\)\)\)\)\s*$", re.MULTILINE)
via_removed = 0
via_spans = []
for mm in via_inst_re.finditer(text):
    x, y = int(mm.group(4)), int(mm.group(5))
    if near_any_moved_pin(x, y, x, y):
        via_spans.append(mm.span())
        via_removed += 1
print(f"Marking {via_removed} old via instance(s) for removal")
for start, end in sorted(via_spans, key=lambda s: s[0], reverse=True):
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end) + 1
    text = text[:line_start] + text[line_end:]

with open("/tmp/Block1_ripped.py", "w") as f:
    f.write(text)
print(f"Wrote /tmp/Block1_ripped.py ({removed_count} shapes + {via_removed} vias removed)")
