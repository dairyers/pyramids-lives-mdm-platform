# Kickoff prompts for Claude Code

Run these as three separate sessions (start a fresh one for each), reviewing results between them.
`CLAUDE.md` is loaded automatically. These prompts replace the chat-oriented parts of
`ai_prompt_sequence_v3.txt`: Claude Code writes files to disk and reads the repo itself, so ignore
"output ONLY fenced code blocks" and "paste the current version of file X".

## Before you start (once)
```
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
git init && git add -A && git commit -m "Scaffold: spec v3, config, tools"
python tools/check_config.py        # expect: no defects
python tools/spec_selfcheck.py      # expect: FAILS: 0  (this is the real-rapidfuzz run of the name table)
```
If `spec_selfcheck.py` reports a FAIL on a name pair, tell Claude Code before building: the scores
were only tested on a stand-in library, so a threshold or weight in `config_rules.json` may need tuning.

## Session 1: data and engines (Step 0 to Prompt 4B)
```
Read CLAUDE.md, action_items_execution_game_plan_v3.txt (the spec) and
ai_prompt_sequence_v3.txt (the build order). Implement Step 0 and then Prompts 2, 3, 4 and 4B
in order (Prompt 1, the config lint, is already done by tools/check_config.py). Write every file
to disk. After each prompt run its VERIFY command, fix failures at the root cause (never edit a
test unless it contradicts the spec, and say so if it does), then git commit. Re-read the relevant
spec section before each prompt. Stop after Prompt 4B and report: pytest results, the dedup
benchmark table with precision and recall, and anything you changed from the spec's reference code.
```

## Session 2: M365, analytics, UI (Prompts 5, 6, 7A to 7D)
Start only when session 1 is green.
```
Read CLAUDE.md and the spec. The engines from session 1 exist and are tested. Implement Prompts
5 and 6, then 7A, 7B, 7C and 7D in order, reading the existing engine files from the repo rather
than rewriting them. After each prompt run its VERIFY command and git commit. For the UI prompts
that say "click through", run the app headlessly where you can and list exactly what I must
check by hand. Stop after 7D with a summary.
```

## Session 3: end-to-end tests, deployment, deliverables (Prompts 8, 9, 10)
```
Read CLAUDE.md and the spec. Implement Prompts 8 and 9. Run the full suite with pytest -q and
fix root causes. Then write Prompt 10's video script and executive summary from the real test
output and the app's actual behaviour; do not claim anything the tests or app do not show
(no production SSO, no real SAP connectivity).
```

## Bug-fix template (Prompt B)
```
The spec is the source of truth. This command fails: <command>. Full output: <paste>.
Fix the root cause, keep Section 6 names and signatures, do not change tests unless they
contradict the spec, and change config_rules.json rather than hardcoding any business value.
```

## Schedule guard (today is Fri 23 Oct; deadline Wed 28 Oct 12:00 SGT)
Fri: session 1. Sat: session 2 up to 7C. Sun: 7D, M365/SLA, decide the CUT LINE in spec Section 7.
Mon: session 3 and deploy. Tue: record the video, executive summary. Wed morning: smoke test and submit.
