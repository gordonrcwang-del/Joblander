#!/usr/bin/env python3
"""
run_scan.py — launchd entry point for the interview scan.

WHY THIS EXISTS (two reasons, both learned the hard way on 2026-08-18)

1. TCC. macOS will not grant Desktop access to /bin/bash, so a launchd job
   whose ProgramArguments start with /bin/bash cannot read anything under
   ~/Desktop — it dies with exit 126 ("Operation not permitted") before the
   script's first line runs. /opt/homebrew/bin/python3 DOES hold that grant
   (it is what com.example.jobdiscover has always run as), and a child
   process it spawns inherits the grant. Verified with two launchd probes:
   bash-as-program → DENIED, python3-as-program → OK, python3's bash child → OK.
   So this file must stay the plist's ProgramArguments[0] target, run by
   /opt/homebrew/bin/python3. Do not "simplify" the plist back to /bin/bash.

2. Failure heartbeat. The scan's own success email is sent at the last step of
   scan_gmail_interviews.sh, so any crash means silence — and silence is
   exactly what a quiet job-market day looks like. That is why reason 1 went
   unnoticed. A `trap` inside the shell script could not have caught it either
   (the script never started). The heartbeat therefore lives HERE, outside the
   process that can die: whatever happens to the child, this wrapper reports it.
"""
import os
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))
SCAN_SCRIPT = os.path.join(BASE_DIR, "scan_gmail_interviews.sh")
EMAIL_SCRIPT = os.path.join(BASE_DIR, "send_email_notification.py")

TIMEOUT_SECONDS = 20 * 60  # a claude -p agent run; well past the ~2-4 min it needs
TAIL_CHARS = 1500          # how much child output to quote in a failure email

# Retry the whole scan on failure rather than waiting for the next slot.
#
# WHY (2026-08-20): the 13:20 run died when the Gmail MCP search timed out
# three times in a row. The heartbeat email fired correctly, but nothing
# re-ran the scan, so the next attempt was 08:20 the following morning — a
# 19-hour blind window. In it, an employer sent a rescheduled interview time
# and it went unrecorded. The upstream flakiness is Gmail's and not fixable from
# here; what IS fixable is that one bad draw ended the day's scanning.
#
# The gap is long because these failures are minute-scale service stalls, not
# millisecond blips, and each attempt is a full agent run — cheap to repeat a
# few times, wasteful to hammer.
ATTEMPTS = 3
RETRY_GAP_SECONDS = 10 * 60


LAUNCHD_LOG_PATH = os.path.join(BASE_DIR, "logs", "launchd.log")


def launchd_is_capturing():
    """True when the plist is already redirecting our stdout into launchd.log.

    The plist passes --scheduled and nothing else does. With the flag,
    printing IS logging and mirroring would double every entry; without it
    (any hand-started run, including one an agent runs for the user) nobody
    is capturing stdout, so this process has to write the log itself.

    An earlier version inferred this from sys.stdout.isatty(), which silently
    dropped every run started through a tool or a pipe. Keep the flag."""
    return "--scheduled" in sys.argv


_transcript = []


def emit(line):
    """print(), plus keep a copy for mirror_to_log on manual runs."""
    print(line)
    _transcript.append(line)


def mirror_to_log(text):
    """Append a hand-started run to the same file launchd writes to, so the log
    records every scan and not only the scheduled ones. Best-effort."""
    try:
        os.makedirs(os.path.dirname(LAUNCHD_LOG_PATH), exist_ok=True)
        with open(LAUNCHD_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(text + "\n\n")   # blank line keeps blocks separated
    except Exception as e:
        print("mirror_to_log failed (non-fatal): %s" % e)


def send_failure_email(subject, body):
    """Best-effort — a broken heartbeat must never be louder than the thing it
    reports on. Failures here go to the launchd log and nowhere else."""
    try:
        subprocess.run([sys.executable, EMAIL_SCRIPT, subject, body],
                       timeout=60, cwd=PROJECT_ROOT)
    except Exception as e:
        print("heartbeat email failed (non-fatal): %s" % e)


def run_once(run_ts):
    """One child scan. Returns (returncode, output); 124 marks a wrapper timeout.

    run_ts is exported so send_email_notification.py can stamp the subject line
    with the run's real start time. The agent has no clock and was inventing
    one (a run at 08:33 mailed a subject reading 14:05), which made subjects
    useless for ordering mail or matching it to a launchd.log block."""
    env = dict(os.environ, INTERVIEW_SCAN_RUN_TS=run_ts)
    try:
        proc = subprocess.run(
            ["/bin/bash", SCAN_SCRIPT],
            cwd=PROJECT_ROOT, env=env,
            capture_output=True, text=True,
            timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        emit("TIMEOUT after %ds" % TIMEOUT_SECONDS)
        return 124, "(timed out after %d minutes, no output captured)" % (TIMEOUT_SECONDS // 60)

    output = (proc.stdout or "") + (proc.stderr or "")
    if output.strip():
        emit(output.rstrip())
    return proc.returncode, output


def main():
    run_ts = time.strftime("%Y-%m-%d %H:%M:%S")
    emit("=== interview-scan %s ===" % run_ts)

    last_rc, last_output = 1, ""
    for attempt in range(1, ATTEMPTS + 1):
        if attempt > 1:
            emit("--- attempt %d/%d (previous exit=%d) ---"
                 % (attempt, ATTEMPTS, last_rc))
        last_rc, last_output = run_once(run_ts)
        if last_rc == 0:
            if attempt > 1:
                emit("OK (recovered on attempt %d/%d)" % (attempt, ATTEMPTS))
            else:
                emit("OK")
            print("")
            if not launchd_is_capturing():
                mirror_to_log("\n".join(_transcript))
            return 0
        if attempt < ATTEMPTS:
            emit("retrying in %d minutes" % (RETRY_GAP_SECONDS // 60))
            time.sleep(RETRY_GAP_SECONDS)

    # Every attempt failed. Only now is silence worth breaking.
    emit("FAILED exit=%d after %d attempts" % (last_rc, ATTEMPTS))
    label = "TIMED OUT" if last_rc == 124 else "FAILED"
    send_failure_email(
        "\u26a0\ufe0f Interview scan %s - %s" % (label, run_ts),
        "=== interview-scan %s ===\n"
        "scan_gmail_interviews.sh did not finish on any of %d attempts (last exit code %d).\n"
        "No success mail != no news today; the scan itself broke.\n"
        "Retry gap %d min, all attempts failed. Next scan waits for the next scheduled slot.\n\n"
        "Last output:\n%s\n\nlog: %s"
        % (run_ts, ATTEMPTS, last_rc, RETRY_GAP_SECONDS // 60,
           last_output[-TAIL_CHARS:] or "(no output)",
           os.path.join(BASE_DIR, "logs", "launchd.log")))
    if not launchd_is_capturing():
        mirror_to_log("\n".join(_transcript))
    return last_rc


if __name__ == "__main__":
    sys.exit(main())
