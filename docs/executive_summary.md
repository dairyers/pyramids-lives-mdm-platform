# Lives Group MDM & Analytics Platform: Executive Summary

*Prototype, October 2026. Every figure below comes from the automated test suite or from the running
app on seeded demo data. None of it is from live Lives Group systems.*

## The problem

Requests to create master data (customers, vendors, items and cost centers in SAP Business One)
travel by email. Appendix C of the brief describes one that waited 18 days because a mandatory field
was missing and nobody owned the queue. Duplicate customers enter SAP under abbreviations such as
"NUHS" or "Nat. Uni. Hospital Singapore", which no exact-match check will catch. Bank account
numbers are forwarded in plain text to people who have no need to see them. Meanwhile regional
sales planning relies on rolling averages over order data that mixes confirmed orders with drafts,
cancellations and enquiries.

## What we built

The prototype is one Streamlit app with two engines, both driven by a single rules file,
`config_rules.json`. That file holds the validation rules, routing matrix, SLA hours, role
permissions, field-security policy, abbreviation dictionaries, duplicate-evidence signals and
analytics parameters. Business behaviour changes by editing the file, not the code.

- The **MDM engine** validates a request, resolves abbreviations, then scores the name against
  existing records (0.6 × token-set + 0.4 × Jaro-Winkler, with a penalty for words like
  "hospital" vs "university"). It requires independent fields to corroborate a name match before
  trusting it, routes the request by structured rules (no `eval`), and escalates on an SLA clock.
- The **analytics engine** removes noise from purchase-order history, forecasts demand with a choice of
  six models that each work from 6 to 12 months of history, tests them on several earlier windows, splits
  growth between new and existing customers, and builds a churn radar and a cross-sell list. The Sales insights
  dashboard on top of it lets the user choose the measure (units, sales value or gross margin), the customers
  (all, existing or new) and which blocks to show.

Eight personas share one dataset. A small demo toggle switches the person, and the pages, rows and field
values shown. Every value passes through one function, `apply_field_security()`, whether it ends
up on screen, in an Outlook card, in an email digest or in the audit log.

## Role-based security

Policy depends on the role, the request state and the channel. The most restrictive of these
wins. The Security Lens in the app shows one record across every role. For the Scenario 2
customer (SMU Solutions, bank account 0123456789):

| Field | Requester | MDM Lead | Finance | Payments | Regulatory | Supervisor | Auditor |
|---|---|---|---|---|---|---|---|
| Credit line | 75000 | 75000 | 75000 | hidden | hidden | ****** | ****** |
| Bank account, pending Finance | ******6789 | ******6789 | ******6789 | ******6789 | hidden | hidden | ******6789 |
| Bank account, after approval | ******6789 | ******6789 | ******6789 | **0123456789** | hidden | hidden | ******6789 |
| Bank account, by email or card | hidden | hidden | hidden | hidden | hidden | hidden | hidden |

Row scope applies before any field is shown. The Regulatory Officer never sees that the Finance
request exists, and the Supervisor sees metadata only. The tests check this in both directions. In
the end-to-end test, all five scenarios run in one session and the S2 request is walked to
SYNCED_TO_SAP. The test then searches every Adaptive Card, every email digest, every audit entry
and every email-channel view for every role, and the full account number appears in none of them.
A second test runs every role, security class, workflow state and channel from the config (294
combinations) and checks that each returns exactly the configured policy. Inside the app, the
Security Lens never shows a viewer more than that viewer's own policy. To Marcus, the Payments
column reads "FULL (withheld from your view)".

## Duplicate detection

A name match alone is never trusted. A match is CONFIRMED only when at least two distinct field
groups agree and at least one of them is not weak (country and customer group count as weak).
Free-mail domains never count, and audit entries record signal IDs and the tier, never the
compared values. The Requestor, MDM Analyst and Auditor see the values themselves, so a requester can compare
their request with the existing record before answering.

| Candidate typed (vs C10023 National University Hospital Singapore) | Evidence | Result |
|---|---|---|
| 3a "Nat. Uni. Hospital Singapore" | corporate email domain, country, group | CONFIRMED (blocked unless justified) |
| 3b "NUHS" | as above, plus alias token in the domain | CONFIRMED |
| 3d "NUHS" | gmail address, country, group (weak only) | UNCONFIRMED: requester asked, MDM Lead decides |
| 3e "NUHS" | other company's domain, Malaysia, other group | CONTRADICTED: note only, routing unchanged |
| 3f "NUHS" | gmail, postal code, contact person (word order ignored) | CONFIRMED with no usable email |
| 3g "NUHS" | tax ID one character off, country, group | CONFIRMED |
| 3h "NUHS" | corporate email, Malaysia, other group | UNCONFIRMED: possible sister company |
| 3c "National University of Singapore" | n/a | **Not flagged** vs the hospital; flagged vs C10031 NUS |
| "SMU Solutions" vs Singapore Management University | n/a | **Not flagged** |

