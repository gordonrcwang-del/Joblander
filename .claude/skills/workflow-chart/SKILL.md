---
name: workflow-chart
description: "Generate (or regenerate) a WORKFLOW.md for another skill in this project — a mermaid flowchart at medium detail (decision branches + real file/folder names kept, but short box labels and no step-by-step prose) plus a 'what gets created/touched' file tree. Use when the user asks to make, update, or fix a workflow chart/diagram for a skill, e.g. 'create skill workflow-chart', 'make a workflow diagram for autofill-agent', 'the interview-prep chart is stale, update it', or 'do the same for X's workflow'. Reference examples of the target detail level: interview-prep/WORKFLOW.md and automation/job-apply/WORKFLOW.md."
---

# Workflow Chart Generator

Produces a `WORKFLOW.md` for a target skill: a mermaid flowchart plus a file tree, at a specific detail level worked out through iteration on this project — not the fully technical version (SKILL.md already has that), and not an oversimplified kids'-explainer version either (tried that, it dropped decision logic the user wanted to keep). The goal is a diagram someone can actually use to understand *what decisions get made* and *what files show up where*, without wading through prose.

## Steps

1. **Identify the target skill.** From the user's message (a skill name, or "do the same as X") or context. If ambiguous, ask — don't guess which skill.

2. **Read the target skill's `SKILL.md` in full** (`.claude/skills/<skill>/SKILL.md`). Extract three things:
   - The main linear sequence of what happens, start to finish.
   - Every **decision/branch point** — anything phrased as "if X exists, skip," "if Y fails, fall back to Z," a config toggle, a check the skill makes before proceeding. These are the parts worth keeping as diamonds; don't flatten them into a straight line.
   - Every **file or folder read or written**, with its real path/name (not a paraphrase) — this feeds the file-tree section below.

3. **Draw the flowchart** at this detail level (match `interview-prep/WORKFLOW.md` and `automation/job-apply/WORKFLOW.md` as reference implementations):
   - Keep every decision point as a `{"..."}` diamond with its real branches — this is the part the user has explicitly asked NOT to simplify away.
   - Box labels: a handful of words, wrapped with `<br/>` if needed, not a full sentence. One emoji per major action box is fine for scannability; skip emoji on decision diamonds and pure pass-through boxes.
   - Use the real file/folder/config names from the skill (e.g. `applicant-profile.json`, `company_brief.md`) — these are not "too technical" to keep, they're the whole point of the "what gets touched" half of the doc.
   - Do NOT explain *how* each step works internally (no anchor-selection heuristics, no exact tool-call sequences, no rationale sentences inside boxes) — that belongs in the skill's own `SKILL.md`, not the chart. If a step in `SKILL.md` has a paragraph of internal reasoning behind it, the chart gets one short box, not a summary of the paragraph.

4. **Add a "What gets created / touched" section** below the diagram: a fenced code block showing the real file tree or path list, each line with a short trailing comment (`← what this is`, `(read, not written)`, `(written the first time, reused after)`) — not a bullet list, not prose.

5. **Optionally close with one sentence** on the skill's trust/safety guarantee if it has one worth surfacing (e.g. "nothing gets submitted without you seeing it first") — only if genuinely load-bearing, skip if the skill has nothing like that.

6. **Decide where to save it.** Default: **next to the skill's actual output**, not inside `.claude/skills/<skill>/` — e.g. `automation/job-apply/WORKFLOW.md` for autofill-agent (its output lives in `automation/job-apply/` and `~/.job-apply-sessions/`), `interview-prep/WORKFLOW.md` for interview-prep (its output lives in `interview-prep/`). If the target skill has no clear project-level output directory (e.g. a chat-only skill like `position-intro`), save it inside the skill's own folder: `.claude/skills/<skill>/WORKFLOW.md`. If genuinely unclear which applies, ask.

7. **If a `WORKFLOW.md` already exists there, regenerate it in place** (overwrite) — this is a generated diagram, not user-authored content, so no need to ask before replacing a stale one. Briefly note what changed if the regeneration was triggered by a `SKILL.md` update (new step, new file, new branch) rather than a first-time request.

8. **Report back** the path written.

## What this skill does NOT do

Doesn't touch the target skill's own `SKILL.md` — this only ever writes a companion `WORKFLOW.md`. Doesn't invent decision points or files that aren't actually in the target skill's spec — if `SKILL.md` is genuinely linear with no branches, the chart is linear too; don't manufacture diamonds for the sake of it.
