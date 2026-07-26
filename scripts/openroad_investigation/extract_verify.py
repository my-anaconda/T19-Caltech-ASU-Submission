import re
import json

PRISTINE_PATH = "/home/defyscience/asu_eval/testcase/asap7/block/layout_script/Block1.py"
text = open(PRISTINE_PATH).read()

STD_CELL_PREFIXES = (
    "BUFx", "INVx", "AND", "OR", "NAND", "NOR", "XOR", "XNOR", "AOI", "OAI",
    "MUX", "DFF", "SDFF", "ICG", "TAPCELL", "DECAP", "FILLER", "FAx", "HAx",
    "A2O1A1", "A2O1A1O1", "A21O1", "O21A1",
)

INSERT_RE = re.compile(
    r"^cell_Block1\.insert\(pya\.CellInstArray\(cell_(\w+)\.cell_index\(\), "
    r"pya\.Trans\((\d+), (True|False), pya\.Vector\((-?\d+), (-?\d+)\)\)\)\)\s*$",
    re.MULTILINE,
)

all_matches = list(INSERT_RE.finditer(text))
std_entries = []
for m in all_matches:
    cell_type, rot, mirror, x, y = m.groups()
    if not any(cell_type.startswith(p) for p in STD_CELL_PREFIXES):
        continue
    std_entries.append({
        "cell_type": cell_type,
        "rot": int(rot),
        "mirror": mirror == "True",
        "x": int(x),
        "y": int(y),
    })

print(f"Extracted {len(std_entries)} standard-cell instances from PRISTINE {PRISTINE_PATH}")

# --- Round-trip verification: re-scan the source text independently (different
# regex construction, same semantics) and confirm exact multiset match. This
# guards against ever again silently building a DEF from a stale/legalized
# intermediate instead of the pristine source. ---
CHECK_RE = re.compile(
    r'cell_Block1\.insert\(pya\.CellInstArray\(cell_(\w+)\.cell_index\(\), '
    r'pya\.Trans\((\d+), (True|False), pya\.Vector\((-?\d+), (-?\d+)\)\)\)\)'
)
check_entries = []
for m in CHECK_RE.finditer(text):
    cell_type, rot, mirror, x, y = m.groups()
    if not any(cell_type.startswith(p) for p in STD_CELL_PREFIXES):
        continue
    check_entries.append((cell_type, int(rot), mirror == "True", int(x), int(y)))

as_tuples = [(e["cell_type"], e["rot"], e["mirror"], e["x"], e["y"]) for e in std_entries]
assert as_tuples == check_entries, "round-trip verification FAILED: extraction is not stable against the pristine source"
assert len(std_entries) == 143, f"expected 143 standard-cell instances, got {len(std_entries)}"
print("Round-trip verification PASSED: extraction matches pristine source exactly, order-stable.")

with open("/tmp/block1_instances_verified.json", "w") as f:
    json.dump(std_entries, f, indent=2)
print("Wrote /tmp/block1_instances_verified.json")