When an abbreviation has two approved expansions ("which NUHS: hospital or health system?"),
the engine reports AMBIGUOUS_ALIAS and asks the requester to choose. With a deciding field such as
the postal code, it confirms the right record and marks the other SUPERSEDED.

On 39 labelled pairs (25 same organisation, 14 different, including 8 negative controls) the
benchmark gives **precision 95.7% and recall 88.0%** (22 true positives, 1 false positive,
3 misses, 13 true negatives). No negative control was flagged. The single "false positive" is
variant 3h, which is flagged for human review by design. All three misses are spelling variants of
full names ("Singapore Genral Hospital", "National Heart Center", "Lumina Diagnostic"). Their raw
string similarity is about 0.97, but the guard that stops "hospital" matching "university" compares
core words exactly. A one-letter typo therefore counts as a conflicting word and scores 60 to 63%,
below the 70% threshold. Letting that guard accept near-identical words is the clearest next
tuning step. We wrote the
labelled set alongside the rules, so treat these figures as a regression baseline, not a field
accuracy estimate.

## People stay in control

Nothing the system infers is committed on its own. Before a request is sent, the requester reviews how the name was read (abbreviations are expanded automatically from a curated list plus initialisms generated from names already in SAP) and can edit the name used for the check. Records whose names fall just below the duplicate threshold are not ignored: the same non-name evidence check runs on them automatically (for example SMU Solutions against Singapore Management University: the email domain contains the abbreviation and country and group match), and the requester answers whether it is the same organisation. On the analytics side, the forecast is a proposal the planner can edit month by month and must approve, and the churn and cross-sell lists are tick-to-approve. Every approval is written to the audit log.

## Changing the platform without IT, and the help guide

The IT / System Administrator changes the platform on Platform settings, with no file edit: mandatory details,
reminder days, each person's role and what each role can open, the duplicate dials, approval rules in a no-code
workflow designer (a route, or an "also require" rule that adds an approver), a new approval step for a new
business unit or team, and a new request type (its own form, SAP table, name-based duplicate check and routing).
Every change is checked, written to the audit log and undoable, and lasts for the session unless the rules are
downloaded. Not covered: the extra evidence checks customers and vendors have are not set up for a new request
type, and sensitive-field editing rights are still set in the settings file.

The audit log is a hash chain: each entry carries a fingerprint of itself and of the entry before it, so an
altered, removed or reordered entry is detected and located. It cannot stop someone who rewrites the whole log;
that needs the latest fingerprint kept in a separate write-once store.

New request has a "Not a new record?" link under its four cards that sends chasers and questions to the status page or the Help
guide, so they never reach an approver. The Help guide is a level-1 lookup with no AI and no network (BM25 over
36 written topics, word-form normalisation, a similar-words list), and the IT administrator edits its topics.

## Workflow and SLA

Scenario 1 shows the fix for the 18-day case. An IVD item with no licence number is rejected at
submission with one message naming the field. Once fixed, rule R04 routes it to the Regulatory
Officer. Scenario 2 routes by R03 (credit above $50,000 or Net 90 terms; this request has both) to Finance. Scenario 4
routes by the default rule R99 to the MDM Lead. Scenario 5 runs on an injected clock. At 48 hours
the approver gets a high-priority email. At 96 hours a review invite goes to the approver and the
Supervisor, and at 192 hours the request is reassigned to the Finance delegate. The tests check
that each fires exactly once and that the SLA clock does not reset.

## Forecasting, churn and cross-sell (seeded demo data)

The generated history has 2,705 order rows over 24 months across five countries (SG, MY, TH,
ID, VN), three entities and three product lines. The noise filter keeps the 2,416 confirmed and
completed POs (89.3%). Drafts, cancellations, enquiries and POs awaiting master-data setup are
excluded. Twenty-four months is the shortest history that supports the reported measure (the last
12 months against the 12 before), and it is shorter than a typical multi-year SAP extract.

