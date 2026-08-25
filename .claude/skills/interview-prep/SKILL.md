---
name: interview-prep
description: "Create the company/position folder pair this user files interview materials under — a company folder inside interview_prep/ at the project root, with a subfolder per role inside it (one folder per company, one subfolder per role) — and populate it with a company_brief.md, a position_intro.md, a 模擬面試_QA.md, and a 基本知識.md, all researched via web search. Use whenever the user asks to set up, create, or make an interview prep folder for a company and role, e.g. 'set up an interview prep folder for the Process Engineer role at Acme Semi', 'make a folder for KLA Product Development Engineer', or right after confirming a new interview when the user wants somewhere to put prep material. Trigger even if the user doesn't say 'interview prep' explicitly, as long as they're naming a company + role and want a folder/place to organize materials."
---

# Interview Prep Folder Setup

Creates `<Company>/<Position>/` under the `interview_prep/` folder, one company folder per employer, one subfolder per role inside it (e.g. `interview_prep/Acme Semi/Process Engineer/`). It also seeds four researched files: `<Company>/company_brief.md` (once per company), `<Company>/<Position>/position_intro.md` (once per role), `<Company>/<Position>/模擬面試_QA.md` (once per role), and `<Company>/<Position>/基本知識.md` (once per role). It does not create any other prep material (no self-intro slide deck, no README) — the user files that themselves as prep progresses.

The four files are **not written by this conversation directly**. Each one is researched and written by a spawned `interview-researcher` subagent (`.claude/agents/interview-researcher.md`), two at a time, so that pages of raw WebSearch and WebFetch output stay out of the orchestrating context. This conversation resolves the folders, dispatches the agents, and QAs what comes back.

**Project root:** the repo root — the folder containing this `.claude/` directory, and your working directory. All paths below are relative to it.
**Interview prep root:** `interview_prep/` — all company/position folders live here, not directly under the project root.

## Steps

1. **Get the company and position** from the user's request (or from context — e.g. an interview email just discussed in the conversation). If either is genuinely ambiguous (company name unclear, or which of several open roles at that company), ask — don't guess.

2. **Resolve the company folder.** List `interview_prep/` and check for an existing folder matching the company case-insensitively (e.g. "acme" should match an existing "Acme", not create a second folder next to it). Use the existing folder if found; otherwise use the company's common short name (the employer's `name` from `automation/job-search/_internal/sources.json` if it's listed there; otherwise the short name people actually say, not a full legal name like "Acme Semiconductor International Inc.").

3. **Resolve the position subfolder name.** Use the exact role title as given (Chinese or English). Replace `/` with `_` since it's filesystem-illegal — this matches the existing convention (`產品品質工程師(產品開發)_林口/汐止` → `產品品質工程師(產品開發)_林口_汐止`). Leave every other character as-is, including parentheses and spaces.

4. **Create the folders.** `mkdir -p "<interview_prep root>/<Company>/<Position>"` — this is safe to run even if the company folder already exists.

5. **Check which of the four files already exist.** `ls` the company folder and the position folder. Existing files are never regenerated or overwritten (unless the user explicitly asked to refresh one) — they just drop out of the work list. Build the list of *missing* files; only those get an agent. If nothing is missing, skip to step 9 and report that.

6. **Wave 1 — spawn the independent files in parallel.** In a single message, issue one `interview-researcher` Agent call per missing file among `company_brief.md` and `position_intro.md`. These two have no dependency on each other, so they must go out together, not one after the other.

   Each spawn prompt must name, explicitly (the agent starts cold and can see none of this conversation):
   - the **absolute path** of the file to write
   - the company and the exact position title
   - **which `## 檔案規格` subsection** in `.claude/skills/interview-prep/SKILL.md` is its spec
   - **a reference implementation, IF one exists.** Look for any prior `company_brief.md` / `position_intro.md` already under `interview_prep/` and name that path in the prompt. On a fresh install there won't be one — say so explicitly in the prompt ("no reference file exists yet; follow the 檔案規格 spec text alone") rather than naming a path that isn't there, which sends the agent hunting for a missing file.
   - for `position_intro.md` only: the posting URL, looked up from `automation/job-search/_internal/ledger.json` by company + title. Look it up **before** spawning — the agent shouldn't have to. If the job isn't in the ledger, the URL is dead, or the posting is behind a login wall, stop and ask the user to paste the JD text or a working link rather than spawning an agent to guess.

