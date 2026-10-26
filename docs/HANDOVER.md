# Handover brief: what changed since the first zip

For: the teammate reviewing the prototype, or an AI agent working for them. Written 25 Oct 2026 for the Wed 28 Oct 2026
12:00 SGT submission (SMU DSA Society 2026 Data Case Competition).

## Read this first

**The new features have not been tested by a person.** The automated test suite passes (1,308 tests after the later Sales insights work, section 9; run on the author's
machine) and the main paths were driven through the real screens by a script. Nobody has used the new screens by hand. Only the
Help button was ever glanced at in a browser (one question typed, one "Take me there" pressed); the rest was never viewed. Treat everything in section 3 as "built and machine-checked, not reviewed".
Expect rough edges in layout and wording.

**The backups are still there on purpose**, in case something is broken. They sit next to the project folder, not inside it
(section 2). Do not delete them until the review is done.

## 1. What "the initial iteration" means

The initial iteration is the project as zipped and sent for review. It is identical to the backup folder
`lives-mdm-platform_BACKUP_before_help_guide` (taken straight after the zip was made). If your copy differs from that
folder, compare yours with it first, because everything below is described relative to it.

What the first zip already contained (not new): the SAP Fiori-style look, the stories, seeded demo data, the duplicate
check with side-by-side comparison, notes banners, the Sales insights page, the six forecast models with the new
"Own season + regional growth" model (5.6 percent error against 10.4 and 13.1), the Platform settings editors, the no-code
workflow designer, the 2-page summary and the first video script.

## 2. The backups (oldest to newest, all in the folder that holds `lives-mdm-platform`)

| Folder | State it holds | Restore to it when |
|---|---|---|
| `lives-mdm-platform_BACKUP_before_help_guide` | The first zip. No Help guide, nothing below | Everything new is unwanted |
| `lives-mdm-platform_BACKUP_before_help_editor` | First zip + the Help guide (no editor, no tabs on Platform settings) | The Platform settings change or anything after it is broken |
| `lives-mdm-platform_BACKUP_before_intake_and_audit` | + Help editor, tabs, word-form normalisation | The question card or audit chain is broken |
| `lives-mdm-platform_BACKUP_before_new_approver_role` | + question card, audit hash chain | The new approver feature is broken |
| `lives-mdm-platform_BACKUP_before_domain_builder` | + new approver | The new request type (domain builder) is broken |

Each folder holds source only (no `.venv`, caches or `.git`). To restore on Windows (PowerShell), replacing the project
completely and deleting files that did not exist then:

```powershell
robocopy <backup folder> lives-mdm-platform /MIR /XD .venv __pycache__ .pytest_cache .git
```

Then run `pytest -q` and `python tools/check_config.py`. On macOS or Linux, delete the project's source and copy the
backup in its place (`rsync -a --delete --exclude .venv --exclude .git <backup>/ lives-mdm-platform/`). There is no git
history, so a restore cannot be undone: copy the current folder somewhere first.

The three older backups (before the forecast upgrade, the simplification and the workflow designer) were deleted earlier
at the author's request. The "REVERSE COURSE" restore point for the workflow designer therefore no longer exists.

## 3. What was added or changed (newest features last)

Everything is on the author's machine only; none of it is deployed.

