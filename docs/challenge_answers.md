# Answers to the brief's six design questions

Every answer says what the prototype does today and, where it falls short, says so. The data is generated demo data;
the SAP and Outlook connections are simulated previews.

## 1. Submitting a request without incomplete or duplicate submissions reaching an approver

**How a requestor submits.** One form per request type (Customer, Vendor, Item, Cost center), picked from cards. Mandatory
details come from one settings file and are flagged as the form is filled, so Amy's blank regulatory licence is caught before
she can send. The requestor presses **Check my request**, reads what the system found, then **Send request**.

**How duplicates are stopped.** A name alone is never trusted:
- Abbreviations are expanded automatically ("NUHS" becomes National University Hospital Singapore).
- The name is scored against SAP and requests already waiting (a mix of word overlap and spelling similarity; 70 percent for
  customers and vendors).
- A match is trusted only when at least two independent kinds of evidence agree and one is strong: email domain, tax number,
  postal code and address, contact person, phone. Free-mail addresses never count.
- Names that score just under the threshold (50 to 70 percent) are checked the same way automatically.
- The requestor sees their request next to the existing record and answers one plain question, "Is this the same as ...?",
  with the recommended answer highlighted. A reason for "no, it is different" travels with the request to the approver.
- An identical tax number is a hard stop.

On 39 labelled pairs we wrote ourselves, precision was 95.7 percent and recall 88.0 percent. That is a regression
baseline, not a field accuracy figure.

**Orders versus enquiries.** The regional view counts only confirmed customer purchase orders (2,416 of 2,705 rows, 89.3
percent). Enquiries, drafts, cancellations and orders still waiting for master-data setup are kept out and shown separately,
and the analyst can tick any of them in. Requests to set up master data are a different workflow and never enter the
forecast. At intake, the New request page has a link under the four cards, **Not a new record?**: the form is only for new
master-data records, so a chaser, a question or an order is sent to the place that answers it (Requests for status, Home for
what is waiting, or the help guide) and never reaches an approver. This is a deliberate choice by the requestor, not a
classifier that reads free text; customer purchase orders and enquiries themselves are not entered on this form.

## 2. Faster approvals, and knowing where a request stands

- **Routing by rule.** Each request goes to the right approver automatically. An item needing regulatory sign-off goes
  Quality and Regulatory, then Business Unit Head, then Business Support, then the MDM Analyst creates the record.
- **Everyone sees the status.** A progress tracker on every request, and the home screen shows what needs you and what is
  with others. Each approver is emailed an Approve / Reject message (shown as a mockup with its technical JSON).
- **A clock that acts.** If nobody acts, an urgent email goes out at 2 days, a review meeting invite for the approver and the
  supervisor at 4 days, and the request moves to the delegate at 8 days. The days can be changed on the dashboard.

Honest limits: Outlook messages and invites are simulated, the clock counts calendar hours not working hours, and there are no
per-user time zones yet.

## 3. A trustworthy record, and protected sensitive fields

- **Record.** Every action (submit, route, approve, reject, create in SAP, reminder, setting change) is written with who,
  their role, and when. The log stores identifiers and outcomes, never the private values themselves. It is also a hash
  chain: each entry carries a fingerprint of its own content and of the entry before it, so changing, removing or reordering
  an entry is detected and the Audit trail points at the first broken one (there is a button that shows the check catching a
  change on a copy). It proves the log was altered; it cannot stop someone who rewrites the whole log, which needs the latest
  fingerprint kept in a separate write-once store.
- **Protection.** What each role sees is decided by one rule set: row access first, then field by field. Finance sees
  credit terms but only the last four digits of the bank account. Only the Payments Officer sees the full number, and only after
  approval. Bank details are never in an email or an Outlook card. A test searches every card, digest, log entry and email view
  for the full account number and finds none.
- **Honest limits.** Who may edit sensitive fields is not built. A requestor can see the other party's contact details in a
  duplicate comparison (a deliberate decision, so they can judge). The log lives in the session, not a shared database.

## 4. Adding a domain, a required field or an approval step without going back to IT

