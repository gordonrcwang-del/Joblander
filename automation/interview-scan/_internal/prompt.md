You are running unattended (no human present to answer questions). Do the following and nothing else.

## Goal
Check Gmail for anything that changes the state of an application — interview scheduling, rejections, offers, assessment requests. Keep `interview-prep/general/面試行程.md` current, make sure each interview exists on Google Calendar, keep the job ledger's status honest. Then send exactly one email every run. Its **body carries only what changed since the last run** — re-telling the user what they already know is the failure mode this scan is tuned against — but the mail itself always goes out, even when that means three empty sections, so a missing mail always means the scan broke.

## Steps

0. **Load the MCP tools first, in ONE call.** This machine runs with tool search enabled, so every `mcp__*` tool starts deferred — it is listed but not callable, and calling one without loading it fails. Issue exactly one ToolSearch:

   `select:mcp__claude_ai_Gmail__search_threads,mcp__claude_ai_Gmail__get_thread,mcp__claude_ai_Google_Calendar__list_events,mcp__claude_ai_Google_Calendar__create_event,mcp__claude_ai_Google_Calendar__update_event`

   If that call does not return the Gmail tools, stop: do not silently continue and report an empty scan — that would look identical to a clean one. Emit the step 6 report with `Gmail search: FAILED (MCP tools did not load)`, send it, and exit.

1. Read two files — together they are everything a previous run already knew:
   - `interview-prep/general/面試行程.md` in full — the current schedule state (dates, times, formats, status, interviewers). One table, no other sections: cancellations and reschedules live in the `狀態` column, never in a separate list. A cancelled interview keeps its row and gets status `已取消`; it is never deleted and never moved.
   - `automation/interview-scan/_internal/reported-state.json` — every line any previous run has already put in the user's inbox. Step 6 uses it to keep the email down to what actually changed. If the file is missing or unparseable, treat it as `{"version": 1, "entries": {}}` and carry on; step 6e says what to do about it.

2. Search Gmail for application-related email from the last 3 days. Three searches — the first two look at what arrived, the third at what you sent:
   - Scheduling: `(interview OR 面試 OR 面談 OR 意願 OR "phone screen" OR onsite OR "interview invitation") newer_than:3d`
     (`意願` is there because Taiwanese recruiters often open with "討論一下您的意願以及後續流程" and never write 面試 until the slot is already agreed — a real interview invitation once slipped past both queries because of exactly that phrasing.)
   - Outcomes: `(<company terms>) (application OR 應徵 OR 職缺 OR unfortunately OR "not selected" OR "not moving forward" OR "other candidates" OR "we regret" OR 婉謝 OR 未錄取 OR 不錄取 OR offer OR 錄取 OR assessment OR "online test" OR 測驗 OR 線上測驗) newer_than:3d`
   - Sent mail: `in:sent newer_than:7d` — used only by step 5.8 to tick off todos the user has already replied to. Seven days, not three: a todo can sit open for a while, and re-seeing a reply that already closed its todo costs nothing (5.8 is idempotent), whereas missing one leaves a done task nagging on the dashboard. This search never feeds buckets A–E; a message you sent is not an event that changes an application's state.

   **`<company terms>` is not a fixed list.** Build it at run time from `automation/job-search/_internal/sources.json`: for every entry, take its `name` plus every string in `aliases`, quote any that contain a space, and join them with ` OR `. Include entries whose `enabled` is `false` — you may still get email from an employer whose job board hasn't been wired up yet. Never hardcode company names here; `sources.json` is the single source of truth, so adding an employer there is all it should take.

3. Read each matching thread in full and sort it into exactly one bucket:
   - **A — interview invitation or reschedule** → steps 4 through 5.6.
   - **B — rejection** ("we've decided to move forward with other candidates", 婉謝, 未錄取).
   - **C — offer.**
   - **D — assessment / online test / take-home request**, with a deadline.
   - **E — noise** → discard and never act on it. This is most of what the second search returns: job alerts, marketing, newsletters, LinkedIn digests, application-*received* auto-replies (those confirm nothing new — the job is already `pending`), and anything not about an application this user actually submitted.

   Be strict about E. Acting on a misread email corrupts the ledger; ignoring one costs nothing, because the next real email on the thread will say the same thing. When a thread is ambiguous, treat it as E and mention it in [NEED ACTION] instead.

   Buckets B, C and D are handled in step 5.7. For bucket A, continue:

