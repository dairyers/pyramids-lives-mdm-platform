# Lives Group MDM & Analytics Platform (prototype)

**Live demo:** https://lives-mdm-platform-m8elmfzg9kjup2dqsnuwz7.streamlit.app/

A Streamlit prototype of two engines driven by one rules file, `config_rules.json`:

1. **MDM engine**: a config-driven intake gate for SAP Business One master data (customers / vendors
   `OCRD`, items `OITM`, cost centers `OOCR`). It validates each request, catches abbreviated or
   misspelled duplicates (for example "NUHS" or "Nat. Uni. Hospital Singapore" against *National
   University Hospital Singapore*), and backs each name match with an evidence quorum built from
   other fields. It routes the request to the right approver and escalates on an SLA clock.
2. **Analytics engine**: cleans noisy PO history, forecasts demand per country and product line with
   a choice of models that each work from 6 to 12 months of history, repeats the accuracy test on several earlier windows,
   splits growth between new and existing customers, and adds a churn radar and cross-sell suggestions for five countries.

There is one app, one dataset and seven generic roles. It is built to look like the real product: a top
navigation of plainly named pages, with two small demo controls in the header (a role switch and the Case Synopsis stories)
and a **Help** guide beside them. Help is a level-1 lookup with no AI and no network: it ranks 36 written topics
(`data/help_topics.json`) with BM25 (`engine_help.py`), treats different forms of a word as one (approve, approving, approver),
uses a list of similar words, repairs small typos, tells you which roles can open a page your role cannot, and offers a
Take me there button. The IT / System Administrator edits, adds and removes topics and similar words in Platform settings,
Help guide tab (checked, audited, undoable). It only answers what is written down; `tests/test_help.py` checks 48 direct
questions and 41 reworded ones.
What you see (pages, rows and field values) depends on who you are. All of these go through one function, `engine_security.apply_field_security()`.
A full bank account number is shown only to the Payments Officer, only after Finance has approved the
request, and never by email or in an Adaptive Card.

The business rules (regexes, thresholds, alias dictionaries, routing, SLA hours, role permissions,
field security, evidence signals and analytics parameters) live in `config_rules.json`, not in code.
The spec is `action_items_execution_game_plan_v3.txt`, and `CONTRACTS.md` lists the function contracts.

| Link | |
|---|---|
| Live app | _<Streamlit Community Cloud URL>_ |
| Demo video (about 2:30) | _<YouTube URL>_ |
| Executive summary (PDF) | _<link>_ |
| Repository | _<GitHub URL>_ |

## Install