On the Platform settings dashboard, no file edit:
- **Required fields:** tick or untick each detail per request type.
- **Approval steps:** the Workflow designer shows every rule as a sentence card. Add or change a route, or an "also require"
  rule that adds an approver whenever a condition is true (for example credit over 20000 also needs the Business Unit Head).
  Each rule is three questions: who is it for, anything extra, who approves.
- **Also on the dashboard:** reminder days, duplicate thresholds, and each person's role and what each role can open.
- **Safe to change:** edits build a draft that is checked and compared, applied in one click, written to the audit log, and
  can be undone.

A new business unit or team that needs its own sign-off is added in the same place (Approvals tab, Add an approval step): name the
step and the person, and it gets the same reminders, queue, email digest and delegate as the shipped approvers, then appears in
the rule designer. It sees only the details its approvals need (private finance and banking values stay hidden). Undo is refused
while a request is still waiting at a step the earlier settings do not have.

A new data domain (request type) is added the same way (Request types tab, Add a request type): name it, start from a blank
form (code, name, business entity) or a copy of an existing type, then add, rename or remove its details, set which are
mandatory, give a detail a list of choices, and mark finance or bank details as private. It gets its own SAP table, a duplicate
check by name, a code in its own style (for example PRO00001), a card on New request, a place in People and access, and it can be
routed in the rule designer. We ran one end to end through the screens: the IT admin made "Project code", a requestor raised
one, the MDM Analyst approved it, it was created in SAP as PRO00001, and a second request named "Alpha Roll-out" was flagged as
84% alike.

Honest limits for new types: the duplicate check is by name similarity only (the extra evidence checks used for customers and
vendors are not set up), the shipped four types are changed only through Mandatory details, SAP is still simulated, a new
type's extra private-field rules beyond the three data classes need the settings file, and a new approver's access to sensitive
fields is changed in the settings file too. Undo is refused while any request still uses the type or step being removed. Changes last for the
session; "Download the current rules" keeps them. A shared database would make them permanent for everyone.

## 5. Beating the 6 to 12 month purchase-order average

- **Six models to plan with**: linear trend, damped trend, seasonal level, seasonal trend, own season plus regional growth, and
  a blend. The rolling average is kept only as the yardstick and is not offered for planning. The level comes from 6 to 12
  months of history; the new model draws each series' own yearly curve from all its earlier history (at least 12 months,
  otherwise it steps down to a simpler model) and adds one growth rate shared by the whole region.
- **Tested honestly** on the last 6 months hidden from every model, across 15 country and product series: own season plus
  regional growth has an average error of 5.6 percent against 13.1 percent for the 6-month average and 10.4 percent for the
  12-month average (57 and 46 percent lower). The earlier regional seasonal level scored 10.1 percent. It beat the 6-month average
  on 14 of 15 series but the 12-month average on only 8 of 15; the gain comes mostly from the large series. Repeated from seven
  earlier start points the error stayed at 5.7 to 5.9 percent (worst 7.6). **We recommend dropping the rolling average.**
  The demo data has a yearly curve and steady growth generated into each series, which is what this model fits, so it shows the
  method works, not that real demand will behave the same.
- **Beyond the forecast:** growth by country and product (Vietnam consumables +23.1 percent; Singapore's growth falls to about 5
  percent without one new account), a churn radar (three accounts down 45 to 55 percent), and a cross-sell list (five accounts
  buying Equipment or IVD but no Consumables). Every forecast and list is a proposal a person edits and approves.

## 6. What we deliberately did not build in version one, and why

- **Live SAP and Microsoft 365 connections, and sign-in.** They carry the most risk and the least learning. We prove the rules
  first on previews, then connect.
- **A shared database.** State lives in the session. It is the first pilot blocker, and the cheapest to fix.
- **Writing to SAP automatically.** A person keys records until the rules have proved themselves.
- **Approvals at the same time, and "send back for a fix".** Both change how a request moves, so they need more design than a
  first version deserves.
- **A wizard for brand-new approver types and data domains.** Reusing the five existing steps covers the new-business-unit case.
- **Machine learning beyond six simple models.** With 6 to 12 months of history, simple models beat complex ones on honest
  tests, and a person has to be able to explain the number.
- **Who may edit sensitive fields, per-user time zones, working-hours clocks.** Real needs, but they follow a pilot.
