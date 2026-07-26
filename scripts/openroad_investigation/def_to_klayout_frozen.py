import re
import json

routed_def = open("/tmp/block1_frozen_routed.def").read()

LAYER_NUM = {"M1": 19, "M2": 20, "M3": 30, "M4": 40, "M5": 50, "M6": 60}
VIA_CELL_NAME = {"VIA12": "VIA_VIA12", "VIA23": "VIA_VIA23", "VIA34": "VIA_VIA34",
                  "VIA45": "VIA_VIA45", "VIA56": "VIA_VIA56"}

m = re.search(r"\nNETS \d+ ;\n(.*?)\nEND NETS\n", routed_def, re.DOTALL)
nets_block = m.group(1)
net_records = re.split(r"\n(?=\s*- net_)", nets_block)


def tokenize(routing_text):
    tokens = []
    for tok in re.findall(r"\(([^()]*)\)|(\S+)", routing_text):
        paren_content, word = tok
        if paren_content != "" or (paren_content == "" and word == ""):
            if paren_content.strip():
                parts = paren_content.split()
                tokens.append(("POINT", parts))
        else:
            tokens.append(("WORD", word))
    return tokens


wires = []      # (layer_num, x1,y1,x2,y2) rects, M1 EXCLUDED
vias = []       # (via_cell_name, x, y)
pads = []       # (layer_num, x,y, x1,y1) explicit RECT, M1 EXCLUDED
skipped_m1_wires = 0
skipped_m1_pads = 0

total_nets_with_routing = 0
for rec in net_records:
    if "+ ROUTED" not in rec:
        continue
    total_nets_with_routing += 1
    route_part = rec.split("+ ROUTED", 1)[1]
    route_part = route_part.rsplit(";", 1)[0]
    full_text = "ROUTED " + route_part

    segments = re.split(r"\b(ROUTED|NEW)\b", full_text)
    cur = []
    for i in range(1, len(segments), 2):
        body = segments[i + 1].strip()
        toks = tokenize(body)
        if not toks:
            continue
        layer = None
        pts = []
        via_name = None
        rect_offsets = None
        idx = 0
        if toks[idx][0] == "WORD":
            layer = toks[idx][1]
            idx += 1
        while idx < len(toks):
            kind2, val = toks[idx]
            if kind2 == "POINT":
                pts.append(val)
                idx += 1
            elif kind2 == "WORD" and val in VIA_CELL_NAME:
                via_name = val
                idx += 1
            elif kind2 == "WORD" and val == "RECT":
                idx += 1
                if idx < len(toks) and toks[idx][0] == "POINT":
                    rect_offsets = [int(v) for v in toks[idx][1]]
                    idx += 1
            else:
                idx += 1

        resolved_pts = []
        for p in pts:
            x_s, y_s = p[0], p[1]
            x = resolved_pts[-1][0] if x_s == "*" and resolved_pts else (int(x_s) if x_s != "*" else (cur[-1][0] if cur else None))
            y = resolved_pts[-1][1] if y_s == "*" and resolved_pts else (int(y_s) if y_s != "*" else (cur[-1][1] if cur else None))
            resolved_pts.append((x, y))
        if resolved_pts:
            cur = resolved_pts

        if layer in LAYER_NUM and len(resolved_pts) >= 2:
            for a, b in zip(resolved_pts, resolved_pts[1:]):
                if layer == "M1":
                    skipped_m1_wires += 1
                    continue
                wires.append((LAYER_NUM[layer], a[0], a[1], b[0], b[1]))
        if via_name and resolved_pts:
            # via CELL layer isn't in LAYER_NUM/M1 filtering - vias always mutable (V1+)
            vias.append((VIA_CELL_NAME[via_name], resolved_pts[0][0], resolved_pts[0][1]))
        if rect_offsets and layer in LAYER_NUM and resolved_pts:
            if layer == "M1":
                skipped_m1_pads += 1
            else:
                px, py = resolved_pts[0]
                pads.append((LAYER_NUM[layer], px + rect_offsets[0], py + rect_offsets[1],
                             px + rect_offsets[2], py + rect_offsets[3]))

print(f"Nets with routing: {total_nets_with_routing}")
print(f"Wire segments (M2+): {len(wires)}, vias: {len(vias)}, explicit pads (M2+): {len(pads)}")
print(f"Skipped M1 wire segments (pin stubs, already provided by cell instances): {skipped_m1_wires}")
print(f"Skipped M1 explicit pads: {skipped_m1_pads}")

with open("/tmp/routed_geometry_frozen.json", "w") as f:
    json.dump({"wires": wires, "vias": vias, "pads": pads}, f, indent=2)
print("Wrote /tmp/routed_geometry_frozen.json")
