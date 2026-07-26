import pya
import json
import re

lef_pins = json.load(open("/home/defyscience/asu_eval/lef_pins.json"))  # DEF units (1000/um)
instances = json.load(open("/tmp/block1_instances_regex.json"))  # KLayout units (4000/um)
netlist = json.load(open("/tmp/block1_netlist_summary.json"))

lef_text = open("/home/defyscience/asu_eval/asap7sc7p5t_28_R.lef").read()
size_re = re.compile(r"MACRO (\S+)\s*\n\s*CLASS \S+ ;\s*\n\s*ORIGIN 0 0 ;\s*\n\s*FOREIGN \S+ 0 0 ;\s*\n\s*SIZE ([\d.]+) BY ([\d.]+) ;")
cell_size_um = {}
for m in size_re.finditer(lef_text):
    name, w, h = m.group(1), float(m.group(2)), float(m.group(3))
    cell_size_um[name] = (w, h)
print(f"Parsed sizes for {len(cell_size_um)} macros")

ROT_TO_ORIENT = {0: "N", 1: "W", 2: "S", 3: "E", 4: "FS", 5: "FW", 6: "FN", 7: "FE"}

def pya_trans_for(rot):
    if rot < 4:
        return pya.Trans(rot, False, pya.Vector(0, 0))
    return pya.Trans(rot - 4, True, pya.Vector(0, 0))

def transform_rect_um(rect_def_units, rot, cell_w_um, cell_h_um):
    """rect_def_units: (xlo,ylo,xhi,yhi) in DEF units (1000/um).
    Returns (xlo,ylo,xhi,yhi) in microns, transformed by rot and re-anchored
    so the cell's own bbox min-corner lands at (0,0) - matching DEF PLACED
    semantics (verified methodology from the orientation-mapping work)."""
    t = pya_trans_for(rot)
    x0, y0, x1, y1 = rect_def_units
    pts = [pya.Point(x0, y0), pya.Point(x1, y0), pya.Point(x1, y1), pya.Point(x0, y1)]
    tpts = [t * p for p in pts]
    xs = [p.x for p in tpts]; ys = [p.y for p in tpts]
    rxlo, rylo, rxhi, ryhi = min(xs), min(ys), max(xs), max(ys)

    cw_def, ch_def = cell_w_um * 1000, cell_h_um * 1000
    cell_pts = [pya.Point(0, 0), pya.Point(int(round(cw_def)), 0),
                pya.Point(int(round(cw_def)), int(round(ch_def))), pya.Point(0, int(round(ch_def)))]
    ctpts = [t * p for p in cell_pts]
    cxs = [p.x for p in ctpts]; cys = [p.y for p in ctpts]
    dx, dy = -min(cxs), -min(cys)

    return ((rxlo + dx) / 1000.0, (rylo + dy) / 1000.0, (rxhi + dx) / 1000.0, (ryhi + dy) / 1000.0)

# Build absolute pin boxes (microns) for all 143 instances
pin_boxes = []  # (inst_idx, pin_name, xmin, ymin, xmax, ymax)
missing_size = set()
for i, inst in enumerate(instances):
    ct = inst["cell_type"]
    if ct not in lef_pins:
        continue
    if ct not in cell_size_um:
        missing_size.add(ct)
        continue
    cw, ch = cell_size_um[ct]
    inst_x_um = inst["x"] * 0.00025
    inst_y_um = inst["y"] * 0.00025
    for pin_name, rects in lef_pins[ct].items():
        # union bbox of all this pin's rects, transformed+anchored, then union
        xs_lo, ys_lo, xs_hi, ys_hi = [], [], [], []
        for r in rects:
            xlo, ylo, xhi, yhi = transform_rect_um(tuple(r), inst["rot"], cw, ch)
            xs_lo.append(xlo); ys_lo.append(ylo); xs_hi.append(xhi); ys_hi.append(yhi)
        pxlo = min(xs_lo) + inst_x_um; pylo = min(ys_lo) + inst_y_um
        pxhi = max(xs_hi) + inst_x_um; pyhi = max(ys_hi) + inst_y_um
        pin_boxes.append((i, pin_name, pxlo, pylo, pxhi, pyhi))

print(f"Missing cell sizes for: {missing_size}")
print(f"Built {len(pin_boxes)} absolute pin boxes")

def find_pin(x, y, eps=0.002):
    matches = []
    for (idx, pname, xlo, ylo, xhi, yhi) in pin_boxes:
        if (xlo - eps) <= x <= (xhi + eps) and (ylo - eps) <= y <= (yhi + eps):
            matches.append((idx, pname))
    return matches

# Resolve each net's std-cell terminals
resolved_nets = []
unresolved_count = 0
resolved_count = 0
for net in netlist:
    members = []
    for sp in net["subcircuit_pins"]:
        cell = sp["subcircuit_cell"]
        if cell.startswith("VIA_"):
            continue  # vias aren't in our DEF COMPONENTS; router regenerates them
        matches = find_pin(sp["x"], sp["y"])
        if len(matches) == 1:
            members.append({"inst_idx": matches[0][0], "pin": matches[0][1]})
            resolved_count += 1
        elif len(matches) == 0:
            unresolved_count += 1
        else:
            # ambiguous - pick nearest by simple heuristic (first match), flag it
            members.append({"inst_idx": matches[0][0], "pin": matches[0][1], "ambiguous": True})
            resolved_count += 1
    if members:
        resolved_nets.append({"net_id": net["net_id"], "members": members})

from collections import Counter
pin_name_counts = Counter()
for net in netlist:
    for sp in net["subcircuit_pins"]:
        if sp["subcircuit_cell"].startswith("VIA_"):
            continue
        for m in find_pin(sp["x"], sp["y"]):
            pin_name_counts[m[1]] += 1
print("Pin name distribution among ALL matches:", pin_name_counts)
print(f"Resolved terminals: {resolved_count}, unresolved: {unresolved_count}")
print(f"Nets with >=1 resolved std-cell terminal: {len(resolved_nets)} / {len(netlist)}")
multi = [n for n in resolved_nets if len(n["members"]) >= 2]
print(f"Nets with >=2 std-cell terminals (real routable nets): {len(multi)}")
for n in multi[:5]:
    print(" ", n)

with open("/tmp/resolved_nets.json", "w") as f:
    json.dump(resolved_nets, f, indent=2)
