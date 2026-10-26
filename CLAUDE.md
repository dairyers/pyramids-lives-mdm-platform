# Lives Group MDM & Analytics Platform (Streamlit prototype)

Deadline: Wed 28 Oct 2026, 12:00 PM SGT. A rough but complete prototype beats a perfect half.

## Source of truth
- `action_items_execution_game_plan_v3.txt` is the spec. Section 6 (Interface Contracts)
  gives the exact function names, signatures and data shapes. Do not rename anything.
- `ai_prompt_sequence_v3.txt` is the build order (Step 0, Prompts 1 to 10, Prompt B bug-fix template).
- `config_rules.json` is copied verbatim from spec Section 1.1. Do not retype or regenerate it.
  Change business behaviour by editing this file, not code.

## Rules (from the prompt file, Phase 1)
1. Complete, runnable code. No TODOs, no "implementation here" placeholders.
2. NO HARDCODED BUSINESS LOGIC. Regexes, thresholds, alias dictionaries, routing rules, SLA hours,
   role permissions, field security policies, evidence signals and analytics parameters are read
   from config_rules.json. Functions contain no business literals.
3. SECURITY: every record value shown in the UI, exported, put in an Adaptive Card or digest, or
   written to the audit log passes through `engine_security.apply_field_security()`. Never call
   `st.json()` or `st.dataframe()` on a raw record or raw DataFrame. Cached functions must not
   contain role-specific views in their cache keys.
4. Follow the spec algorithms exactly: abbreviation resolver pipeline, composite score
   0.6*TokenSet + 0.4*JaroWinkler with discriminator penalty and core-conflict cap, the evidence
   quorum (2+ distinct field groups, at least one non-weak), structured routing (no eval),
   config-driven SLA, a choice of forecast models that each work from 6 to 12 months of history (see CONTRACTS.md).
5. Dependencies: streamlit, pandas, numpy, plotly, rapidfuzz, pytest. Say why before adding any.
6. Duplicate-evidence values (contact person, phone, address, postal code, email) are shown only to
   roles in `detail_visible_roles` (the Requestor, MDM Analyst and Auditor: a requester cannot judge "same
   organisation?" without seeing the other party's details). Audit entries store signal IDs and the tier, never those values.

## Working method
- Work one prompt at a time, in order. After each: save files to disk, run its VERIFY command,
  fix failures at the root cause, then `git commit`.
- Never change a test to make it pass unless the test contradicts the spec; say so if it does.
  If a name-score or evidence-tier test fails, adjust config values before touching code.
- Do not regenerate whole files for a small fix. Edit the function.
- Before each step, re-read the relevant spec section instead of relying on memory.
- Run `python tools/check_config.py` after any change to config_rules.json.

## Commands
- Config check:   `python tools/check_config.py`
- Spec self-check (name table + evidence table): `python tools/spec_selfcheck.py`
- Tests:          `pytest -q`
- App:            `streamlit run app.py`
- Mock data:      `python data/generate_mock_data.py`