7. **Wave 2 — spawn the dependent files in parallel, after wave 1 returns.** `模擬面試_QA.md` and `基本知識.md` are both built out of what `company_brief.md` and `position_intro.md` say, so they cannot start until wave 1's files are on disk. Once they are, issue both Agent calls in a single message.

   Same prompt requirements as wave 1, plus each agent must be told to read, as source material:
   - `<Company>/company_brief.md` and `<Company>/<Position>/position_intro.md` (just written)
   - `Background Informations/BACKGROUND.md` — the candidate's actual track record, the source of every STAR story
   - `interview_prep/General/通用反問清單.md` (for 模擬面試_QA.md only)
   - a reference implementation IF one exists — any prior `模擬面試_QA.md` / `基本知識.md` under `interview_prep/`. Same rule as wave 1: on a fresh install, tell the agent none exists instead of naming a missing path.

8. **QA the returned files yourself — don't skip this.** A cold-started agent follows the tone rules in `## 檔案規格` less tightly than you would writing directly, and you never saw its drafts, only the result. Read each file that was written and check the things most likely to have slipped:
   - `模擬面試_QA.md`: 為什麼是這間公司 and 為什麼是這個職位 still fully separated, with no role/fit language leaking into the company answer; no calculated bridge-phrasing (`這正是同一種能力的不同應用場景` and its cousins); the 通用反問清單 items copied verbatim and complete, not rephrased or truncated; Tier 2 questions actually present.
   - `position_intro.md`: every `[^n]` marker has a matching 來源 definition, and each of the three 網友分享 subsections opens with exactly one synthesis sentence.
   - `company_brief.md`: exactly three sections, nothing added.
   - `基本知識.md`: grounded in this role's actual JD, not a generic textbook chapter.
   - Any specific number, standard, or product name that looks unsourced — check it against the 來源 list, and cut or mark it 未查證 if it isn't there.

   Fix what's wrong by editing the file directly. Don't re-spawn an agent for small corrections.

