**Applies to**: any Workday-hosted application form — `<tenant>.wd1.myworkdayjobs.com`, `wd3.`, `wd5.` etc. Some employers front it with their own careers site; the real ATS is still Workday (Step 0 shows how to find the real Apply link).
**Data source**: `automation/job-apply/_internal/applicant_profile.json`
**Employer-specific answers**: `sop/_local/<company_id>-workday.md` — see *Tenant overlays* at the bottom. That file is gitignored; this one is not.

---

## SETUP (manual, cannot be scripted)

**STEP 0 — Launch**
```
playwright_script.py <job_url> ~/.job-apply-sessions/<company_id>-profile --dir ~/.job-apply-sessions/<key>/
```

If the posting URL is the employer's own careers page rather than a `myworkdayjobs.com` URL, dismiss the cookie banner and dig out the real Apply link first:
```json
{"action": "click", "role": "button", "name": "Only essential cookies"}
{"action": "evaluate", "script": "Array.from(document.querySelectorAll('a,button')).filter(el => el.textContent.includes('Apply')).map(el => ({tag: el.tagName, href: el.href || null, target: el.target || null}))"}
{"action": "goto", "url": "<href from above>"}
```

**STEP 1 — Apply + reuse last application**
```json
{"action": "batch", "commands": [
  {"action": "click", "role": "button", "name": "Apply"},
  {"action": "click", "role": "button", "name": "Use My Last Application"}
]}
```
Some tenants show a cookie banner here instead of at Step 0 — `{"action": "click", "role": "button", "name": "Decline"}` before the Apply click if so. Always prefer **Use My Last Application** over "Autofill with Resume": the resume parser produces messier values (placeholder text, wrong date formats) that you then have to clean up field by field.

**STEP 2 — Sign In**
Ask the user to sign in manually via **"Sign in with email"** in the visible window, then wait for their confirmation. OAuth (Google/Apple/LinkedIn) is blocked under automation on every tenant tested — that's a real security control, don't try to route around it.
```json
{"action": "wait_for_text", "text": "How Did You Hear About Us"}
```

**STEP 2b — Next job at the same employer (no relaunch, no re-sign-in)**
```json
{"action": "goto", "url": "<next job's posting URL>"}
{"action": "batch", "commands": [
  {"action": "click", "role": "button", "name": "Apply"},
  {"action": "click", "role": "button", "name": "Use My Last Application"},
  {"action": "wait_for_text", "text": "How Did You Hear About Us"}
]}
```
If this bounces to Sign In instead, the session actually died — fall back to Step 0 + Step 2.

---

## SCRIPTED (send as written, page by page)

Workday numbers its own steps, and the count varies by tenant: **5 pages** (My Information / My Experience / Application Questions / Voluntary Disclosures / Review) is the common shape; **6 pages** splits Application Questions into two. Don't hardcode the count — check each page's heading before assuming which one you're on.

**STEP 3 — My Information**

"How Did You Hear About Us?" is usually a **two-level** dropdown: a category that opens a submenu, then a leaf. Send the two levels as **separate calls** — batching them aborts the batch (see Notes).
```json
{"action": "choose_option", "label": "How Did You Hear About Us?*", "option": "<category>"}
```
```json
{"action": "batch", "commands": [
  {"action": "click", "text": "<leaf option>"},
  {"action": "click", "role": "button", "name": "Save and Continue"},
  {"action": "wait_for_text", "text": "Work Experience"}
]}
```
For a posting found through your own discovery pipeline rather than a job board, the honest answer is category **"Corporate Website"** → leaf **"Company Career Site"**. Some tenants name the category after themselves (`<Employer>` → `<Employer> Career Site`) — record which, per employer, in the tenant overlay.

**STEP 4 — My Experience**

"Use My Last Application" has already populated this. Verify, don't refill.
```json
{"action": "batch", "commands": [
  {"action": "click", "role": "button", "name": "Save and Continue"},
  {"action": "wait_for_text", "text": "<first question text on the next page>"}
]}
```

**STEP 5 — Application Questions**

