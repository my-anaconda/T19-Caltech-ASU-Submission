import sys
import json
import importlib.util

spec = importlib.util.spec_from_file_location("agent_test", "/home/defyscience/asu_eval/agent_m4s5_test.py")
agent_test = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_test)

base = open("/home/defyscience/asu_eval/result/t19-m4s5-allblocks/block/repair/Block1/Block1_repaired.py").read()
drc = json.loads(open("/home/defyscience/asu_eval/testcase/asap7/block/drc_report/Block1.drc.json").read())

# Force the SAME choice the model made in the real run: M2.S.7 -> p1301 (shrink_bottom_edge)
rule = drc["rules"]["M2.S.7"]
edges = agent_test._violation_to_edges(rule["violations"][0])
cfg = agent_test.SPACING_RULES["M2.S.7"]
cands = agent_test.find_spacing_increase_candidates(base, cfg["layer"], edges, cfg["required_gap_raw"])
p1301_cand = next(c for c in cands if c["var"] == "p1301")
text_p1301 = agent_test._apply_spacing_candidate(base, p1301_cand)
open("/tmp/isolate_p1301_only.py", "w").write(text_p1301)
print("p1301 candidate:", p1301_cand)

p1297_cand = next(c for c in cands if c["var"] == "p1297")
text_p1297 = agent_test._apply_spacing_candidate(base, p1297_cand)
open("/tmp/isolate_p1297_only.py", "w").write(text_p1297)
print("p1297 candidate:", p1297_cand)

# M3.S.2 both instances (as the model chose: p1543, p1458, both shrink_right_edge)
rule2 = drc["rules"]["M3.S.2"]
text_m3s2 = base
for v in rule2["violations"]:
    edges2 = agent_test._violation_to_edges(v)
    cfg2 = agent_test.SPACING_RULES["M3.S.2"]
    cands2 = agent_test.find_spacing_increase_candidates(text_m3s2, cfg2["layer"], edges2, cfg2["required_gap_raw"])
    right_cand = next((c for c in cands2 if "right" in c["action"]), None)
    if right_cand:
        print("M3.S.2 candidate:", right_cand)
        text_m3s2 = agent_test._apply_spacing_candidate(text_m3s2, right_cand)
open("/tmp/isolate_m3s2_only.py", "w").write(text_m3s2)
print("Wrote all isolation files")
