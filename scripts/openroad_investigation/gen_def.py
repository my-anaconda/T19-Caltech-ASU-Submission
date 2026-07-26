import json

data = json.load(open("/tmp/block1_instances_regex.json"))

# KLayout DBU (0.00025 um/unit) -> DEF DBU (0.001 um/unit, i.e. 1000 units/micron
# per the real ASAP7 tech LEF's DATABASE MICRONS 1000): multiply by 0.25.
def cv(v):
    x = v * 0.25
    assert abs(x - round(x)) < 1e-6, f"non-integer DEF coordinate: {v} -> {x}"
    return round(x)

SITE_W = 54   # 0.054um * 1000
SITE_H = 270  # 0.270um * 1000

xs = [cv(d["x"]) for d in data]
ys = [cv(d["y"]) for d in data]
print("X range:", min(xs), max(xs), "| divisible by site_w?", all((x % SITE_W) == 0 for x in xs))
print("Y range:", min(ys), max(ys), "| divisible by site_h?", all((y % SITE_H) == 0 for y in ys))
print("Y row indices (y/site_h):", sorted(set(y // SITE_H for y in ys)))

margin_rows = 1
die_y0 = (min(ys) // SITE_H - margin_rows) * SITE_H
die_y1 = (max(ys) // SITE_H + margin_rows + 1) * SITE_H  # +1 since cell occupies one row above its origin
die_x0 = (min(xs) // SITE_W - margin_rows) * SITE_W
die_x1 = (max(xs) // SITE_W + margin_rows + 4) * SITE_W  # extra margin in X (cells are wider than 1 site)

num_rows = (die_y1 - die_y0) // SITE_H
num_x_sites = (die_x1 - die_x0) // SITE_W
print(f"die area: ({die_x0},{die_y0}) to ({die_x1},{die_y1}); num_rows={num_rows}, x_sites_per_row={num_x_sites}")
assert (die_y1 - die_y0) % SITE_H == 0
assert (die_x1 - die_x0) % SITE_W == 0

# pya.Trans rot code -> DEF ORIENT, verified empirically (not assumed) against
# the real OpenROAD odb API: generated one-instance DEFs for all 8 DEF ORIENT
# strings using a real ASAP7 cell (BUFx3_ASAP7_75t_R), read them back via
# `ord::get_db_block`, and compared the resulting VDD/pin-A pin bounding boxes
# against the same 8 pya.Trans rot codes applied to the cell's real LEF pin
# geometry (re-anchored to the cell bbox's min corner, matching DEF's placement
# convention). Every rot code matched exactly one DEF orient with zero
# ambiguity. This mapping is NOT the naive/guessed one (rot 4-7 do not map to
# FN/FE/FS/FW in that order - it's FS/FW/FN/FE).
ROT_TO_ORIENT = {0: "N", 1: "W", 2: "S", 3: "E", 4: "FS", 5: "FW", 6: "FN", 7: "FE"}

lines = []
lines.append("VERSION 5.8 ;")
lines.append('DIVIDERCHAR "/" ;')
lines.append('BUSBITCHARS "[]" ;')
lines.append("DESIGN Block1 ;")
lines.append("UNITS DISTANCE MICRONS 1000 ;")
lines.append(f"DIEAREA ( {die_x0} {die_y0} ) ( {die_x1} {die_y1} ) ;")
lines.append("")
# NOTE: unlike COMPONENTS, DEF's ROW section has no "ROWS <n> ;"/"END ROWS"
# wrapper - just consecutive ROW lines (confirmed against a real example DEF
# bundled in the ORFS image, flow/designs/nangate45/aes/aes_ng45_fp.def).
for r in range(num_rows):
    y = die_y0 + r * SITE_H
    orient = "FS" if (r % 2 == 1) else "N"  # standard alternating row flip
    lines.append(f"ROW ROW_{r} asap7sc7p5t {die_x0} {y} {orient} DO {num_x_sites} BY 1 STEP {SITE_W} 0 ;")
lines.append("")
lines.append(f"COMPONENTS {len(data)} ;")
for i, d in enumerate(data):
    x = cv(d["x"]); y = cv(d["y"])
    orient = ROT_TO_ORIENT[d["rot"]]
    inst_name = f"inst_{i}_{d['cell_type']}"
    lines.append(f"- {inst_name} {d['cell_type']} + PLACED ( {x} {y} ) {orient} ;")
lines.append("END COMPONENTS")
lines.append("")
lines.append("END DESIGN")

with open("/tmp/block1.def", "w") as f:
    f.write("\n".join(lines) + "\n")
print(f"Wrote /tmp/block1.def with {len(data)} components")
