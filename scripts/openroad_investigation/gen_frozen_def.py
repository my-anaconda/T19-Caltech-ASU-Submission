import json

data = json.load(open("/tmp/block1_instances_verified.json"))

def cv(v):
    x = v * 0.25
    assert abs(x - round(x)) < 1e-6, f"non-integer DEF coordinate: {v} -> {x}"
    return round(x)

SITE_W = 54
SITE_H = 270

xs = [cv(d["x"]) for d in data]
ys = [cv(d["y"]) for d in data]

margin_rows = 1
die_y0 = (min(ys) // SITE_H - margin_rows) * SITE_H
die_y1 = (max(ys) // SITE_H + margin_rows + 1) * SITE_H
die_x0 = (min(xs) // SITE_W - margin_rows) * SITE_W
die_x1 = (max(xs) // SITE_W + margin_rows + 4) * SITE_W

num_rows = (die_y1 - die_y0) // SITE_H
num_x_sites = (die_x1 - die_x0) // SITE_W
assert (die_y1 - die_y0) % SITE_H == 0
assert (die_x1 - die_x0) % SITE_W == 0

ROT_TO_ORIENT = {0: "N", 1: "W", 2: "S", 3: "E", 4: "FS", 5: "FW", 6: "FN", 7: "FE"}

lines = []
lines.append("VERSION 5.8 ;")
lines.append('DIVIDERCHAR "/" ;')
lines.append('BUSBITCHARS "[]" ;')
lines.append("DESIGN Block1 ;")
lines.append("UNITS DISTANCE MICRONS 1000 ;")
lines.append(f"DIEAREA ( {die_x0} {die_y0} ) ( {die_x1} {die_y1} ) ;")
lines.append("")
for r in range(num_rows):
    y = die_y0 + r * SITE_H
    orient = "FS" if (r % 2 == 1) else "N"
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

nets_section = open("/tmp/def_nets_section_frozen.txt").read()
lines.append(nets_section.rstrip("\n"))
lines.append("")
lines.append("END DESIGN")

with open("/tmp/block1_frozen_routable.def", "w") as f:
    f.write("\n".join(lines) + "\n")
print(f"Wrote /tmp/block1_frozen_routable.def with {len(data)} components, die ({die_x0},{die_y0})-({die_x1},{die_y1})")

# Sanity: confirm this matches the die area of the original (pre-legalize) block1.def
print("die area check -> expect (378,270)-(3834,3780):", (die_x0, die_y0), (die_x1, die_y1))
