import re

legal_text = open("/tmp/block1_legalized.def").read()
filler_re = re.compile(
    r"-\s+(FILLER\S+)\s+(FILLER\w*_ASAP7_75t_R)\s+\+\s+SOURCE\s+DIST\s+\+\s+PLACED\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(\S+)\s*;")

fillers = []
for m in filler_re.finditer(legal_text):
    name, cell_type, x_def, y_def, orient = m.groups()
    fillers.append((name, cell_type, int(x_def), int(y_def), orient))

print(f"Extracted {len(fillers)} filler instances")
for f in fillers[:5]:
    print(" ", f)

ORIENT_TO_ROT = {"N": 0, "W": 1, "S": 2, "E": 3, "FS": 4, "FW": 5, "FN": 6, "FE": 7}

lines = ["\n# --- Filler cells added by OpenROAD filler_placement (fills gaps left by moved cells) ---"]
for name, cell_type, x_def, y_def, orient in fillers:
    rot = ORIENT_TO_ROT[orient]
    mirror = "True" if rot >= 4 else "False"
    base_rot = rot - 4 if rot >= 4 else rot
    x_kl, y_kl = x_def * 4, y_def * 4
    lines.append(f"cell_Block1.insert(pya.CellInstArray(cell_{cell_type}.cell_index(), "
                 f"pya.Trans({base_rot}, {mirror}, pya.Vector({x_kl}, {y_kl}))))")

injected = "\n".join(lines) + "\n"

text = open("/tmp/Block1_final.py").read()
idx = text.rfind("layout.write(")
assert idx != -1
text = text[:idx] + injected + "\n" + text[idx:]
with open("/tmp/Block1_final2.py", "w") as f:
    f.write(text)
print(f"Wrote /tmp/Block1_final2.py with {len(fillers)} filler cells added")