- **Six forecast models to plan with, picked in the dashboard** (Sales insights, Forecast explorer):
  a linear trend, a damped trend, a seasonal level, a seasonal trend, own season plus regional
  growth, and a blend of four. The rolling average (today's method) is kept only as the yardstick
  the models are measured against and is not offered for planning. The level comes from 6 to 12
  months chosen by the user. The seasonal models learn the yearly pattern from earlier history: the
  first two from all countries and products pooled, the new one from each series' own curve
  (needs 12 months of that series, otherwise it steps down to a simpler model). Holt-Winters was
  removed because it needs at least two full years for each series.
- **Result on the last 6 months, hidden from every model:** the most accurate model, own season
  plus regional growth, had an average error (WAPE) of **5.6%**, against 13.1% for the traditional
  6-month average and 10.4% for the 12-month average. That is 57% and 46% lower. It also scored
  5.3% to 5.6% at every history length from 6 to 12 months, and the earlier pooled seasonal level
  scored 10.1%. By series it beat the 6-month average on 14 of 15 but the 12-month average on only
  8 of 15, because the gain comes mostly from the large series. **Recommendation: stop planning with
  the rolling average.** The generator builds a yearly curve and steady growth into each series, which is
  exactly what this model fits, so this shows the method works, not that it will match real demand.
  The dashboard repeats the test on three consecutive windows (Forecast explorer, "Does the accuracy hold up?"):
  5.4%, 5.5% and 5.6% for this model, against 10.4% to 12.9% for the better traditional average on the same
  windows, so the result does not hang on one lucky six months. Each window is fitted only on the months before it.
  An earlier run from seven start points outside the app gave 5.7% to 5.9%, worst case 7.6%.
- **Churn radar:** it flags the three accounts the generator seeded as churners (orders down 45%
  to 55%: C10001, C10016, C10026). It correctly leaves out two seeded decliners at −15% and −17%,
  which sit inside the 20% threshold.
- **Cross-sell:** it lists the five seeded accounts that bought Equipment or IVD in the last 90
  days but no Consumables. Lift is 1.0 in every entity, so the demo data shows no association
  between the product lines. The list is a gap list, not a prediction.
- **Regional view:** Vietnam Consumables is the fastest-growing pair at +23.1% year on year
  (+7.6% excluding new accounts), and Malaysia Equipment is the weakest at +0.7%. Singapore's
  headline growth (+11% to +16%) falls to about +5% without one new account.
- **The dashboard (Sales insights, Overview):** a filter bar (country, product, measure, customers) drives four
  headline cards with sparklines, a Southeast Asia map with a heat dot on each capital that opens into cities when
  one country is picked, a growth heatmap, a gained-or-lost chart and an interactive Compare chart (measure, what to
  compare by, and how to draw it: stacked area, share of total, lines or index). "Customise page" hides or shows
  each block. New customers are those who bought nothing in that country and product in the same months a year
  earlier; the split reconciles exactly with the regional totals in units and USD.
- **Assumptions to state plainly:** the data has no margin or city field. Gross margin uses assumed percentages by
  product line (Equipment 22%, IVD 38%, Consumable 45%, kept in `config_rules.json` for Finance to replace), and the
  city split on the map is an invented share of each country's units. Neither is from Lives Group data.
- **Order patterns:** the engine can classify each customer and product pair as scheduled or ad hoc (average gap
  between order months and squared variation of order size). On the generated data all 103 pairs are scheduled, so
  it is not shown on the dashboard, and an intermittent-demand model such as Croston's would not be exercised.
## What this prototype does not do

The role switch is a demo device, with no single sign-on. There is no SAP connection. "Sync to
SAP" changes the request status and an in-memory copy of the master data. Outlook cards, emails
and calendar invites are rendered for preview and never sent. The approval webhook is simulated.
State lives in the browser session and is lost on reload.

## Roadmap

1. **Microsoft Entra ID SSO.** Identity is read in one function, so the role switch is replaced in
   one place. Roles map to Entra app roles, driven by the same config.
2. **SAP B1 Service Layer.** The payloads already use OCRD, OITM and OOCR field names. Bank
   details stay in SAP or a vault and are fetched server-side only for authorised roles.
3. **SLA timer on Azure Functions or Power Automate**, running the same `process_sla_escalations()`
   on real time.
4. **Outlook Actionable Message provider registration**, with a real endpoint that verifies the
   clicking user's token.
5. **Dedup tuning** on real labelled pairs, starting with typo tolerance in the core-word guard.

*Verification: `pytest -q` gives 1,308 passed. `tools/check_config.py` reports no defects, and
`tools/spec_selfcheck.py` reports 0 failures.*
