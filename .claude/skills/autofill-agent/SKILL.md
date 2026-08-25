---
name: autofill-agent
description: "Drive a persistent Playwright browser to fill out and submit online job applications on any ATS platform — Workday, Eightfold.ai, and others as they're encountered — using applicant_profile.json and BACKGROUND.md as the data source. Submission requires explicit user sign-off by default, per-company overridable to auto-submit via automation/job-apply/_internal/auto_submit_config.json. Use whenever the user gives a job-posting URL and asks to apply, or says things like '投這個', '幫我投履歷', '跑一次申請流程', 'apply to this job'."
---

# Job Application Autofill Agent

## When to use
Any time the user gives a job-posting URL (or names a specific role) and wants the application form filled and, once approved, submitted. This is platform-agnostic — the engine and workflow below apply to Workday, Eightfold.ai, and any new ATS encountered. Only the "Known Platform Quirks" appendix at the bottom is platform-specific.

## Architecture (why it's built this way)
- **Single persistent process owns the browser** (`launch_persistent_context`), driven via a file-based command queue: write `command.json`, the process executes it against its one `Page` object, writes `result.json`. Never open a second process/CDP connection to the same browser — two clients fighting over one target causes an indefinite hang, not a clean error.
- **OAuth sign-in blocks automated browsers** on every platform tested so far (Google/Apple/LinkedIn show "this browser may not be secure" under Playwright/CDP). The user must sign in manually via "sign in with email" in the visible window. This is a real security control — never try to route around it.
- **Engine script:** `automation/job-apply/_internal/playwright_script.py` (relative to the repo root) (renamed from `workday_agent.py` — its actions (`click`, `fill`, `type_into`, `select`, `choose_option`, `group` scoping, `batch`, `list_fields`) are built on generic ARIA roles/labels, not Workday-specific DOM, and it's already been run against Eightfold.ai as well — reuse it as the engine for any ATS site instead of writing a new script per platform.
- Run it with `--dir` pointed at `~/.job-apply-sessions/<job-id>/` (e.g. `~/.job-apply-sessions/acme-12345/`) — a fresh, uniquely-named subfolder per application, deliberately OUTSIDE the project folder. This is disposable runtime state (Chrome profile + command/result logs), not project content — never point `--dir` at anything under `automation/job-apply/` in the project folder.
- Read the script's own docstring for the current, authoritative command reference (action list, JSON formats) — don't duplicate that here, it drifts.
- **Page transitions: `wait_for_text` inside the batch, not polling from outside.** Put it as the LAST sub-command of a batch, right after the click that triggers a transition — Playwright waits natively for that text to render, and the whole transition resolves in one round trip. This replaced an earlier pattern of clicking then externally polling `get_state`/`list_fields` in a loop, which cost 3-4 round trips per transition and was prone to false positives.
  ```json
  {"action": "batch", "commands": [
    {"action": "click", "role": "button", "name": "Save and Continue"},
    {"action": "wait_for_text", "text": "Work Experience"}
  ]}
  ```
  **Anchor selection is the part that actually matters.** Pick text that can ONLY exist once real content has rendered — a specific field label, a specific answer value. Do NOT pick static page chrome (button labels like "Submit", section headers like "My Applications", nav text) — those render before the data does, so the wait resolves early and returns a page still showing "Loading" in the fields you actually care about. This produced two false positives in practice (Review page returning with most sections still "Loading" while "Submit" was already visible; the post-submit confirmation returning while the applications table was still empty). When unsure whether an anchor is safe, prefer text specific to the LAST section that loads on that page, not the first.
  - **Fallback for exploration** (before any anchor is known yet, i.e. first contact with a brand-new platform): delete stale `result.json`, write `command.json`, poll on a short interval until `result.json` exists, then read it. Once you've confirmed a reliable anchor for a transition, replace this with `wait_for_text` and don't go back to polling for that transition again.
  ```bash
  rm -f ~/.job-apply-sessions/<id>/result.json
  cat > ~/.job-apply-sessions/<id>/command.json <<'EOF'
  {"action": "..."}
  EOF
  for i in $(seq 1 30); do [ -f ~/.job-apply-sessions/<id>/result.json ] && break; sleep 0.3; done
  cat ~/.job-apply-sessions/<id>/result.json
  ```
- **Skip screenshots by default.** Prefer `list_fields`/`get_state` text — both are far cheaper than a full-page image round-trip. Only screenshot when a widget's visual state genuinely isn't reflected in the DOM (rare). Never write screenshots (or any other runtime file) into the project folder as a matter of routine — everything from a run belongs under `~/.job-apply-sessions/`, not `automation/job-apply/`; if a file genuinely needs to live in the project folder, confirm first.
- **Read `applicant_profile.json` in full before touching a new form**, specifically the `application_defaults` block — years-of-experience/sponsorship/work-authorization answers are very likely already recorded there from a prior application and shouldn't be re-derived from scratch.

## Data sources (read before filling anything)
1. **`automation/job-apply/_internal/applicant_profile.json`** — structured autofill data: legal/preferred name, address, education, `work_experience.entries` (the full factual record, one entry per employer/role), languages, visa/sponsorship defaults, resume path. Fast path — don't re-parse BACKGROUND.md prose if the field is already here.
2. **`Background Informations/BACKGROUND.md`** — narrative/STAR-story source, plus platform-specific pre-staged answers under "Application Autofill Kit".
3. **`Background Informations/job_description_*.md`** — per-employer job-description files. When a Role Description field needs employer-specific framing, check for one of these first and use its bullets verbatim, not paraphrased.
4. **Which `work_experience` entries to include, and how to word Role Description, is a per-application judgment call** — don't dump the unfiltered profile onto every application. Match entries to what the specific posting is asking for.
5. **`automation/job-apply/_internal/auto_submit_config.json`** — per-`company_id` toggle (`"confirm"` default vs `"auto"`) controlling whether Submit needs an explicit user yes. See Workflow step 6.

## Workflow
1. Confirm the job URL and identify the ATS platform from its URL pattern (see appendix). SOPs come in **two tiers, read both**:
   - **Platform SOP** — `automation/job-apply/_internal/sop/<platform>.md` (`workday.md`, `eightfold.md`). Covers what that ATS does at every employer. Always read this first.
   - **Employer overlay** — `automation/job-apply/_internal/sop/_local/<company_id>-<platform>.md`, where `company_id` matches `automation/job-search/_internal/sources.json`. Covers what differs at this specific employer: the question set and its order, page count, verified anchors, standing answers.

   With both present, follow them directly — skip re-exploring component behavior and only re-judge the JD-specific question(s) the overlay flags. With the platform SOP but no overlay, follow the platform steps and expect to discover employer specifics as you go; write the overlay afterwards via **Building an Employer Overlay** below. With neither (a brand-new ATS), explore as normal, then write a new platform SOP *and* an overlay.
2. Launch the engine script against the URL, with a fresh scratch `--dir`.
3. `get_state` to check sign-in status (its text dump shows "Sign In" vs. an account email/name). Fall back to `screenshot` only if the text is genuinely ambiguous. If sign-in is needed, ask the user to complete "sign in with email" manually in the visible window, then re-check.
4. On a "Start Your Application" / "use existing profile" prompt, default to **reusing the last saved application/profile** over a fresh resume-parse autofill — it avoids messier auto-parsed values (generic placeholder text, wrong formats). Only fall back to a fresh parse if no saved profile is offered.
5. Fill each section, preferring:
   - `group`-style scoping to repeated sections (Education 1/2, Work Experience 1/2/3, ...) over raw positional indexes, which drift when a section is added/deleted mid-session.
   - The platform's composite/dropdown action for custom widgets rather than assuming a plain `select`/`.fill()` will work.
   - Real keystroke simulation for comboboxes that silently ignore `.fill()`.
   - Command **batching** — bundle a whole page's fill/click steps into one round trip, ending with `wait_for_text` for the next page's content. This is the main speed lever; don't send one command per field when they can be grouped, and don't follow a batch with a separate polling call if it already ends in `wait_for_text`.
   - Self-verifying reads after each fill (value-echo, or a fast field-dump) instead of a screenshot per field; fall back to a screenshot only for widgets whose visual state isn't reflected in the underlying DOM value (e.g. chip/multiselect fields).
6. **At the review/summary step, check `automation/job-apply/_internal/auto_submit_config.json`** for this posting's `company_id`. Default (`"confirm"`, missing key, or missing file): stop here — `get_state`'s text dump of the review page is normally sufficient to show the user the complete filled content, no screenshot needed unless something on it is visually ambiguous — and get an explicit "yes" before touching Submit. If set to `"auto"` for this company: skip the confirmation and go straight to Submit, but still show the user the filled content in the same reply where you report the submission (after the fact, not before) so they have a record of what went out. This per-company override exists because the user explicitly asked for it (2026-08-13) — it does not relax the confirm-by-default posture for any company not explicitly flipped to `"auto"`.
7. Only when proceeding to Submit (explicit approval under `"confirm"`, or automatically under `"auto"`), attempt the click. If a system-level safety control blocks the click as an irreversible action even after clearance, do not try another tool to route around it — tell the user and let them click it themselves. That's an intentional second gate, not a bug.
8. After the run: if both tiers already existed and everything matched, no file changes needed. If something new turned up, write it to the tier it belongs in — **would this be true at any employer on this ATS?** Yes → platform SOP. No → employer overlay. Getting this split right is what keeps the platform SOPs publishable. If no overlay existed yet, follow **Building an Employer Overlay** below before ending the task.

## Building an Employer Overlay (first application at a given employer+platform)

Triggered by Workflow step 1 when `automation/job-apply/_internal/sop/_local/<company_id>-<platform>.md` doesn't exist yet. Goal: end with a file that lets the *next* application at this employer skip everything below and just replay commands. Format guidance: `automation/job-apply/_internal/sop/_local/README.md`.

1. **Explore page by page.** Two ways to do this:
   - **Recorder (preferred when the user is available):** send `start_recording`, then ask the user to click through the real application form themselves in the visible browser window — every click/change gets logged to `recording.jsonl` (role, accessible name, value) with zero guessing on selectors. Read that log afterward to build the SOP's commands.
   - **Agent-driven exploration (fallback):** `list_fields`/`get_state` (polling fallback from the Architecture section — no anchors are known yet), find the exact working command (`click`/`fill`/`choose_option`/`type_into`) by trial and error.
   Either way, cross-reference the correct value for each field against `applicant_profile.json` / BACKGROUND.md.
2. **Capture the literal JSON that worked**, not a description of it. A quirk write-up like "use choose_option for this dropdown" is not enough to replay later — the exact command with the exact label/role/option string is what goes in the SOP.
3. **Note any new component-interaction quirks** (two-level dropdowns, leftover overlay panels, attribute lies) in the platform's Known Platform Quirks section below as you hit them — this part of the process doesn't change.
4. **For each page transition, find a real anchor** for `wait_for_text`: text that only appears once the *next* page's actual content has rendered. Verify it — re-trigger the transition and confirm the returned state isn't still showing "Loading" anywhere relevant. Prefer an anchor from the last-loading section of the page, not the first.
5. **Separate hardcode-able answers from per-application judgment calls.** Anything sourced from `application_defaults` (sponsorship, years of experience, work authorization) is stable — hardcode it. Anything that depends on the specific posting's JD (e.g. "do you meet the basic requirements") is a judgment call — flag it explicitly in the overlay rather than hardcoding a guess.
6. **Get one full successful submission first**, with the user's explicit yes on the Review step. Don't write anything into the SOP as "verified" until it's actually been submitted and confirmed (platform's own post-submit status, not just a click returning ok).
7. **Write the overlay file** at `automation/job-apply/_internal/sop/_local/<company_id>-<platform>.md`: header (applies-to URL pattern, data source, verified date + req IDs) → **SETUP** section for anything that can't be scripted (manual sign-in) → one subsection per page, **command JSON only, no inline prose** — no rationale, no "this field needs X because Y" sentences between commands. Every step should read like a single code block, not a paragraph with code in it. Collect anything that doesn't fit in a command (gotchas, which answers are stable vs. per-application judgment calls, false-anchor warnings) into one shared **Notes** section at the very end, each point one line.
8. **Replay the overlay once on a second job** before fully trusting it — if anything differs, fix the file itself (don't just patch around it live and leave the file stale).

## Known Platform Quirks

### Workday (`*.myworkdayjobs.com`)
- Custom comboboxes ignore `.fill()` — needs real simulated keystrokes.
- Exact-match label lookups fail on required fields whose accessible name bakes in a "*"; but exact matching IS needed when one label is a substring of another (e.g. "Address Line 1" vs. "Address Line 1 - Chinese"). Judge per field.
- Repeated sections are exposed as `role="group"` named after their heading — scope to them.
- Large alphabetical picklists (500+ entries): try a direct option-by-name click first (auto-scrolls even off-screen); fall back to arrow-key hunting only if that fails.
- Some dropdowns are two-level: a category that itself name-matches (e.g. "Campus") opens a submenu of leaf options ("Campus: Career Board", ...) that use different ARIA semantics than the category level. Detect a still-open submenu after a click and treat it as incomplete, not success.
- Session does NOT persist across a script/browser restart, even with the same profile dir — re-login every relaunch.
- Plain department-name picklist entries (e.g. "Chemical Engineering") do exist standalone; don't assume only combined/hybrid options are available without checking.
- **"Application Questions" page custom dropdowns are NOT accessibly labeled by their question text** — `get_by_label(question_text)` and `group`-scoped clicks both time out (verified live on KLA). They're all just named "Select One" until answered. Fix: click role=button name="Select One" (hits the first unanswered one in DOM order — Playwright locators are DOM-order, not viewport-order), read the revealed options from `get_state`'s text tail, click/`choose_option` the answer, then repeat — each answered question's button name changes away from "Select One", so the next "Select One" click naturally advances to the next question. Do NOT try to target a specific question by its text.
- **After selecting an option in one of these dropdowns, its popper panel can stay open** (an invisible leftover overlay) and intercept clicks on later fields — the failure mode is a `Locator.click: Timeout` whose call log shows `<div>...</div> subtree intercepts pointer events` naming a stale option from an earlier dropdown. Fix/prevention: send `{"action": "press_key", "key": "Escape"}` right after every dropdown selection on this page, not just when something breaks.
- Checkbox `value` attributes reported by `list_fields` (e.g. `"value": "on"`) are static regardless of actual checked state — same caveat as radio buttons. Don't trust it to mean "already checked"; a required checkbox can still fail validation ("Field and Value required") even when `list_fields` shows `"on"`. Verify via the Review-step text dump or an explicit click.
- "How Did You Hear About Us?" is commonly a two-level dropdown; for postings sourced via this project's own discovery pipeline (`automation/job-search/_internal/scan_jobs.py`), the honest answer is category **"Corporate Website"** → leaf **"Company Career Site"**.

### Eightfold.ai (`careers.<employer>.com`, `/careers/apply` or `/careerhub/` paths)
- Use the romanized/English legal name for Legal First/Last Name fields — CJK characters are rejected at submit (the error message misleadingly blames punctuation). This overrides the general preference for the Chinese legal name used elsewhere.
- Autocomplete dropdowns require input in the SAME language as the page UI (e.g. type "其他" not "Other" on a 繁體中文 interface) or they silently return no matches.
- Gives each employer two separate profile surfaces (job-scoped review vs. persistent general profile) that don't always stay in sync — check both during profile cleanup.

### New platform encountered
Add a new subsection here (URL pattern → bullet list of component-interaction quirks) the first time a new ATS is worked through — this is the quirks appendix specifically; the full explore-then-write-an-SOP procedure is **Building a New SOP** above.
