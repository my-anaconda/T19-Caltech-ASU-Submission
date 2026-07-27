#!/bin/bash
set -e
cd /home/defyscience/asu_eval

# Test each Tier-1 candidate in isolation by patching a copy of the
# m4s5-only baseline script directly (bypassing the model call), to find
# exactly which edit(s) cause the regression.
python3 - <<'EOF'
import sys
sys.path.insert(0, "agent_isolate_tmp")
import importlib.util
spec = importlib.util.spec_from_file_location("agent_test", "agent_m4s5_test.py")
agent_test = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_test)

import json
base = open("result/t19-m4s5-allblocks/block/repair/Block1/Block1_repaired.py").read()
drc = json.loads(open("testcase/asap7/block/drc_report/Block1.drc.json").read())

cases = [
    ("M2.S.7_only", "M2.S.7"),
    ("M3.S.2_only", "M3.S.2"),
]

for label, rule_name in cases:
    text = base
    rule = drc["rules"].get(rule_name)
    for i, v in enumerate(rule.get("violations", [])):
        edges = agent_test._violation_to_edges(v)
        if edges is None:
            continue
        cfg = agent_test.SPACING_RULES[rule_name]
        cands = agent_test.find_spacing_increase_candidates(text, cfg["layer"], edges, cfg["required_gap_raw"])
        safe = [c for c in cands if c["obstacle_free"]]
        if not safe:
            continue
        # deterministically pick the first candidate (no model call - isolating geometry only)
        c = safe[0]
        text = agent_test._apply_spacing_candidate(text, c)
        print(f"{label}: applied {rule_name}[{i}] var={c['var']} action={c['action']} shift={c['shift_nm']}nm")
    with open(f"/tmp/isolate_{label}.py", "w") as f:
        f.write(text)
    print(f"Wrote /tmp/isolate_{label}.py")
EOF
