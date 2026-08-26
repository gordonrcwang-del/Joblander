#!/bin/bash
# Scans Gmail for application-state-changing email and updates
# interview-prep/general/面試行程.md, Google Calendar, and the job ledger.
#
# NOT invoked directly by launchd. com.example.interviewscan runs
# run_scan.py (via /opt/homebrew/bin/python3) at 08:20 and 13:20, and that
# wrapper runs this script. Reason: macOS TCC refuses /bin/bash access to
# ~/Desktop, so a bash-fronted launchd job dies at exit 126 before line 1.
# See run_scan.py's docstring for the full diagnosis — don't repoint the plist
# back at /bin/bash.

set -euo pipefail

# Repo root, derived from this script's own location — do not hardcode.
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PROMPT_FILE="$PROJECT_ROOT/automation/interview-scan/_internal/prompt.md"

cd "$PROJECT_ROOT"

# ToolSearch is REQUIRED, not optional: this machine runs with
# ENABLE_TOOL_SEARCH=true, which makes every mcp__* tool deferred — listing
# them in --allowedTools grants permission but does NOT make them callable,
# so without ToolSearch the agent reports the Gmail tools as unavailable and
# the scan silently does nothing. Verified under launchd 2026-08-18.
# Absolute path required: launchd runs with a minimal PATH.
# Find yours with `which claude` and set CLAUDE_BIN in docs/SETUP.md step 3.
"${CLAUDE_BIN:-$HOME/.local/bin/claude}" -p "$(cat "$PROMPT_FILE")" \
  --allowedTools "ToolSearch mcp__claude_ai_Gmail__search_threads mcp__claude_ai_Gmail__get_thread mcp__claude_ai_Google_Calendar__list_events mcp__claude_ai_Google_Calendar__create_event mcp__claude_ai_Google_Calendar__update_event Read Edit Write Skill WebSearch WebFetch Bash(mkdir *) Bash(python3 automation/job-search/_internal/scan_jobs.py progress *) Bash(python3 automation/interview-scan/_internal/send_email_notification.py *) Bash(python3 automation/interview-scan/_internal/todo.py autoclose *) Bash(python3 automation/interview-scan/_internal/todo.py add *)"
