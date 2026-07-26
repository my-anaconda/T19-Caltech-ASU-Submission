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

LAYERS = {
    "LISD": 17, "V0": 18, "M1": 19, "V1": 21, "M2": 20, "V2": 25,
    "M3": 30, "V3": 35, "M4": 40, "V4": 45, "M5": 50, "M6": 60,
}
regions = {}
for name, num in LAYERS.items():
    regions[name] = l2n.make_layer(layout.layer(pya.LayerInfo(num, 0)), name)

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
circuit = netlist.circuit_by_name("Block1")

# Layers we might need to strip/replace for a "reroute this net's M2+" pass.
# M1/V0 excluded - frozen seed. V5/M6 kept for completeness even though this
# design barely uses them.
STRIP_LAYERS = {"M2": 20, "V1": 21, "M3": 30, "V2": 25, "M4": 40, "V3": 35,
                 "M5": 50, "V4": 45, "M6": 60}

nets_out = []
for net in circuit.each_net():
    shapes_by_layer = {}
    for lname, lnum in STRIP_LAYERS.items():
        region = l2n.shapes_of_net(net, regions[lname], True)
        boxes = []
        for poly in region.each():
            bb = poly.bbox()
            boxes.append([bb.left, bb.bottom, bb.right, bb.top])
        if boxes:
            shapes_by_layer[lname] = boxes
    if not shapes_by_layer:
        continue
    nets_out.append({
        "net_id": net.cluster_id,
        "shapes_by_layer": shapes_by_layer,
    })

print(f"Nets with M2+ shapes: {len(nets_out)}", file=sys.stderr)
with open("/tmp/block1_net_shapes.json", "w") as f:
    json.dump(nets_out, f)
print("Wrote /tmp/block1_net_shapes.json", file=sys.stderr)