9. **Report back:** the full path(s) created — folders, and which of the four files were written vs. already existed (existing files aren't an error, just say so).

## 檔案規格

*These are the specs the `interview-researcher` agents follow. Steps 6 and 7 point an agent at one subsection each; the agent reads it here. Edit the rules here, not in the steps above.*

### `<Company>/company_brief.md`

Shared across every role at that company — written once, never regenerated just because a new position folder is being set up. Research with WebSearch and write exactly three sections — no more, no less.
- **一句話** — one line: what the company actually sells/does, and to whom (e.g. "賣機台給晶圓廠，不是自己做晶片"). This is the single sentence that reframes how the user should think about the company walking into the interview.
- **核心業務** — business segments/products, each with a rough size (revenue % or $ if easy to find) and what it covers. Table format when there's more than one segment. Fold in anything an interview-relevant recent development would change about this picture (earnings direction, layoffs, a segment hitting a record) as a short note under the relevant segment — don't give it its own "recent news" section.
- **台灣** — Taiwan site(s)/presence, what's distinctive about the Taiwan operation specifically (not just "there's an office"), and why that's interview-relevant (e.g. which site trains for which role, where the position being interviewed for actually sits).
- Below the three sections, a **來源** link list — every factual claim above should trace to one of these.
- Keep it tight — this is orientation before an interview, not a company dossier. Aim for something skimmable in under 2 minutes.

### `<Company>/<Position>/position_intro.md`

Written once per role; only regenerated if the user explicitly asks to refresh it.
- **Format: follow `.claude/skills/position-intro/SKILL.md` → "Canonical `position_intro` format" exactly** (JD 摘要 with its 2 numbered points and the 3-row work-mix table, 網友分享摘要 with its three subsections and quote-plus-footnote rule, then the 來源 footnote definitions). That section is the single source of truth for this format — read it, don't reconstruct it from memory, and don't restate it here. The only difference is the destination: that skill prints to chat, this one writes the file.
- **Getting the JD (this part is specific to this skill):** look the role up in `automation/job-search/_internal/ledger.json` (match by company + title) to get its posting `url`, then WebFetch that URL. If the job isn't in the ledger, the URL is dead, or the posting is behind a login wall, ask the user to paste the JD text or a working link rather than guessing at the content.

### `<Company>/<Position>/模擬面試_QA.md`

Written once per role. This is the piece that actually maps to getting the offer, not just orientation — don't make it a generic behavioral-question rehash that would read the same for any employer. Structure as numbered Q&A, STAR format (S/T/A/R bolded sub-bullets) for behavioral questions, plain-paragraph answers for the rest. Draw the STAR stories from `Background Informations/BACKGROUND.md` — reuse the same underlying stories across companies (they're the candidate's actual track record), but the *questions* and how each story gets framed must be specific to this company/role, built from what `position_intro.md` and `company_brief.md` already surfaced:
- **Why this company** and **why this role** are always two separate questions, never merged into one, and they stay on opposite sides of a hard line: "why this company" is commentary on the company alone — its positioning, scale, technology standing, industry role (from `company_brief.md`'s 一句話) — and must not mention the specific role, its responsibilities, or personal fit at all. Don't let it slide into "and that's why this position appeals to me" — that sentence belongs in the next question, not this one.
- **Both answers can run 3–5 sentences with real specifics** (a number from `company_brief.md`, a concrete piece of the JD) — length isn't the problem, *tone* is. Keep every sentence sincere and concrete, not a structured two-paragraph pitch. "Why this role" needs: what's genuinely interesting about the actual day-to-day work (from `position_intro.md`'s JD responsibilities), then a specific piece of the candidate's own experience it connects to. Cut analytical bridge-language like "這正是同一種能力的不同應用場景" — it reads as calculated rather than genuine, no matter how long the answer is. An answer that only argues fit ("my background matches this") without first saying what draws the candidate to the work itself is incomplete.
- **2–4 technical/situational judgment questions specific to what this JD actually asks the person to do** (e.g. an equipment-troubleshooting scenario for a field service role, a QCD-tradeoff or in-process quality catch for a quality/process role, an escalation-handling scenario for a customer-support role) — pull the shape of these from the JD responsibilities in `position_intro.md`, not from a generic bank.
- **At least one domain-gap question** — the interviewer's obvious objection given the candidate's actual background (whatever `BACKGROUND.md` says it is, when it isn't the target domain) for *this specific* field (e.g. no field-service/hands-on repair experience, no lithography/IC manufacturing experience, no direct SMT/strain-gauge experience) — answered by bridging via the transferable skill (root-cause analysis, DOE, quantitative QC method-building), not by claiming false familiarity.
- **Strengths/weaknesses, failure/improvement story, career plan, why left last job** — same underlying stories as other companies' files, reframed for this role.
- **Salary expectation** — reference the 薪資結構 numbers already found in this role's `position_intro.md`; if the user hasn't set a target number yet, say so explicitly as a placeholder to fill in rather than inventing one.
- **Questions to ask them** — two tiers, both included (the candidate picks 2–4 at interview time, this file is the full pool). Tier 1: the fixed question list verbatim from `interview_prep/General/通用反問清單.md` — copy it as-is, don't rephrase, and count the items in the file yourself rather than assuming a number, since that file grows. Tier 2: position-specific questions built from open items flagged in `position_intro.md` (things forum posts couldn't answer, ambiguities in the JD) — this tier is what actually earns interview points, so don't skip it just because Tier 1 exists.

### `<Company>/<Position>/基本知識.md`

Written once per role. This is domain/technical fundamentals — the crash-course knowledge sheet for a candidate whose actual background (read it from `Background Informations/BACKGROUND.md`) is different from this role's field, covering what an interviewer might quiz on. It is NOT company facts (that's `company_brief.md`) and NOT a mock Q&A script (that's `模擬面試_QA.md`) — it's the reference material underneath both. - Ground every section in what `position_intro.md`'s JD responsibilities and `company_brief.md` actually name — don't write a generic textbook chapter unconnected to the specific role.
- Organize by topic (e.g. process flow overview, core physical/technical mechanisms, equipment/platform names, industry-standard frameworks, safety basics — whatever topics the role's field actually requires). Format each item as **Term/中文名稱** — plain-language explanation (2–4 sentences) — a line on why it's relevant to what an interviewer might ask *this* candidate for *this* role.
- Verify every specific fact (numbers, standards, product names) via WebSearch — do not fabricate specifics. If a fact can't be confirmed, say so explicitly in an "未查證" note rather than guessing.
- If forum posts (Dcard/PTT/Mobile01/104/面試趣) report specific technical questions asked for this company/role, add a short "論壇回報的技術考題" section with `[^n]` footnote citations; if nothing turns up, omit the section rather than inventing one — a brief note explaining the search came up empty is fine.
- Traditional Chinese, zh-TW phrasing throughout. Keep it skimmable — a crash-course reference, not a textbook chapter.

## What this skill does NOT do

Beyond `company_brief.md`, `position_intro.md`, `模擬面試_QA.md`, and `基本知識.md`, don't create any other files inside the new folder (no self-intro slide deck, no README) — the user files real materials there themselves as prep progresses. If the user wants more starter files, that's a separate ask.