4. Extract, if present: company, role, interview date, time, format (phone/video/onsite), location, interviewer names, recruiter/contact name and email, and any explicit instructions (e.g. "bring ID"). Then compare against what's already in `interview-prep/general/面試行程.md`.
   - If the info is already reflected there (same date/time/details), skip it — do not duplicate.
   - If it's genuinely new (a new interview) or changed (rescheduled, new interviewer added), update the file: add or edit a row in the `面試排程` table, following the existing table's exact column format and Chinese phrasing style. A brand-new interview gets status `待進行`; a past one you are recording after the fact gets `已完成`. **The `形式` cell must be a markdown link to the meeting URL for anything online** — `[一面／線上](https://teams.microsoft.com/...)` — so the row is one click from joining. In-person interviews stay plain text, no link. **The `形式` cell is only the round plus the format** — `一面／線上`, `二面／現場`, `HR 談意願／線上`. Never put the location in it (no `二面／現場（新竹台元）`, no building or city names); the user reads this column to know what kind of interview it is, not where. Address details belong in the notification email, not in this table. If the invitation has no URL yet, leave the cell plain rather than inventing one, and say so in [INTERVIEW].
   - Do not remove or alter rows that are still accurate.
   - Do not touch any other file directly (see step 5.5 for the one exception).
   - **A `改期中` status overrides everything above.** `面試行程.md` is a single table whose `狀態` column carries one of `已完成` / `待進行` / `改期中` / `已取消`. If this email's company + role sits on a row whose status is `改期中`, a reschedule has already been requested and the other side hasn't confirmed yet. The email still quotes the OLD time — that is expected, not a change. **Do not rewrite the date, do not touch the status, and skip step 5.6 entirely for it** (see 5.6.0). Report it in [INTERVIEW] as `<Company> <date> <role> - reschedule pending, untouched`. Only once a confirmation email arrives with a genuinely NEW time does this stop applying — then update the file, set that row's status back to `待進行`, and proceed normally.

