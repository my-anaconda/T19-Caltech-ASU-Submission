import json

routed_geo = json.load(open("/tmp/routed_geometry.json"))
WIRE_WIDTH_DEF = 18  # DEF units, confirmed min width M1-M3

text = open("/tmp/Block1_ripped.py").read()

lines = []
lines.append("\n# --- Injected by OpenROAD rip-up-and-reroute (Phase 3) ---")

half_w_def = WIRE_WIDTH_DEF // 2  # 9

def to_kl(v):
    return v * 4  # DEF units (1000/um) -> KLayout units (4000/um)

# Wires: convert each (layer_num, x1,y1,x2,y2) segment into a rectangle
n = 0
for (layer_num, x1, y1, x2, y2) in routed_geo["wires"]:
    if x1 == x2:  # vertical segment
        bx0, by0 = x1 - half_w_def, min(y1, y2)
        bx1, by1 = x1 + half_w_def, max(y1, y2)
    elif y1 == y2:  # horizontal segment
        bx0, by0 = min(x1, x2), y1 - half_w_def
        bx1, by1 = max(x1, x2), y1 + half_w_def
    else:
        continue  # non-manhattan, shouldn't happen for this router
    lines.append(f"cell_Block1.shapes(layout.layer(pya.LayerInfo({layer_num}, 0))).insert("
                 f"pya.Box({to_kl(bx0)}, {to_kl(by0)}, {to_kl(bx1)}, {to_kl(by1)}))")
    n += 1

# Pads (explicit RECT from DEF): (layer_num, x0,y0,x1,y1) already absolute
for (layer_num, x0, y0, x1, y1) in routed_geo["pads"]:
    lines.append(f"cell_Block1.shapes(layout.layer(pya.LayerInfo({layer_num}, 0))).insert("
                 f"pya.Box({to_kl(x0)}, {to_kl(y0)}, {to_kl(x1)}, {to_kl(y1)}))")
    n += 1

# Vias: instantiate the existing VIA_* cells (already defined earlier in the script)
for (via_name, x, y) in routed_geo["vias"]:
    cell_var = f"cell_{via_name}"
    lines.append(f"cell_Block1.insert(pya.CellInstArray({cell_var}.cell_index(), "
                 f"pya.Trans(0, False, pya.Vector({to_kl(x)}, {to_kl(y)}))))")
    n += 1

injected_code = "\n".join(lines) + "\n"

# Insert right before the final layout.write(...) call
assert 'layout.write(' in text
idx = text.rfind("layout.write(")
text = text[:idx] + injected_code + "\n" + text[idx:]

with open("/tmp/Block1_final.py", "w") as f:
    f.write(text)
print(f"Injected {n} new geometry statements into /tmp/Block1_final.py")
