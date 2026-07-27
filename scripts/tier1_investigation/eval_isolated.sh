#!/bin/bash
set -e
cd /home/defyscience/asu_eval
for case in p1301_only p1297_only m3s2_only; do
  RUN_ID="t19-isolate-$case"
  mkdir -p "result/$RUN_ID/block/repair/Block1"
  cp "/tmp/isolate_${case}.py" "result/$RUN_ID/block/repair/Block1/Block1_repaired.py"
  python3 evaluator/evaluate_repair.py --case Block1 --run-id "$RUN_ID" > /dev/null 2>&1
  python3 -c "
import json
d = json.load(open('factors/$RUN_ID/block/repair/Block1_factors.json'))
d2 = json.load(open('temp/eval/$RUN_ID/block/repair/Block1/Block1.drc.json'))
print('=== $case ===')
print('  connectivity_preserved:', d['connectivity_preserved'], 'final_violations:', d['final_violations'])
for name, r in sorted(d2['rules'].items()):
    marker = ' <-- NEW/CHANGED' if name in ('M2.S.2','M2.W.1','V2.AUX.1','V2.M3.EN.2') or (name=='V2.M3.AUX.2' and r['violation_count']>0) else ''
    print(' ', name, r['violation_count'], marker)
"
done
