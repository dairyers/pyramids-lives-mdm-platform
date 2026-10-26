# Demo video script: the Case Synopsis stories, answering the brief (about 3:00)

> **Update, 26 Oct 2026:** the stories are now played hands-free from the **Case Synopsis stories** menu (the manual guide-bar
> stories were removed from it). Click a story and the player keys every field in itself, with on-screen flags; use
> fast-forward (2x or 3x) to fit the time. The click-by-click table below still describes the content of each story, but you
> no longer click inside a story yourself. Keep the browser tab visible when you start a story.

The video runs five stories from the **Case Synopsis stories** menu, one after another: you pick each story yourself
(Amy, then Wei Jie, then Priya, then the 18-day wait, then the IT administrator). The guide bar at the bottom tells you
each click inside a story. The script is built on the brief's own words: Section 5 asks six design questions and Section 7
lists what the regional business view must contain. Each segment states the question out loud, shows the answer on screen,
and says why we chose that method. The brief asks for 2 to 3 minutes; this runs about 3:00 and **is too long to record as
written**: use the trim list at the end to get to about 2:25 before you record.

**New since the first version of this script** (marked "NEW" below): the "Not a new record?" link on New request, the
audit trail's tamper check, the Help button, the IT administrator's new approval step and new request type, and the forecast
numbers (5.6 percent). The NEW parts have not been rehearsed on screen; do a dry run first.

## Where each part of the brief is answered

| The brief asks (Section 5, 7) | Answered in | Proof on screen |
|---|---|---|
| Q1 Prevent incomplete or duplicate submissions | Amy, Priya | Red message before sending; plain duplicate question |
| Q1 Separate actionable requests and orders from enquiries and follow-ups | New request (NEW), Sales insights | The "Not a new record?" link; the "Which orders count" table (Forecast explorer, Advanced) with its Counted ticks |
| Q2 Faster approvals, everyone knows where it stands | Amy, the wait | Progress tracker, Outlook email, 2/4/8-day reminders |
| Q3 Trustworthy record of who approved what and when | Amy (last step) | Auditor's trail, and the tamper check (NEW) |
| Q3 Protect sensitive fields | Wei Jie | Bank account last four digits; full number only for Payments |
| Q4 Change a required field, approval step or data domain without IT | Platform settings | Workflow designer rule (Apply, next request follows it); NEW: Request types and Approval steps |
| Q5 Data science beyond the 6 to 12 month average | Sales insights | Six models, tested on months they never saw |
| Q6 What we chose not to build | Close | Spoken |
| Regional view: stated period, growth measure, implications, actions | Sales insights | (i) on "Where are we growing?", chart, Recommended Actions |
| Business case: cost, effort, adoption | Close (points to the 2-page summary) | Spoken |

## Before you record

Each story starts from its own first step, so you can record the five stories as separate clips in any order and
join them in the edit.

1. Run `streamlit run app.py` (or open the deployed link) in a fresh browser tab at full-screen width.
2. Open **Case Synopsis stories** and click **Reset demo** before every take.
3. Hide bookmarks and other tabs. Zoom the browser to 100 or 110 percent so the guide bar text is readable.
4. Speed the approval clicks up 2x in the edit if they drag.
5. Rehearse the NEW parts once. Changes made on Platform settings last for the session, so Reset demo before each take.

## The script