5. **For every genuinely new interview invitation** (not a duplicate you skipped in step 4), also run the `interview-prep` skill for that company + role, via the Skill tool (`Skill({skill: "interview-prep"})`), so a prep folder with `company-brief.md`, `position-intro.md`, `模擬面試-QA.md`, and `基本知識.md` gets created automatically. Two adjustments for running it unattended:
   - `interview-prep`'s normal instruction to "ask the user to paste the JD text" if the posting URL is missing/dead/gated does not apply here — you can't ask anyone. Instead: try `automation/job-search/_internal/ledger.json` first (as the skill normally does), and if that fails too, write `position-intro.md` with what you *can* determine (from the email itself, or company-brief-level info) and leave the JD-summary section with a clear one-line note that the JD needs to be pasted in manually — don't block the run waiting for input.
   - If a company/position folder for this exact role already exists with all four files, `interview-prep` will just skip everything (that's expected, not an error) — don't treat it as a problem.

5.5. **For every genuinely new interview invitation**, also check whether that job is already tracked in the job-search ledger — read `automation/job-search/applied-jobs.md` and look for a row matching the same company and a clearly matching role title.
   - If there's exactly one unambiguous match and its `目前狀態` isn't already `面試中` (or a further-along status like `已收到 offer`), run `python3 automation/job-search/_internal/scan_jobs.py progress <key> interview` from the project root — this updates `ledger.json` and regenerates `applied-jobs.md` for you, so don't edit either file directly.
   - If the match is ambiguous (multiple similarly-titled postings at the same company) or there's no match at all, skip it — don't guess a key. Put it in [NEED ACTION] instead (e.g. `<Company> <role> - no matching ledger row, status not updated`).
   - This is the one exception to "don't touch other files" above — only this exact command, only for a confirmed match.

5.6. **For every genuinely new or changed interview**, make sure it exists on Google Calendar (`<your-gmail-address>`).

   0. **Skip this whole step if the interview's row in `面試行程.md` has status `改期中`.** The time in the email is the one being moved away from, so creating an event for it would put a known-wrong slot on the calendar and collide with whatever the reschedule lands on. Wait for the confirmation email carrying the new time. Never create a placeholder event for a date that is under negotiation.

   a. First check what's already there: `mcp__claude_ai_Google_Calendar__list_events` with `calendarId: "<your-gmail-address>"`, `timeZone: "Asia/Taipei"`, and `startTime`/`endTime` bounding that whole interview day (e.g. `2026-08-26T00:00:00+08:00` → `2026-08-27T00:00:00+08:00`). Do **not** filter by `fullText` — see (b) for why.

   b. Judge whether any returned event is already this same interview. **Recruiters usually send their own calendar invitation, which lands on the calendar automatically, and its title will not match anything you'd write.** Their titles look nothing like yours — real ones seen in the wild take the form `<your name> + <Employer>` or `Your in-person interview at <Employer> for <Role> (<req id>)`. So match on **company (any alias — AMAT/Applied Materials, ASML, Garmin, KLA, TSMC, TI) appearing anywhere in the title, description, or organizer email, combined with a start time on that day**. Don't require the titles to look alike.
      - Same interview already on the calendar at the right time → **do nothing**. Never create a second copy.
      - Same interview on the calendar but the time has changed (rescheduled) → `mcp__claude_ai_Google_Calendar__update_event` with that `eventId`, the new `startTime`/`endTime`, `timeZone: "Asia/Taipei"`, and `notificationLevel: "NONE"`.
      - Nothing matching → create it, per (c).

   c. `mcp__claude_ai_Google_Calendar__create_event` with:
      - `calendarId`: `"<your-gmail-address>"`
      - `summary`: `面試｜<公司> — <職位>` — `<公司>` is the employer's `name` from `sources.json`
      - `startTime` / `endTime`: local ISO with the `+08:00` offset, **and always pass `timeZone: "Asia/Taipei"`**. This is mandatory — this calendar's default timezone is `America/Los_Angeles`, so a time without it lands 15 hours off.
      - End time: use the one stated in the email; if only a start time is given, default to **1 hour**.
      - `location`: the onsite address if it's in person, otherwise the video-meeting URL.
      - `description`: format (視訊/現場/電話), interviewer names, recruiter contact name + email, meeting link/ID/passcode, and any explicit instructions (e.g. 記得帶證件). End it with the line `由 interview-scan 自動建立`.
      - `overrideReminders`: `[{"method":"popup","minutes":1440},{"method":"popup","minutes":60}]`
      - `notificationLevel`: `"NONE"`
      - **Never set `attendees` / `attendeeEmails`, and never set `addGoogleMeetUrl`.** Adding the recruiter as an attendee would email them an invitation from you — that must not happen. Contact details go in the description only.
      - Never delete a calendar event, under any circumstance.

5.7. **Buckets B (rejection), C (offer) and D (assessment)** — keep the ledger's post-application status honest. Historically this was only ever updated by the user saying so out loud, which is why 33 applications sat at `pending` with zero rejections recorded despite months of activity. These emails arrive; the scan just has to read them.

   a. **Find the ledger key.** Read `automation/job-search/applied-jobs.md` — every row has a `Key` column. Match in this order, and stop at the first one that works:
      1. **Requisition ID.** Ledger keys embed it, in the form `<company-id>-<req-id>`. Employer emails nearly always quote that same ID somewhere (subject, body, or the footer's job link). Pull any req-ID-shaped token out of the email and look for a key ending in it. This is by far the most reliable match — try it first.
      2. **Company + job title**, only if exactly one applied row matches both.
      3. Otherwise → **no match. Skip the update entirely and report it in [REJECTED] with a `ledger not updated (<why>)` suffix.** Never guess a key; a wrong key writes a false status onto a live application.

   b. **Apply it**, from the project root:
      - B → `python3 automation/job-search/_internal/scan_jobs.py progress <key> rejected --note "<YYYY-MM-DD> 收到婉拒信"`
      - C → `python3 automation/job-search/_internal/scan_jobs.py progress <key> offer --note "<YYYY-MM-DD> 收到 offer"`
      - D → **no status change.** `assessment` isn't a valid status (they are `pending`/`interview`/`offer`/`rejected`/`withdrawn`), and an assessment request doesn't mean an interview was scheduled. Leave the ledger alone and surface it in [NEED ACTION] with its deadline — this is the one bucket that's time-sensitive and needs the user to act.

   c. **Don't fight yourself across runs.** The 3-day window means the same email gets read up to 6 times.
      - If the job's current status in `applied-jobs.md` already equals what you'd set, do nothing and say nothing — it's already been handled by an earlier run.
      - Never move a job backwards to `pending`.
      - Never set `withdrawn` — that's the user's decision alone, never inferred from an email.

5.8. **Tick off todos the user has already replied to.** The 待回信 section of `interview-prep/general/TODO.md` lists people waiting on a reply. If the user has since replied, that todo is done and should not still be sitting on the dashboard.

   Take the sent-mail results from step 2, reduce each message to its recipients and the date it was sent, and hand the whole lot to the todo CLI in one call:

   ```
   python3 automation/interview-scan/_internal/todo.py autoclose --sent '[{"to": "may.lo@garmin.com", "date": "2026-08-27"}, ...]'
   ```

   `to` may hold several addresses in one string; the CLI pulls them apart. Dates are `YYYY-MM-DD`. **Do not decide anything yourself here** — do not read `TODO.md`, do not judge whether a reply "really" answers the question, do not edit that file directly. The CLI owns every rule (only 待回信 rows, only email-address contacts, only replies dated on or after the todo appeared) precisely so the decision is the same every run instead of depending on how a thread reads today. You supply facts; it decides.

   The command prints one `已勾掉 …` line per todo it closed and ends with `autoclose: <N> closed`. Carry those lines into step 6's `[TODO CLOSED]` section verbatim. If the command fails, report `[TODO CLOSED] autoclose FAILED (<reason>)` and carry on — a stale todo is a nuisance, a halted scan loses interviews.

6. **Report — but only what is actually new.** Every run re-reads the same 3 days of email, so without a filter the report is the same four "already current" lines twice a day, and a genuinely new item is invisible inside them. The rule is: **the email carries deltas, the log carries everything.**

   6a. **Drop the no-ops.** An `[INTERVIEW]` line whose entire outcome is `already current`, `calendar already present`, or both — i.e. you verified something and changed nothing — never appears in the report. It is not news. A line survives only if it contains at least one of `schedule file updated`, `calendar created`, `calendar updated`, `ledger -> interview`, `prep folder created`, or `reschedule pending, untouched`.

   6b. **Drop what a previous run already said.** `reported-state.json` (read in step 1) is the memory of that. Compute a signature for each surviving line and look it up:

   | Section | Signature |
   |---|---|
   | `[INTERVIEW]` | `interview\|<company>\|<YYYY-MM-DD HH:MM>` |
   | `[REJECTED]` | `rejected\|<company>\|<ledger key>` — or `rejected\|<company>\|<YYYY-MM-DD the email was sent>` when no key matched |
   | `[NEED ACTION]` | `action\|<company>\|<deadline YYYY-MM-DD>` — or `action\|<company>\|<kind>` when there is no deadline, `kind` ∈ `assessment`, `offer`, `timing-conflict`, `other` |

   `<company>` is the employer's `name` from `sources.json`, or one of its `aliases` — always the same string for the same employer, run after run. Everything else in a signature is a date, a time, a ledger key, or a fixed keyword.

   **Nothing you phrase yourself belongs in a signature** — that is the whole design. Role titles and summaries get reworded every single run — the same interview has been logged as `HR intro` one day and `RD Engineer_<team code>` the next, and one item was worded three different ways in three consecutive runs — so any signature containing them fails to match its own previous entry and the line repeats forever. That is the exact bug this step exists to kill. Company plus a hard timestamp is enough to be unique — two different interviews at one company starting the same minute, or two rejections from one company in one day, do not happen.

   - Signature already in `entries` → **suppress the line**, with the one exception in 6c.
   - Signature not there → print it, and add `{"first_seen": "<today>", "last_reported": "<today>", "summary": "<the line you printed>"}`.
   - The interview signature carries the date+time on purpose: a rescheduled interview produces a *different* signature and correctly reports as new.

   6c. **Deadlines resurface.** A `[NEED ACTION]` item with a deadline reprints when today is on or past that deadline, even if already reported — at most once per day, so print it only if `last_reported` is not today, then set `last_reported` to today. Prefix it `DUE TODAY -` or `OVERDUE -`. Stop resurfacing once the deadline is more than 7 days past — by then the user either did it or decided not to, and a nag that never ends is the same noise this step removes. Everything without a deadline stays suppressed permanently after its first report.

   6d. **Turn brand-new `[NEED ACTION]` items into todos.** A line in that section is something the user has to do; the place they actually look for those is the dashboard, not an email from this morning. Create the todo with the CLI — never by editing the file:

   ```
   python3 automation/interview-scan/_internal/todo.py add --kind reply|deliver --action "<動作>" --contact <email> [--due YYYY-MM-DD]
   ```

   - **Only for a signature 6b decided was NEW.** A resurfacing item (6c) already has its todo — creating one again is the resurrection bug this rule exists to stop. `reported-state.json`, not `TODO.md`, is what remembers this: the done section keeps only the last 20 entries, so a todo that was ticked off weeks ago is no longer visible there, and dedup against the file alone would raise it from the dead every run.
   - **`--kind reply`** for an offer waiting on the user's answer — those are the ones step 5.8 can later tick off automatically, and it can only do that when `--contact` is the recruiter's actual email address. **`--kind deliver`** for an assessment or anything the user has to send in. **Everything else in `[NEED ACTION]` — timing conflicts, a broken state file, `other` — gets no todo**, only the report line; that section's job is to be read, the todo list's job is to be worked through, and not every notice is a task.
   - **`<動作>` is the action and nothing else** — `回覆 offer`, `完成線上測驗`, `寄成績單 PDF`. No company name (the contact says who), no dates in the text (`--due` says when), no explanation of why. The todo list is read at a glance; a sentence in it is a sentence too long.
   - Pass the deadline through `--due` when the item has one. No deadline is fine — the todo still gets created.
   - The command is idempotent, so a repeat with identical wording cannot double-add. Do not lean on that: your phrasing drifts run to run (the same reason 6b exists), so the signature check above is the real guard and this is only a backstop.
   - If the command fails, report it on the `[NEED ACTION]` line as `todo not created (<why>)` and carry on. Never edit `TODO.md` yourself to compensate.

   Todos the user typed in by hand are none of your business — `add` only ever appends, and nothing in this prompt removes a todo. The only automatic removal is step 5.8, and that needs a real reply in the sent folder.

   6e. **Write the state back**, and prune while you're there: drop any entry whose `first_seen` is more than 60 days old. Rewrite the whole file with the **Write tool** — arbitrary `python3` is not in this run's allowlist, and the file is small enough to emit in full. Keep the existing `version` and `note` fields. If reading or writing it fails, **fall back to reporting everything** and add a `[NEED ACTION]` line saying the state file is broken; a noisy report is recoverable, a silently swallowed interview invitation is not.

   6f. **Build the report string** from what survived:

   ```
   Gmail search: <ok|FAILED> (3 queries, <N> threads, <M> noise)

   [INTERVIEW]
     <one line each, or: none>

   [REJECTED]
     <one line each, or: none>

   [NEED ACTION]
     <one line each, or: none>

   [TODO CLOSED]
     <one line each, or: none>
   ```

   The ``` lines above are literal: emit them too, per 6h. Emit all four sections even when empty — an omitted section is indistinguishable from an agent that forgot to write it. `N` and `M` are the real counts from step 2, not post-filter counts.

   What goes in each section:

   - **[INTERVIEW]** — bucket A, one line per interview that actually changed:
     `<Company> <YYYY-MM-DD HH:MM> <role> - <what you did>`
     The trailing part is a comma-joined list drawn only from these phrases, so the log stays greppable: `schedule file updated`, `calendar created`, `calendar updated`, `ledger -> interview`, `prep folder created`, `reschedule pending, untouched`. (`already current` and `calendar already present` are still the right words to *think* in — they just mean the line gets dropped by 6a.)

   - **[REJECTED]** — bucket B, first run that sees it only. **Every rejection goes here, whether or not the ledger got written.** The ledger outcome is a suffix on the line, not a reason to file it elsewhere:
     `<Company> <role> - ledger -> rejected (<key>)`
     `<Company> <role> - ledger not updated (<why>)`
     e.g. `<Company> <role> (via 104) - ledger not updated (no req ID, title matches two open reqs)`
     An unfileable rejection would otherwise repeat for all 3 days of the window — 6b is what stops that.

   - **[TODO CLOSED]** — the `已勾掉 …` lines from step 5.8, verbatim, one per todo that got ticked off. **This section is exempt from 6a and 6b** — it is not a re-report of something the user already knows, it is a record of the scan changing the user's todo list on their behalf, and that must be visible every single time it happens. `none` when nothing closed.

   - **[NEED ACTION]** — things the user must actually do, soonest deadline first: assessment requests (bucket D) with their deadline, and offers (bucket C) awaiting a reply. Reported once, then again on and after the deadline day per 6c.
     A rejection you could not file never appears here — it is already in [REJECTED] with its reason, and there is nothing urgent to do about it.

   Do not invent extra sections. Noise stays a count on the first line — do not list noise subjects.

   If the Gmail search itself failed (tool error, not zero results), the first line is `Gmail search: FAILED (<one-line reason>)` and all four sections read `none`. Never let a broken scan render as a clean one.

   6g. **Send it — every run, without exception.** `python3 automation/interview-scan/_internal/send_email_notification.py "Interview scan - {ts}" "<report>"` from the project root.

   **Pass `{ts}` literally — those four characters, not a time.** You have no clock, and when earlier versions asked you for `<YYYY-MM-DD HH:MM>` the result was invented: a run at 08:33 mailed a subject reading `14:05`, one at 10:56 read `22:30`. The sending script substitutes the run's real start time, the same one in the `launchd.log` header. Do not "helpfully" fill it in. Same Gmail App Password (Keychain) as the job-search scan — no separate setup.

   A run where everything was filtered out still sends, with all three sections reading `none`. That mail is a heartbeat, not news: it says the scan ran and found nothing new. **Never skip the send because the report looks empty** — then a missing mail would be ambiguous between a quiet day and a broken scan, which is the exact confusion `run_scan.py`'s docstring exists to prevent.

   6h. **Make your final assistant message be that exact report and nothing else** — no preamble, no commentary, no markdown tables, no "Scan complete." That text is what lands in `logs/launchd.log`, and it must match the email byte for byte.

   **Wrap the report in a ``` fence** — an opening ``` line, the report, a closing ``` line, and nothing outside them. The fence is wanted (user's call, 2026-08-24): it marks where one run's report starts and stops inside `logs/launchd.log`, which is one long append-only file, and it survives into the mail body unchanged. `send_email_notification.py` passes the body through verbatim and deliberately does not strip it. Do not add a language tag after the opening backticks, and do not put any text — not a heading, not "Scan complete" — outside the fence.

## Constraints
- Read-only on Gmail — never send, label, or modify any email.
- On Google Calendar: create and update only, and only on `<your-gmail-address>`. Never delete an event, never add attendees (that would send an invitation to a recruiter), never touch an event unrelated to an interview.
- File writes are limited to `interview-prep/general/面試行程.md`, `automation/interview-scan/_internal/reported-state.json`, whatever `interview-prep` itself writes under `interview-prep/<Company>/<Position>/`, and — only via the exact CLI commands named in this prompt, never a direct edit — `automation/job-search/_internal/ledger.json` / `applied-jobs.md` (via `scan_jobs.py progress` in steps 5.5 and 5.7) and `interview-prep/general/TODO.md` (via `todo.py autoclose` in step 5.8 and `todo.py add` in step 6d). **Never edit `TODO.md` with the Edit or Write tool.** Nothing outside that.
- Suppressing a line from the email never means skipping the work behind it. Still update `面試行程.md`, still create the calendar event, still file the ledger status — step 6's filter is about the report only. A no-op is quiet because nothing changed, never because you decided not to check.
- A wrong ledger key is worse than a missed update: the missed one gets caught by the next email or by you, the wrong one silently marks a live application dead. When the match isn't certain, skip and report.
- Don't ask questions — you're unattended. If something is ambiguous, make the conservative choice (don't guess at missing details; leave a field out or a clear TODO note rather than inventing one) and proceed.
