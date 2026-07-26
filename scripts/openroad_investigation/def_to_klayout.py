import re
import json

routed_def = open("/home/defyscience/asu_eval/block1_fully_routed.def").read()

LAYER_NUM = {"M1": 19, "M2": 20, "M3": 30, "M4": 40, "M5": 50, "M6": 60}
WIRE_WIDTH_DEF = 18  # DEF units, confirmed from tech LEF (0.018um min width, M1-M3)
VIA_CELL_NAME = {"VIA12": "VIA_VIA12", "VIA23": "VIA_VIA23", "VIA34": "VIA_VIA34", "VIA45": "VIA_VIA45"}

# Extract the NETS...END NETS block
m = re.search(r"\nNETS \d+ ;\n(.*?)\nEND NETS\n", routed_def, re.DOTALL)
nets_block = m.group(1)

# Split into individual net records (each starts with "- net_")
net_records = re.split(r"\n(?=\s*- net_)", nets_block)

def tokenize(routing_text):
    """Tokenize a ROUTED/NEW clause's point-and-via list into a flat token list."""
    # Turn "( 855 495 )" -> point tuples, keep bare words (layer names, VIA names, RECT) as-is
    tokens = []
    i = 0
    for tok in re.findall(r"\(([^()]*)\)|(\S+)", routing_text):
        paren_content, word = tok
        if paren_content != "" or (paren_content == "" and word == ""):
            if paren_content.strip():
                parts = paren_content.split()
                tokens.append(("POINT", parts))
        else:
            tokens.append(("WORD", word))
    return tokens

wires = []  # (layer_num, x1,y1,x2,y2) rects
vias = []   # (via_cell_name, x, y)
pads = []   # (layer_num, x,y, dx1,dy1,dx2,dy2) explicit RECT

total_nets_with_routing = 0
for rec in net_records:
    if "+ ROUTED" not in rec:
        continue
    total_nets_with_routing += 1
    # Get everything from "+ ROUTED" to the closing ";"
    route_part = rec.split("+ ROUTED", 1)[1]
    route_part = route_part.rsplit(";", 1)[0]
    full_text = "ROUTED " + route_part

    # Walk segment by segment: split on NEW/ROUTED keywords which start a new sub-path
    segments = re.split(r"\b(ROUTED|NEW)\b", full_text)
    # segments alternates: '', 'ROUTED', ' M3 ( 855 495 ) ( * 999 )', 'NEW', ' M1 ...', ...
    cur = []
    for i in range(1, len(segments), 2):
        kind = segments[i]
        body = segments[i + 1].strip()
        toks = tokenize(body)
        if not toks:
            continue
        # first token(s): layer name (a WORD), possibly followed by RECT keyword directly (rare)
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

        # Resolve '*' wildcards against the previous ABSOLUTE point (tracked globally per net)
        resolved_pts = []
        for p in pts:
            x_s, y_s = p[0], p[1]
            if x_s == "*":
                x = resolved_pts[-1][0] if resolved_pts else (cur[-1][0] if cur else None)
            else:
                x = int(x_s)
            if y_s == "*":
                y = resolved_pts[-1][1] if resolved_pts else (cur[-1][1] if cur else None)
            else:
                y = int(y_s)
            resolved_pts.append((x, y))
        if resolved_pts:
            cur = resolved_pts  # track last path's points as context for next segment's wildcard base... but each NEW starts its own point; simplest: use its own first point only
        # emit wire segments (consecutive point pairs)
        if layer in LAYER_NUM and len(resolved_pts) >= 2:
            for a, b in zip(resolved_pts, resolved_pts[1:]):
                wires.append((LAYER_NUM[layer], a[0], a[1], b[0], b[1]))
        # emit via
        if via_name and resolved_pts:
            vias.append((VIA_CELL_NAME[via_name], resolved_pts[0][0], resolved_pts[0][1]))
        # emit explicit rect pad
        if rect_offsets and layer in LAYER_NUM and resolved_pts:
            px, py = resolved_pts[0]
            wires_rect = (LAYER_NUM[layer], px + rect_offsets[0], py + rect_offsets[1],
                          px + rect_offsets[2], py + rect_offsets[3])
            pads.append(wires_rect)

print(f"Nets with routing: {total_nets_with_routing}")
print(f"Wire segments: {len(wires)}, vias: {len(vias)}, explicit pads: {len(pads)}")
for w in wires[:5]:
    print("  wire", w)
for v in vias[:5]:
    print("  via", v)
for p in pads[:5]:
    print("  pad", p)

with open("/tmp/routed_geometry.json", "w") as f:
    json.dump({"wires": wires, "vias": vias, "pads": pads}, f, indent=2)
print("Wrote /tmp/routed_geometry.json")
