import pya
import json
import sys

path = "/home/defyscience/asu_eval/testcase/asap7/block/layout_script/Block1.py"
ns = {"pya": pya}
exec(compile(open(path).read(), path, "exec"), ns)
layout = ns["layout"]

top = None
for c in layout.each_cell():
    if c.name == "Block1":
        top = c
        break
assert top is not None

l2n = pya.LayoutToNetlist(pya.RecursiveShapeIterator(layout, top, []))

# Layer stack (verified against testcase/asap7/asap7.lyp):
# LISD=17, V0=18, M1=19, V1=21, M2=20, V2=25, M3=30, V3=35, M4=40, V4=45, M5=50, M6=60
LAYERS = {
    "LISD": 17, "V0": 18, "M1": 19, "V1": 21, "M2": 20, "V2": 25,
    "M3": 30, "V3": 35, "M4": 40, "V4": 45, "M5": 50, "M6": 60,
}
regions = {}
for name, num in LAYERS.items():
    regions[name] = l2n.make_layer(layout.layer(pya.LayerInfo(num, 0)), name)

# Connectivity: same-layer shapes touching are connected; each via layer
# connects the two metal/LISD layers it sits between.
for name in ["LISD", "M1", "M2", "M3", "M4", "M5", "M6"]:
    l2n.connect(regions[name])
l2n.connect(regions["LISD"], regions["V0"])
l2n.connect(regions["V0"], regions["M1"])
l2n.connect(regions["M1"], regions["V1"])
l2n.connect(regions["V1"], regions["M2"])
l2n.connect(regions["M2"], regions["V2"])
l2n.connect(regions["V2"], regions["M3"])
l2n.connect(regions["M3"], regions["V3"])
l2n.connect(regions["V3"], regions["M4"])
l2n.connect(regions["M4"], regions["V4"])
l2n.connect(regions["V4"], regions["M5"])

l2n.extract_netlist()

netlist = l2n.netlist()
print(f"Top circuits: {netlist.top_circuit_count()}", file=sys.stderr)
print(f"Total circuits: {[c.name for c in netlist.each_circuit()][:20]}", file=sys.stderr)
circuit = netlist.circuit_by_name("Block1")
print(f"Circuit name: {circuit.name if circuit else None}", file=sys.stderr)

nets_out = []
for net in circuit.each_net():
    subckt_pins = []
    for scp in net.each_subcircuit_pin():
        sc = scp.subcircuit()
        pin = scp.pin()
        trans = sc.trans
        subckt_pins.append({
            "subcircuit_id": sc.id(),
            "subcircuit_cell": sc.circuit_ref().name,
            "pin_name": pin.name(),
            "x": trans.disp.x,
            "y": trans.disp.y,
        })
    pin_names = [p.name() for p in net.each_pin()]
    nets_out.append({
        "net_id": net.cluster_id,
        "name": net.name,
        "expanded_name": net.expanded_name(),
        "top_level_pins": pin_names,
        "subcircuit_pins": subckt_pins,
    })

print(f"Extracted {len(nets_out)} nets", file=sys.stderr)
for n in nets_out[:20]:
    print(" ", n, file=sys.stderr)

with open("/tmp/block1_netlist_summary.json", "w") as f:
    json.dump(nets_out, f, indent=2)
