# Employer overlays — not published

Everything in this folder is **gitignored**. It's where your own employer-specific application knowledge lives, so the platform-level SOPs one level up (`workday.md`, `eightfold.md`) stay generic and shareable.

## Naming

```
<company_id>-<platform>.md
```

`company_id` matches the `id` field in `automation/job-search/_internal/sources.json` — that file is the single source of truth for which employers exist. `platform` is `workday`, `eightfold`, or whatever new ATS you work through.

## What belongs here

- The **literal JSON commands** that worked — not a description of them. "Use `choose_option` for this dropdown" is not replayable; the exact command with the exact label and option string is.
- Which page count this tenant uses (Workday 5-page vs 6-page), and the verified `wait_for_text` anchor for each transition.
- The Application Questions set **in order**, with your standing answer to each.
- The employer's real careers host, and any fake mirror to avoid.
- The date you last verified it, and the requisition IDs it was verified against.

## What does NOT belong here

Anything that would be true at *any* employer on that platform. That's a platform-level quirk — put it in `../workday.md` or `../eightfold.md` so everyone benefits, and so you only have to learn it once.

## Why the split

Your application history (which employers, which requisitions, which dates) is personal. The knowledge of how a Workday form misbehaves is not. Keeping them in separate files means you can publish the second without leaking the first.
