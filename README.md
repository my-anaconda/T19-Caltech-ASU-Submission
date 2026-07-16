# T19 (Caltech) — ASU ICLAD 2026 Submission

Agent for the ASU block-repair benchmark (`ICLAD26-ASU-Problems`). See
`NOTES.md` for the full design rationale, empirical comparison against T19's
10 prior agent iterations, and the concrete plan for the real repair engine
that follows this submission.

**Current status: a verified safe floor, not yet a repair engine.** `agent.py`
writes the original layout script back unchanged. This guarantees eligibility
(valid KLayout evaluation + trivially preserved connectivity). Verified
locally: `final_violation_rate = 1.2909...`, `repair_rate = 0.0` - note this
is **not** 1.0; a real, unexpected discrepancy between the static given DRC
report and a fresh live KLayout re-evaluation of the *exact same unmodified
script* (traced to 3 specific grid-alignment rules - see `NOTES.md`) means
even zero edits doesn't score exactly at parity. This result **matches**
T19's best prior attempt (v2, which turns out to have been an accidental
CRLF-normalizing no-op landing on the same floor) and **beats** the other 8
scored attempts (several scored far worse; one was disqualified for breaking
connectivity - see `NOTES.md` for the full table and the investigation behind
this number). The real repair logic requires hierarchy-aware coordinate-
transform parsing through nested standard-cell instances; that work is
scoped in `NOTES.md` but not implemented here yet.

## Layout

```
T19-Caltech-ASU-Submission/
├── agent.py                 ← the submission agent (this is what gets submitted)
├── scripts/
│   └── model_service.py     ← local Vertex AI Express Mode proxy, dev/test only
├── NOTES.md                 ← design rationale, prior-version comparison, next steps
└── README.md                ← this file
```

`agent.py` has zero external dependencies (stdlib only), so no
`requirements.txt` is included - `scripts/model_service.py` is a development
helper (not part of the submission) and needs `google-genai`, installed
separately if you want to run it.

## Prerequisites

0. Clone this repo, and clone the official problem repo (it provides
   `scripts/run_block_benchmark.py`, `evaluator/`, and the block testcases -
   none of that is duplicated here):
   ```bash
   git clone https://github.com/my-anaconda/T19-Caltech-ASU-Submission.git
   git clone https://github.com/ICLAD-Hackathon/ICLAD26-ASU-Problems.git
   ```
   (`ICLAD26-ASU-Problems` is also reachable as a submodule of the top-level
   `ICLAD-Hackathon-2026` repo, at `problem-categories/ICLAD26-ASU-Problems`.)
1. Python 3.10+, and KLayout **0.30.1 exactly** (evaluation checks the exact
   version and fails otherwise - see the official repo's `DEPENDENCIES.md`).
2. To run the local test model service: `pip install google-genai` and:
   ```bash
   echo 'EXPRESS_MODE_KEY=your_actual_key_here' > .env
   ```

## Running

```bash
cd ICLAD26-ASU-Problems

# Prepare + inspect a case without calling an agent:
python3 scripts/run_block_benchmark.py --case Block1 --prepare-only

# Run this agent through the official runner (uses the official Vertex AI
# Express Mode model service by default - set EXPRESS_MODE_KEY per the
# official repo's DEPENDENCIES.md):
python3 scripts/run_block_benchmark.py \
    --case Block1 \
    --agent-path /path/to/T19-Caltech-ASU-Submission/agent.py \
    --run-id t19-safe-floor
```

To test against a local model service instead of the official one:

```bash
# Terminal 1, from T19-Caltech-ASU-Submission/:
python3 scripts/model_service.py --port 9000

# Terminal 2, from ICLAD26-ASU-Problems/:
python3 scripts/run_block_benchmark.py \
    --case Block1 \
    --agent-path /path/to/T19-Caltech-ASU-Submission/agent.py \
    --run-id t19-safe-floor \
    --upstream-endpoint http://127.0.0.1:9000
```

## Evaluating

```bash
python3 evaluator/evaluate_repair.py --case Block1 --run-id t19-safe-floor
cat factors/t19-safe-floor/block/repair/Block1_factors.json
```

Expect `valid_repair: true`, `connectivity_preserved: true`,
`final_violation_rate: 1.2909...`, `repair_rate: 0.0` - confirmed by direct
local testing (KLayout 0.30.1 via WSL) during development. See `NOTES.md` for
why this isn't exactly 1.0 and how that was verified (not assumed).
