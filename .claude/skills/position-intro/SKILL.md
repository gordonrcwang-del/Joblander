---
name: position-intro
description: "Research a job posting the user just pasted (URL or raw JD text) and output a position-intro.md-style summary directly in the chat reply — never write it to a file. Covers JD summary (role + core responsibilities, work-mix table) plus a synthesis of what Taiwan forums (Dcard/PTT/104/518/Mobile01) say about WLB, pay structure, and growth prospects, with footnote-style source citations. Use whenever the user pastes a job link or JD and wants a quick read on it — e.g. 'what's this role like', 'is this worth applying to', or just a bare URL with no other comment when context makes clear they want it summarized. Distinct from the interview-prep skill: this is a fast, disposable chat-only lookup, not folder/file setup — if the user wants it filed under interview-prep/, that's the interview-prep skill instead, not this one."
---

# Position Intro — Chat-Only Job Posting Summary

Produces the same two-section research the `interview-prep` skill writes into `<Company>/<Position>/position-intro.md`, but outputs it **directly as the chat reply** — never write it to a file, never create folders. This is for a fast read on a posting the user just pasted, not for filing into `interview-prep/`. If the user separately asks to set up an interview prep folder for this role, that's the `interview-prep` skill, not this one.

## Steps

1. **Get the JD.** If the user pasted a URL, WebFetch it. If the fetch fails (dead link, login wall) or the user pasted raw JD text instead, use what's given — don't guess at content that couldn't be retrieved. If neither a working URL nor JD text is available, say so and ask for one rather than fabricating a summary.

2. **Identify company + role title** from the JD, for use as the reply's heading and in forum search queries.

3. **Research forum sentiment.** WebSearch Taiwan forums/review sites first — Dcard, PTT (相關看板, e.g. Salary, Tech_Job, or the company's own board), 104/518 面試心得與評價, Mobile01 — using queries like `"<Company>" "<position or level>" 面試 心得`, `"<Company>" 評價 dcard`. Glassdoor/LinkedIn only as fallback if Taiwan sources turn up nothing.

4. **Output directly in the chat reply** (not a file), in the canonical structure below.

## Canonical `position-intro` format

*This section is the single source of truth for this format. The `interview-prep` skill writes the same structure to `<Company>/<Position>/position-intro.md` and points here rather than restating it — edit the format here and both skills follow. Only the destination differs: chat reply here, file there.*

- **標題**: `# <Company> <Position>` as a heading.
- **JD 摘要** — two numbered points only:
     1. 簡短的 JD 總結（1–2 句話：角色定位＋核心職責）。
     2. 工作內容比例估算——手作／分析與文書／客戶溝通三者大致佔比（依 JD 描述的職責推算，標明是估算）。Render as a 3-row markdown table (類型／內容／比例), not prose.
- **網友分享摘要** — three subsections only: **WLB 程度**、**薪資結構**、**發展性**. Each subsection opens with **one synthesis sentence** — combine what netizens said with your own read on what it implies for the candidate — then below it the supporting **direct quotes** (in 「」form), each followed by a `[^n]` footnote marker. No other paraphrased summary paragraphs beyond that one opening sentence. If nothing relevant turns up for one of the three subsections, say so explicitly instead of inventing a quote or a synthesis.
- Below both sections, a **來源** list using markdown footnote definitions (`[^1]: [title](url)`, one per line) — every `[^n]` used above must have a matching definition here.

## Remaining steps

5. **Don't offer to save it as a follow-up unless asked.** If the user later wants this filed under `interview-prep/`, that's a separate request — point them to the `interview-prep` skill rather than writing the file yourself here.

## What this skill does NOT do

Never write files, never create folders, never touch `interview-prep/`. Output is chat text only, formatted in GitHub-flavored markdown so it renders inline. If the user wants persistent files (company-brief.md, position-intro.md, 模擬面試-QA.md, 基本知識.md filed under a company/position folder), that's the `interview-prep` skill.
