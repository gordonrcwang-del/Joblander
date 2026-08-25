**Applies to**: any Eightfold.ai-hosted application form — typically `careers.<employer>.com/careers/apply` or `/careerhub/` paths.
**Data source**: `automation/job-apply/_internal/applicant-profile.json` + the employer's own persistent Eightfold profile (Eightfold stores your details per employer and refills them on every subsequent application).
**Employer-specific answers**: `sop/_local/<company_id>-eightfold.md` — see *Employer overlays* at the bottom. That file is gitignored; this one is not.

> **Watch for fake Workday mirrors.** Some employers on Eightfold also expose a `<tenant>.wd1.myworkdayjobs.com/External` site that looks live but carries no matching real postings. Confirm which host the employer's own careers page actually links to before treating either as canonical, and record the answer in the overlay.

---

## SETUP (manual, cannot be scripted)

**STEP 0 — Launch**
```
playwright_script.py <job_url> ~/.job-apply-sessions/<company_id>-eightfold-profile --dir ~/.job-apply-sessions/<key>/
```
`<job_url>` is normally `https://careers.<employer>.com/careers/job/<numeric_id>?domain=<employer>.com` — take it from `ledger.json`'s `url` field for that job rather than reconstructing it.

**STEP 1 — Dismiss cookie banner**
```json
{"action": "click", "role": "button", "name": "Reject All"}
```

**STEP 2 — Apply**
```json
{"action": "click", "role": "link", "name": "Apply Now"}
```
The Apply control is a styled `<a>` — its accessible role is **link, not button**.

**STEP 3 — Sign In**
Ask the user to sign in manually in the visible window, then wait for confirmation:
```json
{"action": "click", "role": "link", "name": "Sign In"}
```
Always sign in, even though the form is technically reachable as a guest — a guest submission doesn't attach to the profile and can't be tracked or reused.

**STEP 4 — Verify the autofilled form before submitting**

Signing in pulls the **entire** form from the employer's saved Eightfold profile — resume, contact info, address, standard application questions. Your job here is verification, not filling.

Use a **screenshot**, not `list_fields`: custom dropdowns and checkboxes don't appear in the field dump, so `list_fields` undercounts what's actually filled. This is one of the rare cases where a screenshot is worth the round trip.

Check specifically:

| Field | What goes wrong |
|---|---|
| Legal First / Last Name | CJK characters are **rejected at submit**, and the error message misleadingly blames punctuation. Use the romanized legal name from `legal_name.family_en` / `given_en` |
| "Given Name - Western Script" | This is the **preferred** name field — your everyday English name, not your romanized legal given name. It has defaulted to the legal name before; check it, don't assume either way |
| Resume filename | Surfaced verbatim to the recruiter on some tenants |
| Phone, address | Format varies by tenant — see `applicant-profile.json`'s `contact.note` / `address.note` |
| "How Did You Hear About Us?" | Usually `<Employer> Corporate Website` for a posting found on their own site |
| Terms and Conditions | Must be ticked; see the checkbox caveat in Notes |

**STEP 4b — Position Specific Questions (if present)**

This block is **per-posting and NOT pre-filled** from the saved profile, unlike everything else on the form. Always check for it and answer fresh on every posting.

```json
{"action": "click", "role": "radio", "name": "<option text><full question text>"}
```

Target these by **full `aria-label`** — option text plus the entire question text, concatenated. Clicking by visible label text fails: the `<input>` intercepts pointer events, and on retry the sticky footer intercepts instead.

Two traps seen here:
- A question worded as multi-select ("choose **all** cities that apply") whose underlying inputs are `type="radio"` sharing one `name` — it is actually single-select, and picking a second option silently deselects the first.
- Language-proficiency band questions where the bands are named by test score. Match the band to the score in `applicant-profile.json`'s `languages[].note`, don't eyeball it.

If the employer has a standing answer for one of these (a preferred site, a fixed location choice), record it in the overlay so it isn't re-decided per application.

**STEP 5 — Submit**

Check `auto-submit-config.json` for this `company_id`. Default (`"confirm"`, missing key, or missing file): show the user the complete filled application and get an explicit **yes** first.
```json
{"action": "click", "role": "button", "name": "Submit application"}
{"action": "click", "role": "button", "name": "Continue"}
```
The real confirmation is the modal — *"Thank you for your application, \<name\>!"* — plus an increment in the applications-list count. A click returning ok is not verification.

**STEP 6 — Applying to a second job**
```json
{"action": "goto", "url": "https://careers.<employer>.com/careers/job/<next_id>?domain=<employer>.com"}
```
Then repeat from Step 1. The Eightfold session **persists across a full engine restart** — relaunch and you're still signed in, unlike Workday which needs a fresh sign-in every time.

Navigate by direct job URL, not by clicking "Dashboard": `{"action":"click","text":"Dashboard"}` resolves to a hidden `<code id="navbar-data">` JSON blob rather than the visible nav link. Use `{"action":"click","role":"link","name":"Dashboard"}` if you ever genuinely need it.

**STEP 7 — "Already applied" case**

If the job page shows a green *"You have already applied for this position."* badge instead of an Apply button, mark it `applied` in the ledger with a note that it wasn't submitted this session. Do not attempt to reapply.

**STEP 8 — Wrap up**
```
python3 automation/job-search/_internal/scan_jobs.py mark <key> applied
echo '{"action":"stop"}' > ~/.job-apply-sessions/<key>/command.json
```

---

## Notes

- **Single-page form**, not a multi-step wizard like Workday. There is no Save-and-Continue rhythm and no `wait_for_text` page-transition dance — the whole application is one screen.
- **Autocomplete dropdowns require input in the same language as the page UI.** On a 繁體中文 interface, typing "Other" returns no matches where "其他" works. They fail silently — no error, just an empty list.
- **Two separate profile surfaces per employer.** Eightfold keeps a job-scoped review copy and a persistent general profile, and they don't always stay in sync. When cleaning up profile data, check both.
- **Untested path: a brand-new account.** Every run so far filled from an existing saved profile, with no field manually driven via `fill`/`choose_option`. How "How Did You Hear About Us?" and the other dropdowns behave when *not* pre-filled is unverified — treat a first-ever application at an employer as exploration, and expect to discover new quirks.

---

## Employer overlays

This file covers what Eightfold does everywhere. What differs per employer — the exact careers host, whether a fake Workday mirror exists, the Position Specific Questions and your standing answers to them, the "How Did You Hear About Us?" wording — goes in:

```
automation/job-apply/_internal/sop/_local/<company_id>-eightfold.md
```

`sop/_local/` is gitignored. Record the **literal JSON that worked**, not a description of it, plus the date you last verified it. Nothing in `_local/` is published.