| # | Feature | Where to find it | Main files |
|---|---|---|---|
| 1 | **Help guide**: a level-1 "how do I / where is" lookup. No AI, no network. BM25 ranking over 36 written topics, typo repair, "Take me there" buttons, tells you which roles can open a page your role cannot | **Help** button in the header, next to Role and Case Synopsis stories | `engine_help.py`, `tabs/help_guide.py`, `data/help_topics.json`, `app.py`, `tabs/ui.py` (header width) |
| 2 | **Help guide editor**: the IT admin edits, adds and removes topics, and edits a similar-words list, with a "try a question" box. Checked, audited, undoable | Platform settings, **Help guide** tab (log in as IT / System Administrator) | `tabs/help_editor.py`, `engine_help.py`, `tabs/common.py` (`base_config` now carries the help data under `"help"`) |
| 3 | **Platform settings split into tabs**: Approvals, Request types, Reminders and duplicates, People and access, Help guide, Changes. Layout only; the old controls behave as before | Platform settings | `tabs/tab_admin.py` |
| 4 | **Word-form normalisation** for the Help guide: approve, approving, approver and approval count as one word; listed similar words also catch their other forms | Help guide search | `engine_help.py`, `data/help_topics.json` (`suffixes`, `synonyms`, `stopwords`) |
| 5 | **"Not a new record?" link** on New request: a link under the four cards sends chasers and questions to Requests, Home or the Help guide so they never become requests | New request (log in as Requestor) | `tabs/tab_intake.py`, `data/ui_text.json` (`intake_other`), `tabs/help_guide.py` (`show_answers`) |
| 6 | **Tamper-evident audit trail**: each audit entry carries a hash of itself and the previous entry. A banner says "Record intact" or names the first broken entry, and a button shows the check catching a change on a copy | Audit trail | `engine_mdm.py` (`log_event`, `verify_audit_chain`), `tabs/tab_audit.py` |
| 7 | **New approver**: the IT admin adds an approval step (role, person, delegate) for a new business unit or team. It gets reminders, a queue, a digest entry and a role-switcher entry, then appears in the rule designer. Undo is refused while a request still waits at a step the earlier settings do not have | Platform settings, Approvals tab, "Add an approval step" | `engine_settings.py` (`add_approver_role`), `tabs/tab_admin.py`, `tabs/tab_home.py` (default welcome text), `data/ui_text.json` |
| 8 | **Starter domain builder (new request type)**: the IT admin names a type and starts from a blank form (code, name, business entity) or a copy of an existing one, then adds, renames or removes details, sets mandatory, gives a choice list, marks finance or bank details as private. It gets its own SAP table, a name-similarity duplicate check, codes like PRO00001, a card on New request, a column in People and access, and a place in the rule designer | Platform settings, Request types tab | `engine_settings.py` (`add_request_type`, `set_type_fields`), `tabs/tab_admin.py`, `tabs/common.py` (`domain_text`, `table_names`), `tabs/tab_staging.py` (code prefix), plus the pages that now read type names from `domain_text` (`tab_home`, `tab_inbox`, `tab_intake`, `workflow_designer`) |
| 9 | **Documents updated**: video script, 2-page summary (.docx, still 2 pages in Word on the author's machine), README, executive summary, answers to the brief's six questions, and this brief | `docs/` | `docs/*.md`, `docs/Lives_MDM_2page_Summary.docx` |

Not changed: `config_rules.json` on disk is byte-for-byte the same as in the first zip. The new settings (new roles,
types, approvers, help topics) live in the per-session copy and can be downloaded from Platform settings, Changes tab.

## 4. Behaviour that differs from the first zip (things a reviewer or test could trip on)

- **Audit entries have two new fields**, `prev_hash` and `hash`. One existing test (`test_log_event_duplicate_stores_ids_and_tier_only`)
  asserted the exact key set; it was changed to include them and to ignore the hashes when searching for leaked values.
- **`common.base_config()` now returns the settings file plus a `"help"` key.** Anything that compares the live settings
  with the file, or downloads the rules, will see the extra key.
- **A request type or approver made on the dashboard is session-only.** Reset demo or a new browser session removes it.
- **The 18-day SLA clock, the stories and the seeded data are unchanged.** The IT admin story (S7) still opens on the
  Workflow designer because Approvals is the first tab.
- **One Help topic ranking depends on every other topic.** Adding a topic can push another below the cutoff. This happened
  once; the fix was a phrase on the affected topic. `tests/test_help.py` catches it.

## 5. What has and has not been checked

Checked by machine (author's computer, Windows 11, Python 3.12 in a conda env):

- `pytest -q`: 1,308 passed (1,304 before the Sales insights work in section 9). New test files: `tests/test_help.py`, `tests/test_audit_chain.py`, `tests/test_new_approver.py`,
  `tests/test_new_type.py`.
- `python tools/check_config.py`: no defects.
- Scripted runs through the real screens (Streamlit's `AppTest`): the IT admin adds an approver, a request type and a Help
  topic; a requestor raises a request of the new type; the MDM Analyst approves it and creates it in SAP (code PRO00001); a
  second near-identical request is flagged; Undo works and is refused when it should be; every one of 18 users opened every
  one of 7 pages with no error.

**Not checked, please look at these first:**

1. How every new screen **looks**: the six Platform settings tabs, the Request types editor (table editors with add-row),
   the Help popover, the "Not a new record?" link on New request, the audit banner and button. A script cannot judge layout.
2. Whether the **selected tab stays put** after pressing Apply on Platform settings (it may jump back to the first tab).
3. The header at **narrow widths**: it now holds three buttons (Role, Case Synopsis stories, Help).
4. Typing in the editors by hand: adding a row in the Help topics, similar words and request-type details tables.
5. The video script's **NEW parts** (marked in the file) have not been rehearsed, and the whole script runs about 3:00,
   at the upper limit. The trim list at the end of the script gets it to about 2:25.
6. The 2-page summary was checked for page count in Word on the author's machine only; other renderers may paginate
   differently. Its YouTube, GitHub and deployment links are still placeholders.
7. Wording in the 36 Help topics: written by the author's assistant from knowledge of the app; nobody has proofread them.

## 6. Known limits (stated honestly in the documents too)

- SAP and Outlook are simulated previews on generated demo data; sign-in is a demo role switch.
- All state is in the browser session: requests, audit log, settings changes. A shared database is the first pilot blocker.
- The audit hash chain detects edits to the log; it cannot stop someone who rewrites the whole log (the newest fingerprint
  would need storing elsewhere). Dropping only the newest entry is not detectable on its own.
- A new request type checks duplicates by name similarity only. The extra evidence checks customers and vendors have are not
  set up for new types. Private-data classes beyond the three that exist, and a new approver's access to sensitive fields,
  need the settings file.
- The forecast result (5.6 percent) is on generated data that has a yearly curve and steady growth built into each series,
  which the model fits. It shows the method works, not that real demand behaves the same.
- Still to do by the team: record the video, add the YouTube and GitHub links to the 2-page summary, deploy (optional).

## 7. How to run and check (about 5 minutes)

```bash
pip install -r requirements.txt     # Python 3.11 or newer
pytest -q                           # expect 1,308 passed
python tools/check_config.py        # expect: config_rules.json: no defects
streamlit run app.py
```

A 10-minute walk-through that covers every new feature:

1. As **Requestor**: open **New request**, click the "Not a new record?" link under the cards, type "where is my request".
2. Click **Help** (header), try "where do i download the numbers" (the answer says which roles can open it), then press
   **Take me there** on a topic your role can open.
3. Switch **Role** to **IT / System Administrator**, open **Platform settings**. Click through all six tabs.
4. **Approvals**: add an approval step (name "Regional Compliance Officer", person "Aiko Tan"). In the Workflow designer add an
   "also require" rule that uses it. Switch **Role** to the Approver list and check the new step appears.
5. **Request types**: add "Project code", edit its details (add a Choice detail), Apply. As **Requestor** raise one, then as
   **MDM Analyst** approve it and, on Requests, open the **SAP records** view and create it (expect code PRO00001). Raise a
   second named "Alpha Roll-out" style near-duplicate and confirm it is flagged.
6. **Help guide** tab: change an answer, add a topic and a similar word, use the "Try a question" box, Apply, then ask the
   Help button the new question.
7. **Changes** tab: press **Undo the last change** repeatedly and check it refuses when a request still uses the thing.
8. As **MDM Analyst** or **Auditor**, open **Audit trail**: check the "Record intact" banner and press the "Change one entry in
   a copy" button inside the expander.

If any step fails, note which, and decide whether to fix it or restore the matching backup (section 2).

## 8. For an AI agent: how this codebase works

Read `CLAUDE.md` and `CONTRACTS.md` first. The rules that matter:

- **No hardcoded business logic.** Thresholds, routes, roles, field security, reminder days and analytics parameters live in
  `config_rules.json`. The dashboard edits a per-session copy (`tabs/common.live_config`); `tabs/tab_admin._commit` applies a
  change to a deep copy, validates it, logs `SETTING_CHANGED`, pushes the old copy onto `config_history` for Undo, and reruns.
  Any new setting function belongs in `engine_settings.py` and must raise `ValueError` before mutating anything.
- **Security.** Every record value shown, exported, emailed or logged goes through `engine_security.apply_field_security()`.
  Never call `st.dataframe()` or `st.json()` on a raw record.
- **Engines have no Streamlit.** `engine_*.py` and `m365_integration.py` are plain Python. Pages live in `tabs/`.
- **Do not change a test to make it pass** unless it contradicts the stated behaviour; say so when you do.
- **Help content is data**: `data/help_topics.json`. To add an answer, add a topic and a row to `QUESTIONS` in
  `tests/test_help.py`, then run that file; the ranking is relative, so another topic can slip.
- **No git.** Snapshot the folder (see the backups) before any larger change.
- The Streamlit app runs on port 8503 on the author's machine; cached loaders (`st.cache_resource`) mean settings-file or
  help-file edits need a server restart to show.

Commands: `pytest -q`, `python tools/check_config.py`, `python tools/spec_selfcheck.py`, `streamlit run app.py`.


## 9. Later session: Sales insights overhaul and request-page clean-up (26 Oct 2026)

A full backup of the project before this work is next to the project folder: `lives-mdm-platform_BACKUP_before_dashboard_overhaul`
(source only, no `.venv`). Keep it until the review is done. Restore as in section 2.

| What | Where | Notes |
|---|---|---|
| New look for Sales insights Overview: filter bar, KPI cards with sparklines, map, heatmap, gained-or-lost chart, Compare chart, Customise page | `tabs/tab_analytics.py`, new `tabs/charts.py` (chart theme and cards), card CSS in `tabs/ui.py` | Presentation only. Figures are aggregates; no customer rows are shown |
| **Measure** (units, sales value, gross margin) and **Customers** (all, existing, new) | `tabs/tab_analytics.py` (`_basis`, `_filters`) | New-customer growth is "% of last year's volume added by new customers" |
| New vs existing customers | `engine_analytics.py`: `account_status`, `summarize_new_vs_existing` | Same definition as the regional view; a test checks it adds up to the regional totals in units and USD |
| Southeast Asia map with a heat dot on each capital; one country opens into cities | `tabs/tab_analytics.py` (`map_rows`, `map_figure`), `data/map_points.json` | **The city split is invented demo data** (the order file has no city field). The map downloads its outlines from the internet |
| Rolling backtest ("Does the accuracy hold up?") | `engine_analytics.py`: `backtest_rolling`; Forecast explorer | 5.4, 5.5, 5.6 percent on three windows against 10.4 to 12.9 for the better traditional average |
| Error band on the forecast chart | `tabs/tab_analytics.py` (`forecast_figure`) | Half-width is the model's measured test error, not an invented interval |
| Order-pattern classes (scheduled or ad hoc) | `engine_analytics.py`: `classify_order_patterns`, `summarize_order_patterns` | **Engine and tests only, not on the dashboard**: all 103 pairs are scheduled in the generated data |
| "Which orders count" moved from the Overview to Forecast explorer, Advanced; a warning shows above every tab when it is changed | `tabs/tab_analytics.py` (`_data_in_use`, `_data_warning`) | The checkbox column is now called Counted |
| New request: four cards plus a "Not a new record?" link, instead of a fifth card | `tabs/tab_intake.py` | Same panel and copy (`data/ui_text.json`, `intake_other`) |
| Model picker no longer says "(most accurate in the test)" | `tabs/tab_analytics.py` | The default model is still the most accurate one |

**Config additions** (`config_rules.json`, under `analytics`): `rolling_backtest` (windows and step), `order_pattern` (cutoffs and labels)
and `margin`. **`margin.by_product` (A 22, B 38, C 45 percent) is an assumption**, not Lives Group data; it carries a note and is shown
on the page when Gross margin is selected. `config_rules.json` is therefore no longer byte-identical to the first zip. The checker reports no defects.

**Tests:** 1,308 pass (4 new in `tests/test_analytics.py`). No existing test was changed.

**Checked by machine:** the Overview and Forecast explorer render without error for MDM Analyst, Supervisor and Auditor across every
combination of measure, customers, grouping, drawing style and block toggles, and with Advanced on. **Looked at in the browser
pane (MDM Analyst, 1280 px):** the Overview, the map in both modes, the Compare chart, the rolling backtest chart and New request.

**Not looked at:** the Customers to follow up tab after its tiles changed, any role other than MDM Analyst, narrow and phone widths,
and the final column widths of the filter bar. Cached settings need a server restart after editing `config_rules.json`.

**Not built:** per-user saved layouts (Customise page resets with the session), an intermittent-demand model such as Croston's
(nothing in the data to exercise it), and typo tolerance in the duplicate check.

**Environment:** the `.venv` in the project was built on a conda Python that is not on this machine; its pinned requirements were
installed again into it. Run with `.venv\Scripts\python.exe -m streamlit run app.py`.

**Documents updated:** README, FEATURES, executive summary, challenge answers, demo video script, this brief, and the 2-page summary
(`Lives_MDM_2page_Summary.docx`: rewritten compactly with no tables, 2 pages in Word here with room to spare; the previous version is not kept in the project). The video link, GitHub link and deployment link in the summary are still placeholders, and the video script has not been re-timed.

## 10. Later changes: hands-free demo and administrator controls (26 Oct 2026)

Built after the sections above. All machine-checked (1,338 tests) and many played through the real screens in a browser, but not
reviewed by a person. Expect wording and timing to need tuning.

| Change | Where |
|---|---|
| A start page ("Begin tour") above the app | `app.py` (`_splash`) |
| The manual Case Synopsis stories were removed from the menu and replaced by hands-free stories and role tours. Nothing is pre-filled; roles are switched with the role switch, always to the generic accounts | `app.py`, `tabs/autopilot.py` (script builder), `tabs/autopilot.js` (player), `data/autopilot.json` (steps, timing, explanations) |
| Player controls: progress bar, Back, fast-forward 1x/2x/3x, Pause, Stop | `tabs/autopilot.js` |
| Flags (how it works, security, checks). Security and role checks are answered by the real engine; values on screen are scrolled to, ringed and read back | `tabs/autopilot.py` (`policy_check`), `tabs/autopilot.js` |
| Details and the similar-name comparison are plain tables with a row per field, so a row can be pointed at | `tabs/ui.py` (`detail_table`, `compare_table`), `tabs/common.py`, `tabs/tab_inbox.py` (the Who can see what panel) |
| Sample requests are back for normal use (customer with credit terms and bank details, vendor with Finance); stories start empty | `data/demo_seed.json`, `app.py`, `tabs/autopilot.py` (`launch`) |
| The IT administrator decides who sees credit terms and bank details (hidden, masked, full) | `engine_settings.py` (`set_field_policy`), `tabs/tab_admin.py` (People and access), `tests/test_field_policy.py` |
| IT administrator tour creates a role and a rule on screen | `data/autopilot.json` (`R_IT`) |

Known limits:
- A hidden or background browser tab does not draw the top navigation, so a story may fail to open its first page. Keep the tab visible.
- Fast-forward cannot beat the time the app takes to redraw after each click.
- Back replays the explanation of an earlier step; it cannot undo data that step already changed.
- The Sales insights walkthrough after the map (custom graphs, forecast comparison) and the "Who can see what" and Outlook-card
  segments were written but not all replayed end to end.
- `tabs/stories.py`, the guide bar and the story text in `data/ui_text.json` are still in the folder; the hands-free script reads
  its wording from `data/ui_text.json`, so do not delete it.