Python 3.11 or newer is required (pandas 3.x). The test run below used Python 3.13.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
python data/generate_mock_data.py  # optional: the generated files are committed; seeded, so output is identical
streamlit run app.py
```

Open the URL Streamlit prints (default http://localhost:8501). You first see a **start page** with the prototype's name and a
**Begin tour** button; it sits above the app like a login page. After it you land on **Home** as a Requestor. Use **Role** in
the header to switch between the seven roles, or **Case Synopsis stories** to play a story **hands-free** (see below), or to
**Reset demo**. A normal session starts with sample requests at different stages (including a customer with credit terms
and bank details, so redaction has something to show), so no page opens empty. A hands-free story starts from nothing.

| Page | Who sees it | What it is for |
|---|---|---|
| Home | everyone | Colour-coded counts (click Show to open that list), a **Needs you** list with a Review button on each row, or a clear next step |
| New request | requestors only | Pick the type on a card (a link under the cards, **Not a new record?**, sends chasers and questions to Requests, Home or the Help guide instead of becoming a request), fill the required details (finance details fold away until needed), then **Check my request**: it shows how the name was read (editable), duplicate matches and similar records reconciled automatically, and asks one plain question ("Is this the same as ...?") with the recommended answer highlighted. Nothing is sent until the requester presses **Send request** |
| Requests | by role (own, queue, release or all requests) | Each request with a progress tracker, details, history, and approve or reject, plus the Outlook email the approver received (an email mockup with a Technical JSON tab). Roles that handle SAP also get a **SAP records** tab here: approved requests to create in SAP, and what SAP already holds |
| Sales insights | roles with analytics access | Three tabs. **Overview**: a filter bar (country, product, **Measure**: units, sales value or gross margin; **Customers**: all, existing or new) drives headline cards with sparklines, a Southeast Asia map (a heat dot on each capital; pick one country and it opens into cities), a growth heatmap, a gained-or-lost chart and an interactive **Compare** chart (by country, product, entity or new vs existing customers; stacked area, share of total, lines or index). **Customise page** hides or shows each block. Then recommended actions. **Forecast explorer**: choose a model, history and horizon; the forecast carries a band of its typical test error; **Does the accuracy hold up?** repeats the backtest on three windows; under Advanced, model comparison and **Which orders count** (tick enquiries, drafts or cancellations in or out). Forecasts can be edited month by month and must be approved. **Customers to follow up**: churn and cross-sell lists, tick-to-approve. Gross margin uses assumed percentages by product line and the city split on the map is invented demo data; both are labelled in the files below |
| Platform settings | IT / System Administrator | Six tabs: **Approvals** (add an approval step for a new business unit, and the workflow designer), **Request types** (add a request type, edit its details, mandatory details), **Reminders and duplicates**, **People and access** (who may open what, and **Who sees private values**: the administrator chooses whether a role sees credit terms or bank details hidden, masked or in full), **Help guide** (edit topics and similar words), **Changes** (history, undo, download, reset). **Workflow designer**: every rule is a card in plain words. Routes (the first that fits wins) and 'also require' rules (every one that fits adds an approver) are edited as three questions: who is it for, anything extra, who approves. Switch rules on or off, move them, save to a draft, apply, undo. Under Advanced: try a request against the draft and see the diagram. Also changes mandatory details by request type, reminder days, each person's role and request types, and what each role can open. A change is checked, applied at once everywhere for the session, and written to the audit trail; the current rules can be downloaded |
| Waiting times | MDM Lead, Supervisor | Requests waiting for approval, the automatic reminder ladder, and the 18-day wait demo |
| Audit trail | MDM Lead, Auditor | Who did what and when. The log is a hash chain: a banner says whether the record is intact, and a button shows the check catching a change on a copy |

## Test

```bash
pytest -q                          # full suite
pytest -q -s tests/test_dedup_benchmark.py   # prints the precision / recall table
python tools/check_config.py       # lint config_rules.json (run after any edit to it)
python tools/spec_selfcheck.py     # re-runs the spec's own reference code on the name and evidence tables
```

| File | Covers |
|---|---|
| `tests/test_scenarios.py` | Spec 3.2 end to end: S1 to S5, validation, the full security matrix, and a search of every card, digest, audit entry and EMAIL view for the bank account number |
| `tests/test_mdm.py` | validation, abbreviation resolver, name table, evidence quorum, routing, state machine, audit scrubbing |
| `tests/test_security.py` | field and row security, tabs, capability text, Security Lens |
| `tests/test_m365.py` | Adaptive Cards per role, SLA escalations, email digest, simulated webhook |
| `tests/test_analytics.py` | noise filter, the six forecast models, churn radar, cross-sell, regional view, backtest, the rolling backtest, new vs existing customers (reconciles with the regional totals), order-pattern classes |
| `tests/test_dedup_benchmark.py` | precision / recall on 39 labelled pairs in `data/dedup_labelled_pairs.json` |
| `tests/test_settings.py`, `tests/test_workflow.py` | the dashboard's setting changes and the no-code rule designer |
| `tests/test_help.py` | the Help guide: word forms, similar words, topic editing, 89 sample questions |
| `tests/test_audit_chain.py` | the audit hash chain catches edits, removals, reordering and inserts |
| `tests/test_new_approver.py` | a new approval step: role, delegate, reminders, privacy |
| `tests/test_new_type.py` | a new request type: form, validation, duplicate check, routing, code style |
| `tests/test_field_policy.py` | the administrator's choice of who sees credit terms and bank details, and nothing else changing |
| `tests/test_autopilot.py` | the hands-free demo: every story builds, every field is keyed in, roles and pages are valid, the delays, the engine checks pass, the controls exist |

## Deploy to Streamlit Community Cloud

1. Push this repository to GitHub (public). The layout must stay as is: `app.py`, `config_rules.json`
   and `requirements.txt` at the root, with `tabs/` and `data/` beside them. The app reads `data/*.json`
   and `data/regional_orders.csv` with paths relative to the repository.
2. At share.streamlit.io choose **New app**, pick the repository and branch, and set **Main file
   path** to `app.py`.
3. Under **Advanced settings**, pick Python 3.11 or newer (3.13 matches the test run). No secrets
   are needed.
4. Deploy, then open the link in a fresh private window and run Scenarios 1 to 5 as a smoke test.

What to expect when it is hosted:

- **State lives in session memory.** Requests, the audit log and the simulated clock are kept in
  `st.session_state`. Each browser session starts from the same seeded master data. A reload or an
  app restart clears everything, and nothing is written to SAP or to disk.
- **The SLA runs on a simulated clock.** The Scenario 5 slider sets `sim_now` (pending since + N days).
  Escalations fire when the page reruns, not from a background timer. Emails, calendar invites and
  Adaptive Cards are rendered for preview only and are never sent. "Simulate click Approve (webhook)"
  calls the same transition a real Outlook card would POST.

## Roles and the Case Synopsis stories

The header bar has two demo controls. They are the only demo-specific parts of the screen; the page below them shows
only what a real user of that role would see.

**Role** switches between seven generic roles. What each sees (pages, rows and field values) is generated from
`config_rules.json` (`roles`, `security`, `users`, `demo.roles`).

| Role | Raises or decides | Pages |
|---|---|---|
| Requestor | Raises customers, vendors, items and cost centers | Home, New request, Requests |
| Approver (pick the step: Finance, Quality / Regulatory, Business Unit Head, Business Support) | Decides only the requests waiting at that step | Home, Requests (Finance also gets SAP records and Sales insights, Business Unit Head gets Sales insights) |
| MDM Analyst | Reviews duplicates, makes the final check, creates the record in SAP. Does not raise requests | Home, Requests (with SAP records), Sales insights, Waiting times, Audit trail |
| IT / System Administrator | Reads approval routes, mandatory fields, reminders and role access | Home, Platform settings, Waiting times, Audit trail |
| Payments Officer | Sees the full bank account, only once a request is approved | Home, Payments release (with SAP records) |
| Supervisor | Sees stuck requests and who was reminded; names and timings only | Home, Waiting times, Sales insights |
| Internal Auditor | Read-only view of every request and the audit trail | Home, Requests (with SAP records), Audit trail, Sales insights |

**Case Synopsis stories** are played **hands-free** on the real screens. Nothing is pre-filled and no request is created
behind the scenes: the player switches role with the header role switch (always one of the generic roles, never a new
named account), opens pages from the top navigation, scrolls, clicks and keys every field in. Stories: Amy's 18-day item
code, Marcus's new business unit, Wei Jie's credit terms, Priya's near-duplicate, the 18-day wait, the regional Sales
insights view, and one record seen through every role (redaction). **Role tours** cover the roles the stories do not:
IT administrator (creates a role and a rule, and decides who sees private values), Payments Officer, Supervisor and
Internal Auditor.

- **Flags** explain how it works, the security features and live checks. A check shows PASS or FAIL: the real security
  engine answers it, and where a value is on screen the player scrolls to the row, rings it and reads it back ("masked, not
  hidden", "full value"). Each check can point at the real test that backs it.
- **Name matches** are never skipped: the player scrolls the side-by-side comparison, shades the rows that do not match and
  gives the answer before the requester responds.
- **Email and integration JSON** are shown too: the Outlook email for each reminder, the approval card, and the SAP mapping,
  with the key fields explained.
- **Controls** sit at the bottom: a progress bar (one slice per step, click one to jump), **Back** (replays the previous
  step's explanation; it never repeats a step that changes data), a **fast-forward** that cycles 1x, 2x, 3x, Pause and Stop.
- In real life every account is tied to one role and sees only that role's pages and fields. The demo moves between roles
  only to show the whole journey. Keep the browser tab visible when you start: a hidden tab does not draw the top navigation.
- Timing (scroll 0.5 s, click 0.2 s, type 0.2 s, reading time) and every story's steps live in `data/autopilot.json`;
  the player is `tabs/autopilot.js`, the script builder is `tabs/autopilot.py`.

A regulated item (IVD or consumable) follows the chain in the brief's day-in-the-life: Quality / Regulatory,
then the Business Unit Head, then Business Support, then the MDM Analyst creates the record. The chain is the
`then` list on routing rule R04, and every approver's reminder clock starts when the request reaches them.

Duplicate evidence values (contact person, phone, address, postal code, email) are shown only to the Requestor, the MDM Analyst
and the Internal Auditor (a requestor cannot judge "same organisation?" without seeing the other party's details). Audit entries store signal IDs and the tier, never the compared values.
## Prototype vs production

| Area | This prototype | Production |
|---|---|---|
| Identity | The header role switch (a demo device). The app reads identity in one place, `get_current_user()` in `app.py` | Microsoft Entra ID (Azure AD) SSO. Roles map to Entra app roles or security groups, and the same `config_rules.json` drives them |
| Bank details | Mock values in server memory, masked by `apply_field_security()` | Stay in SAP (or a vault) and are fetched server-side only for authorised roles. Nothing sensitive goes into M365 messages |
| SAP Business One | Mock master data in `data/sap_master_mock.json`. "Sync to SAP" sets the request to SYNCED_TO_SAP and adds the record to the session's in-memory master copy; nothing leaves the app | SAP B1 Service Layer (the payloads already use OCRD / OITM / OOCR field names) |
| SLA engine | Runs on page rerun against a simulated clock | A scheduled Azure Function or Power Automate flow running `process_sla_escalations()` on real time |
| Outlook Actionable Messages | Adaptive Card JSON rendered for preview, with a simulated webhook | Actionable Email provider registration (originator ID), a real HTTPS endpoint, and bearer-token verification of the clicking user |
| Data and audit | Session memory, cleared on reload | A persistent database and an append-only audit store |

## Repository layout

```
app.py                  Streamlit shell: top navigation, demo toggle (switch person, start stories)
config_rules.json       all business rules (spec Section 1.1)
engine_mdm.py           validation, abbreviation resolver, dedup + evidence quorum, routing, state machine, audit
engine_security.py      field / row security, tabs, capability text, Security Lens
m365_integration.py     Adaptive Cards per role, SLA escalations, email digest, simulated webhook
engine_settings.py      the dashboard's setting changes: mandatory details, reminders, people, duplicate dials, new approvers, new request types
engine_workflow.py      the no-code rule designer's logic (rules as rows, validation, apply, diagram)
engine_help.py          the Help guide's BM25 search, word normalisation and topic editing (no AI)
engine_analytics.py     noise filter, six forecast models and their backtest (single and rolling), churn radar, cross-sell, regional view, new vs existing customers, order-pattern classes
tabs/                   one module per page (home, intake, inbox, staging, analytics, waiting times, audit, admin), plus ui.py (look and feel), charts.py (chart theme, KPI cards), autopilot.py and autopilot.js (the hands-free demo) and stories.py (the older manual stories, no longer in the menu)
data/                   seeded generator and its outputs (master data, PO history, scenarios, labelled pairs), map_points.json (capitals and demo cities for the map)
tests/                  pytest suite (see above)
tools/                  config lint and spec self-check
docs/                   demo video script, executive summary, answers to the brief's six questions, usability test plan, 2-page summary (.docx), HANDOVER.md
```
