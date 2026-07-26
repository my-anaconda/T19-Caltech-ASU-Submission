import pya
import json
import re

lef_pins = json.load(open("/home/defyscience/asu_eval/lef_pins.json"))
instances = json.load(open("/tmp/block1_instances_verified.json"))
netlist = json.load(open("/tmp/block1_netlist_summary.json"))

lef_text = open("/home/defyscience/asu_eval/asap7sc7p5t_28_R.lef").read()
size_re = re.compile(r"MACRO (\S+)\s*\n\s*CLASS \S+ ;\s*\n\s*ORIGIN 0 0 ;\s*\n\s*FOREIGN \S+ 0 0 ;\s*\n\s*SIZE ([\d.]+) BY ([\d.]+) ;")
cell_size_um = {}
for m in size_re.finditer(lef_text):
    cell_size_um[m.group(1)] = (float(m.group(2)), float(m.group(3)))

def pya_trans_for(rot):
    if rot < 4:
        return pya.Trans(rot, False, pya.Vector(0, 0))
    return pya.Trans(rot - 4, True, pya.Vector(0, 0))

def pin_boxes_for_instance(inst):
    ct = inst["cell_type"]
    if ct not in lef_pins or ct not in cell_size_um:
        return {}
    cw, ch = cell_size_um[ct]
    ix_um = inst["x"] * 0.00025
    iy_um = inst["y"] * 0.00025
    t = pya_trans_for(inst["rot"])
    cw_def, ch_def = int(round(cw * 1000)), int(round(ch * 1000))
    cell_pts = [pya.Point(0, 0), pya.Point(cw_def, 0), pya.Point(cw_def, ch_def), pya.Point(0, ch_def)]
    ctpts = [t * p for p in cell_pts]
    dx = -min(p.x for p in ctpts); dy = -min(p.y for p in ctpts)

    out = {}
    for pin_name, rects in lef_pins[ct].items():
        if pin_name in ("VDD", "VSS"):
            continue
        xlos, ylos, xhis, yhis = [], [], [], []
        for r in rects:
            x0, y0, x1, y1 = r
            pts = [pya.Point(x0, y0), pya.Point(x1, y0), pya.Point(x1, y1), pya.Point(x0, y1)]
            tpts = [t * p for p in pts]
            xs = [p.x for p in tpts]; ys = [p.y for p in tpts]
            xlos.append(min(xs)); ylos.append(min(ys)); xhis.append(max(xs)); yhis.append(max(ys))
        xlo = (min(xlos) + dx) / 1000.0 + ix_um
        ylo = (min(ylos) + dy) / 1000.0 + iy_um
        xhi = (max(xhis) + dx) / 1000.0 + ix_um
        yhi = (max(yhis) + dy) / 1000.0 + iy_um
        out[pin_name] = (xlo, ylo, xhi, yhi)
    return out

inst_by_pos = {}
for i, inst in enumerate(instances):
    key = (round(inst["x"] * 0.00025, 6), round(inst["y"] * 0.00025, 6))
    inst_by_pos.setdefault(key, []).append(i)

def find_instance(x, y, tol=0.001):
    key = (round(x, 6), round(y, 6))
    if key in inst_by_pos:
        return inst_by_pos[key]
    for (kx, ky), idxs in inst_by_pos.items():
        if abs(kx - x) < tol and abs(ky - y) < tol:
            return idxs
    return []

def box_center(box):
    xlo, ylo, xhi, yhi = box
    return ((xlo + xhi) / 2, (ylo + yhi) / 2)

def dist(p1, p2):
    return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5

resolved_nets = []
unmatched_instance = 0
no_pins_available = 0
for net in netlist:
    std_members = []
    anchors = []
    for sp in net["subcircuit_pins"]:
        cell = sp["subcircuit_cell"]
        if cell.startswith("VIA_"):
            anchors.append((sp["x"], sp["y"]))
        else:
            idxs = find_instance(sp["x"], sp["y"])
            if not idxs:
                unmatched_instance += 1
                continue
            std_members.append(idxs[0])
    if not std_members:
        continue
    members_out = []
    for inst_idx in set(std_members):
        boxes = pin_boxes_for_instance(instances[inst_idx])
        if not boxes:
            no_pins_available += 1
            continue
        ref_points = anchors if anchors else [(instances[inst_idx]["x"] * 0.00025, instances[inst_idx]["y"] * 0.00025)]
        best_pin, best_d = None, None
        for pname, box in boxes.items():
            c = box_center(box)
            d = min(dist(c, rp) for rp in ref_points)
            if best_d is None or d < best_d:
                best_d, best_pin = d, pname
        members_out.append({"inst_idx": inst_idx, "pin": best_pin, "dist_um": round(best_d, 4)})
    if members_out:
        resolved_nets.append({"net_id": net["net_id"], "members": members_out})

print(f"Total nets: {len(netlist)}, resolved nets (with >=1 std-cell member): {len(resolved_nets)}")
print(f"Unmatched instance lookups: {unmatched_instance}, no-pins-available: {no_pins_available}")
multi = [n for n in resolved_nets if len(n["members"]) >= 2]
print(f"Nets with >=2 std-cell members (real routable signal nets): {len(multi)}")

with open("/tmp/resolved_nets_frozen.json", "w") as f:
    json.dump(resolved_nets, f, indent=2)
print("Wrote /tmp/resolved_nets_frozen.json")