Every dropdown on this page is `role=button` named **"Select One"** until answered — they are NOT accessibly labeled by their question text, so `get_by_label(question)` and `group`-scoped clicks both time out. Each `choose_option` call hits the first still-unanswered question in DOM order, so a fixed sequence naturally walks the whole page in order. Send `Escape` after each selection to close the leftover popper panel that otherwise intercepts the next click.

```json
{"action": "batch", "commands": [
  {"action": "choose_option", "role": "button", "name": "Select One", "option": "<answer 1>"},
  {"action": "press_key", "key": "Escape"},
  {"action": "choose_option", "role": "button", "name": "Select One", "option": "<answer 2>"},
  {"action": "press_key", "key": "Escape"},
  {"action": "click", "role": "button", "name": "Save and Continue"},
  {"action": "wait_for_text", "text": "<anchor for next page>"}
]}
```

The **question set and its order are tenant-specific** — record them in the overlay once, then replay. Typical questions and where the answer comes from:

| Question | Source |
|---|---|
| AI-matching consent | `application_defaults.ai_matching_consent` |
| Do you meet the basic job requirements | **Per-application judgment.** Safe to answer Yes for anything that already passed `scan_jobs.py`'s screening |
| Legally eligible to work in \<country\> | `application_defaults.legally_eligible_to_work_*` |
| Require sponsorship now or in future | `application_defaults.requires_sponsorship_now_or_future` |
| Years of relevant experience | `application_defaults.years_of_relevant_experience_bucket` |
| Worked here via a third party in the last 12 months | `application_defaults.worked_at_company_via_third_party_last_12mo` |

Free-text fields (a job-list textarea, location preferences, a test score) need their element ids read first:
```json
{"action": "evaluate", "script": "Array.from(document.querySelectorAll('textarea')).map(el => ({id: el.id, value: el.value}))"}
```
```json
{"action": "fill", "selector": "#<id>", "value": "<value>"}
```
If a tenant asks **"which jobs have you applied for with us"**, regenerate the answer from `ledger.json` on every run — the list grows with each application, so a value copied from the overlay is stale the moment it's written:
```
python3 -c "
import json
led = json.load(open('automation/job-search/_internal/ledger.json'))
apps = sorted((j['applied_date'], j['title']) for k,j in led['jobs'].items() if k.startswith('<company_id>-') and j['state']=='applied')
print('\n'.join('%s %s' % (d.replace('-','/'), t) for d,t in apps))
"
```

**STEP 5b — Second Application Questions page (6-page tenants only)**

Check the heading after Step 5's Save and Continue. If it's already "Voluntary Disclosures" / "I agree and consent to terms and conditions", this page doesn't exist on this posting — skip to Step 6. Where it does exist it usually carries clean-room / shift-work / travel-percentage questions, whose answers are stable per employer and belong in the overlay.

**STEP 6 — Voluntary Disclosures**
```json
{"action": "batch", "commands": [
  {"action": "click", "label": "<the terms-and-conditions checkbox label, verbatim including the *>"},
  {"action": "click", "role": "button", "name": "Save and Continue"},
  {"action": "wait_for_text", "text": "I am fluent in this language"}
]}
```
For self-ID dropdowns (gender, veteran status, disability), do **not** use `choose_option` — open the dropdown and click the exact option (see the substring bug in Notes):
```json
{"action": "click", "role": "button", "name": "Select One"}
{"action": "click", "text": "<your answer>", "exact": true}
```

**STEP 7 — Review → Submit**

The `wait_for_text` result from Step 6 already carries the full review page — read it from there rather than spending another `get_state`.

Check `auto_submit_config.json` for this `company_id`. Default (`"confirm"`, missing key, or missing file): show the user the complete filled application and get an explicit **yes** before clicking Submit. Only under `"auto"` do you submit first and show the content afterwards.
```json
{"action": "click", "role": "button", "name": "Submit"}
{"action": "wait_for_text", "text": "Under Review"}
```
Anchor varies by tenant — `"Under Review"` or `"Application Submitted"`. Confirm via the returned `url` (`.../jobTasks/completed/application`) plus this job showing as submitted today, not by the click returning ok.

