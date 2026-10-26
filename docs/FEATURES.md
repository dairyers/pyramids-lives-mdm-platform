# Lives Group MDM & Analytics Platform: feature walkthrough

## What it is
- A Streamlit prototype for Lives Group with two engines driven by one rules file, `config_rules.json`: master data management (MDM) for SAP Business One, and sales analytics.
- One app, one demo dataset and seven generic roles, switched with the Role button in the header.
- SAP and Outlook are simulated, and sign-in is a demo role switch. State lasts only for the browser session.

## Header and navigation
- The top bar lists only the pages your role can open, and scrolls sideways if they do not all fit.
- **Role:** switches who you are, for example Requestor, Approver, MDM Analyst, IT admin, Payments Officer, Supervisor or Internal Auditor.
- **Case Synopsis stories:** each story and role tour plays itself on the real screens (see "Hands-free demo" below), with a Reset demo button. A start page with a **Begin tour** button sits above the app.

## Hands-free demo
- Plays a story by itself: it switches role with the role switch (generic roles only), opens pages, scrolls, clicks and keys every field in. Nothing is pre-filled. A story starts from an empty session; a normal session starts with sample requests.
- Stories: Amy's item code, Marcus's cost center, Wei Jie's credit terms, Priya's near-duplicate, the 18-day wait, regional Sales insights (map, Malaysia by city, custom graphs, forecast model comparison), and one record seen by every role (redaction). Role tours: IT administrator (creates a role and a rule, decides who sees private values), Payments Officer, Supervisor, Internal Auditor.
- **Flags** explain how it works, the security features and live checks. Checks are answered by the real security engine and, where a value is on screen, read back from the page after scrolling to and ringing the row ("Masked, not hidden", "Full value").
- Similar-name matches are shown before they are answered: the side-by-side table is scrolled and the rows that do not match are shaded.
- Shows the Outlook email for each reminder with its Technical JSON, the approval card, and the SAP mapping, with the key fields explained.
- Controls: a progress bar (click a slice to jump), Back (replays the earlier step without repeating anything that changes data), fast-forward (1x, 2x, 3x), Pause, Stop.
- Every account is tied to one role in real life; the demo moves between roles only to show the whole journey.
- **Help:** a how-do-I lookup with no AI and no network. It ranks 36 written topics, repairs typos, and offers "Take me there" buttons.

## Home
- A welcome and a "My work" row of tiles (With others, Needs you, Rejected, Created in SAP), with counts for your role.
- A tile shows how many requests have waited past the first reminder time, with an "Only the late ones" shortcut.
- A "Next step" area, and a "Recent activity" list of the latest steps on requests you can see.

## New request (Requestor)
- Four cards: Customer, Vendor, Item / product, Cost center. Under them, a quiet link, "Not a new record? Chase a request, or ask how to do something", sends chasers and questions to Requests, Home or Help so they do not become requests.
- The form follows the rules file: mandatory fields, choice lists, and Finance details (credit, terms, banking) marked private, with an (i) hover note.
- **Duplicate check:** catches abbreviated or misspelled names, such as "NUHS" against National University Hospital Singapore. It scores name similarity and backs it with evidence from other fields (contact, phone, address, postal code, email).
- The requester sees a side-by-side comparison, and can pick the existing record or justify a new one.
- Submitting routes the request to the right approver automatically.

## Requests and approvals
- A card per request shows type, status, a progress tracker, waiting time, and details, history and email tabs. Cards are collapsed by default.
- Approvers get Approve and Reject with an optional comment, and a duplicate-review panel.
- An Outlook-style email preview with a simulated "Click Approve in the email".
- The Payments Officer sees a release queue. Full bank numbers appear only to that role, and only after Finance approves.
- The MDM Analyst creates the record in SAP from the "SAP records" view.
- Filters: With others, Finished, Everything, and "Over 2 days only" (requests waiting past the first reminder).

## Waiting times (SLA board)
- Shows what is waiting and for how long, on an 18-day clock.
- Automatic reminders and hand-overs to a delegate fire on the schedule in the rules file.

## Sales insights
Three tabs: Overview, Forecast explorer, Customers to follow up.

**Overview**
- A filter bar: Country, Product, **Measure** (Units, Sales value, Gross margin) and **Customers** (All, Existing, New). Every block follows it.
- **Customise page** hides or shows each block: headline numbers, map, units per month, growth heatmap, gained or lost, Compare chart.
- Four headline cards with sparklines: units, sales value, fastest growing, needs attention.
- A Southeast Asia map. Each country has a heat dot on its capital (size is the amount, colour is growth), with a ranked table beside it. Pick one country and the map opens into that country's cities.
- A growth heatmap (country by product), a units-per-month trend against the year before, and a gained-or-lost chart by country.
- A **Compare** chart: choose a measure (sales value, gross margin, units, margin %, price per unit, active customers, units per customer), what to compare by (country, product, entity, or new vs existing customers) and how to draw it (stacked area, share of total, lines, index).
- Recommended actions in plain words, and the full numbers with a CSV download.

**Forecast explorer**
- Choose entity, country, product, model, months of history and horizon. Six models, each working from 6 to 12 months. The forecast carries a shaded band of its typical test error.
- **Does the accuracy hold up?** repeats the backtest on three consecutive windows: 5.4, 5.5 and 5.6 percent for "Own season + regional growth", against 10.4 to 12.9 percent for the better traditional average.
- Under Advanced: compare models, and **Which orders count** (confirmed orders by default; enquiries, drafts and cancellations can be ticked in, and a warning shows above every tab when they are).
- A human edits and approves the forecast plan; approvals are audited.

**Customers to follow up**
- A churn radar and cross-sell suggestions for five countries, tick-to-approve and audited.

**Assumptions**
- The data has no margin or city field. Gross margin uses assumed percentages by product line (in `config_rules.json`, to be replaced with Finance's figures), and the city split on the map is an invented share of each country's units (`data/map_points.json`).

## Audit trail
- A permanent record of who did what and when. Private values are never written to it.
- Each entry carries a fingerprint chained to the one before it, with a "Record intact" banner.
- A button shows the check catching a change on a copy.
- Filters by event and request reference, and a CSV download.

## Platform settings (IT admin)
- Six tabs: Approvals, Request types, Reminders and duplicates, People and access, Help guide, Changes.
- **Who sees private values** (People and access): pick a role and credit terms or bank details, then Hidden, Masked or Full value. It applies at once for the session, is written to the audit trail and can be undone. Bank details are never put in an email, whatever is chosen.
- A new role (an approval step) and a new "also require" rule can be created here without code; the IT administrator tour in the hands-free demo does both.
- **Approvals:** add a new approval step with a person and delegate. It gets reminders, a queue and a digest entry.
- **Request types:** a builder for a new request type, for example "Project code", with its own SAP table, duplicate check and code prefix. It includes a no-code workflow designer for approval rules.
- **Help guide:** edit topics and similar words, with a "try a question" box.
- **Changes:** every change is audited and can be undone, and the rules can be downloaded.
- Changes last for the session only.

## Security and privacy
- Every value shown, exported, emailed or logged goes through one function, `apply_field_security()`.
- Duplicate-evidence details (contact, phone, address) are visible only to roles allowed to see them.

## Limits to mention
- Generated demo data, a simulated SAP and Outlook, and session-only state. A shared database is the first pilot blocker.
- The audit chain detects edits, but cannot stop someone who rewrites the whole log.

## Suggested demo order
1. Requestor submits a request.
2. An approver and the MDM Analyst process it.
3. IT admin changes a rule.
4. Audit trail and Sales insights.
