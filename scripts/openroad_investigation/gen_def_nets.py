import json

resolved = json.load(open("/tmp/resolved_nets2.json"))
instances = json.load(open("/tmp/block1_instances_regex.json"))

MAX_REASONABLE_MEMBERS = 8  # real gate-level signal nets are small; larger = rail-like artifact

signal_nets = []
excluded_large = []
for n in resolved:
    if len(n["members"]) < 2:
        continue
    if len(n["members"]) > MAX_REASONABLE_MEMBERS:
        excluded_large.append(n["net_id"])
        continue
    # de-dup (inst_idx, pin) pairs (same instance/pin can appear more than once
    # if multiple vias in the same net anchor to it)
    seen = set()
    members = []
    for m in n["members"]:
        key = (m["inst_idx"], m["pin"])
        if key in seen:
            continue
        seen.add(key)
        members.append(m)
    if len(members) >= 2:
        signal_nets.append({"net_id": n["net_id"], "members": members})

print(f"Excluded {len(excluded_large)} rail-like net(s) (>{MAX_REASONABLE_MEMBERS} members): {excluded_large}")
print(f"Clean signal nets for DEF NETS: {len(signal_nets)}")

lines = []
lines.append(f"NETS {len(signal_nets)} ;")
for n in signal_nets:
    inst_pin_strs = " ".join(f"( inst_{m['inst_idx']}_{instances[m['inst_idx']]['cell_type']} {m['pin']} )" for m in n["members"])
    lines.append(f"- net_{n['net_id']} {inst_pin_strs} ;")
lines.append("END NETS")

with open("/tmp/def_nets_section.txt", "w") as f:
    f.write("\n".join(lines) + "\n")
print(f"Wrote /tmp/def_nets_section.txt")
for l in lines[:6]:
    print(" ", l)