**STEP 8 — Wrap up**
```
python3 automation/job-search/_internal/scan_jobs.py mark <key> applied
```
Only stop the engine if this was the last job in the batch — otherwise go back to STEP 2b.
```
echo '{"action":"stop"}' > ~/.job-apply-sessions/<key>/command.json
```

---

## Notes

- **Sign-in survives navigation inside one live browser context**, but not a process/browser relaunch — even with the same profile dir. Sign in once per batch, then use STEP 2b for every subsequent job at that employer.
- **No polling, no state-checking.** Every batch ends with a `wait_for_text` anchor that can only exist once the *next* page has actually rendered; Playwright waits natively and the call returns only when ready. Never follow such a batch with a `get_state`/`list_fields` check. If a batch fails it stops at the failing sub-command and reports the index — stop there and ask the user rather than improvising.
- **Anchor choice is the part that actually matters.** Pick text that cannot exist until real content has rendered. Never anchor on page chrome — button labels ("Submit"), stepper labels ("Application Questions 2 of 2"), nav text — those render before the data and the wait resolves early against a page still showing "Loading". Prefer text from the *last* section to load on the target page: on a Review page that is typically Languages ("I am fluent in this language"), not "Legal Name".
- **Two-level dropdowns abort a batch.** `choose_option` on a category returns `ok:false` ("submenu is still open") because it only opens the submenu. That aborts the batch and silently skips everything after it (reproduced 4/4). Split category and leaf into separate calls. Treat a still-open submenu as incomplete, not as success.
- **`choose_option` substring-matches.** `option: "Male"` can silently select **Female** ("Female".includes("Male")). Any option value that is a substring of a sibling option is unsafe — open the dropdown and click with `exact: true` instead.
- **Checkbox `value` attributes lie.** `list_fields` reports `"value": "on"` regardless of actual checked state — same caveat as radio buttons. A required checkbox can still fail validation while showing `"on"`. Clicking is a *toggle*, not a set: never click "just in case". Verify via the Review-page text dump first, then click only if genuinely needed.
- **Custom comboboxes ignore `.fill()`** — they need real simulated keystrokes (`type_into`).
- **Exact-match labels: judge per field.** Exact matching fails on required fields whose accessible name bakes in a `*`, but is *required* when one label is a substring of another ("Address Line 1" vs "Address Line 1 - Chinese").
- **Repeated sections** (Education 1/2, Work Experience 1/2/3) are exposed as `role="group"` named after their heading — scope to them rather than using positional indexes, which drift when a section is added or removed mid-session.
- **Large alphabetical picklists** (500+ entries): try a direct option-by-name click first — it auto-scrolls even off-screen. Fall back to arrow-key hunting only if that fails. Plain department names ("Chemical Engineering") do exist standalone; don't settle for a combined/hybrid option without checking.
- **Education ordering is not stable across jobs** at the same employer — undergrad first on some, grad first on others. Same records, same content; it's Workday's own rendering choice, not a data problem.
- **Silent bounce-back.** Observed once: mid-Application-Questions, after every dropdown was answered but before Save and Continue, the app reset to Step 1 (the URL dropped `/useMyLastApplication`) and a `fill` against a previously-confirmed element id timed out with "no such element". Cause unconfirmed. **If any action times out against an element `get_state` just confirmed was there, re-run `get_state` before retrying** — you may have been bounced back a step. Pages already Saved-and-Continued past stay intact; the in-progress page's answers do not.

---

## Tenant overlays

This file covers what Workday does everywhere. What differs per employer — the exact "How Did You Hear About Us" category, the Application Questions set and its order, whether there are 5 pages or 6, which `wait_for_text` anchors were verified — goes in:

```
automation/job-apply/_internal/sop/_local/<company_id>-workday.md
```

`sop/_local/` is gitignored. Keep your own employer answers there and this file stays generic, so an update to the platform-level steps never collides with your personal application history.

An overlay should record the **literal JSON that worked**, not a description of it — "use choose_option for this dropdown" is not replayable; the exact command with the exact label and option string is. Include the date you last verified it and, if useful to you, the requisition IDs it was verified against. Nothing in `_local/` is published.