| Time | On screen (what you click) | Say |
|---|---|---|
| 0:00 to 0:10 | **Home** as the Requestor. Open **Case Synopsis stories**, click **Amy, Product Manager: the 18-day item code**. | "Lives Group creates SAP master data by email, and Amy's item code took eighteen days. Here is our answer to the brief's six questions, in five short stories." |
| 0:10 to 0:45 | **Amy.** On **New request** point at the four cards and the **Not a new record?** link under them (NEW). Choose Item, point at the red message under **Regulatory License No**. Type `HSA-IVD-2026-0881`, **Check my request**, **Send request**. **Next** through the three approvers (jump cut). Show the tracker, **Next** to the MDM Analyst, **SAP records**, **Create ... in SAP**, then the **Auditor** trail: point at **Record intact**, press **Change one entry in a copy and check it** (NEW). | "Question one: how do we stop incomplete requests reaching an approver? One form, checked as you type, and a question or a chaser is sent to the right page instead of becoming a request. Amy's blank licence is flagged before she can send. Question two: rules route it through Quality, the Business Unit Head and Business Support, each emailed in Outlook, and a tracker shows both sides where it stands. Question three: every step is in the audit trail, and it is a chain of fingerprints, so a changed entry is caught and located." |
| 0:45 to 1:05 | Open the menu, pick **Wei Jie, Finance Manager: credit terms and banking**. **Marcus:** **Check my request**, answer **No, a different organisation**, **Send request**. **Wei Jie:** point at credit terms and the bank account. **Next** to the Payments Officer: full number. | "Sensitive fields: Finance sees the credit terms but only the last four digits of the bank account. Only the Payments Officer sees the full number, after approval, and it is never in an email." |
| 1:05 to 1:35 | Open the menu, pick **Priya, MDM Analyst: the near-duplicate hospital**. **Marcus** types NUHS: **Check my request**, pause on the **side-by-side** table, **Continue**. Point at the **Recommended** answer, choose **No, it is different**, type a reason, **Send as a new record**. **Priya:** hover the (i) on the orange banner, point at **Notes attached**, show **Details**. | "Duplicates: N-U-H-S fools exact matching, and similar names are often different companies. So we expand the abbreviation, then need two independent signals, like email domain and country, before calling it a duplicate. Marcus sees his request next to the existing record, answers one plain question and records a reason. Priya sees why it was flagged and decides. People stay in control." |
| 1:35 to 1:47 | Open the menu, pick **Wei Jie's inbox: nobody notices for 18 days**. **Waiting times:** click **2 days**, **4 days**, **8 days**, **18 days**. Pause on the email mockup. | "Waiting is the real problem, so a clock acts: an urgent email at two days, a review meeting at four, a hand-over to the delegate at eight." |
| 1:47 to 2:15 | Open the menu, pick **IT / System Administrator: a new approval rule, no code**. In **Workflow designer** click **Add an 'also require' rule**: **Customer**, **Add a condition** (credit limit **20000**), add **Business Unit Head**, **Save to the draft**, **Apply the workflow**, **Next**. Then (NEW, not in the guide bar) open the **Request types** tab, **Add a request type**, name it `Project code`, **Add request type**. Flash the **Approvals** tab's **Add an approval step** panel. | "Question four: change the process without going back to IT. The administrator adds a rule: customers with a credit limit over twenty thousand also need the Business Unit Head. Save, apply, and the next request follows it. A new kind of record is the same: name it, shape its form, and it has its own SAP table, a duplicate check and a card on New request. A new business unit gets its own approver the same way. No file, no code." |
| 2:15 to 2:42 | Switch **Role** to **MDM Analyst**, open **Sales insights**. Hover the (i) on **Where are we growing?**, use the filter bar (**Measure**, **Customers**), point at the map and the heatmap, open the **Compare** chart on **New vs existing customers**, then **Recommended actions**. Open **Forecast explorer**, point at the model picker and the **Does the accuracy hold up?** chart, then **Advanced** and **Which orders count**. | "Question five: forecasting. Growth is the last twelve months of confirmed customer orders against the twelve before, for Products A, B and C in five countries. Enquiries, drafts and setup requests are kept out, and you can tick them in under Advanced. Six models work from six to twelve months of history, and the rolling average is kept only as the yardstick. Tested on months they never saw, our best is off by five point six percent, against thirteen for the six-month average and ten for the twelve-month one, and repeating the test on three earlier windows gives 5.4, 5.5 and 5.6 percent. So we recommend dropping the rolling average. It fits the yearly curve we generated, so we will re-test on real data." |
| 2:42 to 3:00 | Back to **Home**. Click **Help**, type a question (for example "where do i download the numbers"), show the answer. Hold on the screen. | "Question six: what we left out. No automatic SAP write, no machine learning beyond six models, until a pilot proves the rules. SAP and Outlook are previews on demo data. A built-in help guide, with no AI, answers where-is-it questions. Our two-page summary estimates eight to eleven person-weeks." |

## Trim list (to about 2:25)

- Cut the Request types and approval-step part of the IT segment and say only "a new request type or approver is added the same way": saves about 10 seconds.
- Skip the audit tamper button: saves 6 seconds.
- Skip the Payments Officer shot: saves 5 seconds.
- Speed the three approvers up 3x: saves 4 seconds.
- Drop the Help demo (keep one spoken line) and the Vietnam sentence if you keep it: saves 8 seconds.
- Drop "People stay in control": saves 2 seconds.

## Words and claims to avoid

- Do not call the numbers real Lives Group results. The orders are generated demo data.
- Do not say the app is connected to SAP or sends real Outlook email. Both are previews.
- Do not say people sign in. The Role button is a demo device, and Entra ID sign-in is the roadmap.
- Do not say the forecast "beats" the old method without both numbers (5.6 percent against 13.1 and 10.4; say it fits the generated yearly curve).
- Do not say the audit log cannot be tampered with. Say a changed entry is detected; someone who can rewrite the whole log is not stopped.
- Do not say the help guide understands questions. It looks up written answers.
- Do not say a new request type has the full duplicate evidence checks. It checks by name only.
- Say the person-weeks and monthly cost are our estimates, to be replaced with Lives Group figures.

## After recording

Upload to YouTube, paste the link into the **Demo video** line at the top of `docs/Lives_MDM_2page_Summary.docx`, and
check the length shows between 2:00 and 3:00.
