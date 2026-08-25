#!/usr/bin/env python3
"""Send the interview-scan's result summary as an email, using the same
Gmail App Password (macOS Keychain) already set up for the job-search scan's
email notification. No separate setup needed — same account, same entry.

The body is sent verbatim. In particular the ``` fence the agent wraps its
report in is DELIBERATE and stays (user's call, 2026-08-24): it delimits one
run's report from the surrounding text in both the mail and launchd.log. A
strip_code_fences() helper briefly lived here and was removed — don't add it
back thinking the fences are a formatting leak.

Usage: python3 send_email_notification.py "<subject>" "<body>"
"""
import os
import re
import subprocess
import smtplib
import sys
import time
from email.mime.text import MIMEText

def _load_config():
    """Read config.json from the repo root. See config.example.json."""
    import json
    root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "..", ".."))
    path = os.path.join(root, "config.json")
    if not os.path.exists(path):
        sys.exit("config.json not found at %s — copy config.example.json to "
                 "config.json and fill it in (see docs/SETUP.md)." % path)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


_CONFIG = _load_config()
GMAIL_ADDRESS = _CONFIG["gmail_address"]
GMAIL_APP_PASSWORD_KEYCHAIN_SERVICE = _CONFIG.get(
    "gmail_app_password_keychain_service", "job-scan-smtp-app-password")

# run_scan.py exports this so the subject line can carry the run's real start
# time — the same string it writes into the launchd.log header.
RUN_TS_ENV_VAR = "INTERVIEW_SCAN_RUN_TS"
SUBJECT_PREFIX = "Interview scan - "
TS_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}")


def run_timestamp():
    """The run's start time if run_scan.py exported one, else now.

    Falls back to the clock so a hand-started scan still gets a real time."""
    exported = os.environ.get(RUN_TS_ENV_VAR, "").strip()
    if TS_PATTERN.match(exported):
        return exported[:16]          # trim seconds; the subject shows HH:MM
    return time.strftime("%Y-%m-%d %H:%M")


def stamp_subject(subject):
    """Put the real time in the scan's subject line.

    WHY (2026-08-24): prompt.md asks the agent to send
    `Interview scan - <YYYY-MM-DD HH:MM>`, and the agent has no clock — it was
    filling that placeholder with a plausible-looking invention. Real examples:
    a run at 08:33 mailed `Interview scan - 2026-08-24 14:05`, and one at 10:56
    mailed `- 2026-08-24 22:30`. Subjects were therefore useless for ordering
    the mail or matching a message to its launchd.log block.

    The agent cannot get this right, so it is not asked to: it passes the
    literal token `{ts}` and the substitution happens here. The regex branch is
    a net for the older wording, in case a run still invents a time.

    Only subjects starting with `Interview scan - ` are touched, which leaves
    run_scan.py's `⚠️ Interview scan FAILED - <ts>` heartbeat alone — that one
    is stamped in Python and is already correct."""
    if not subject.startswith(SUBJECT_PREFIX):
        return subject
    tail = subject[len(SUBJECT_PREFIX):].strip()
    real = run_timestamp()
    if tail == "{ts}":
        return SUBJECT_PREFIX + real
    if TS_PATTERN.match(tail):
        if tail[:16] != real:
            print("send_email_notification: subject said %r, sending %r" % (tail[:16], real))
        return SUBJECT_PREFIX + real + tail[16:]
    return subject


def send_email_notification(subject, body):
    """Best-effort, must never raise: a missing Keychain entry or a transient
    network failure is swallowed, not raised (this runs unattended)."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password",
             "-a", GMAIL_ADDRESS,
             "-s", GMAIL_APP_PASSWORD_KEYCHAIN_SERVICE,
             "-w"],
            capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            print("send_email_notification: no Keychain entry yet for "
                  "service '%s' — skipping email" % GMAIL_APP_PASSWORD_KEYCHAIN_SERVICE)
            return
        app_password = result.stdout.strip()

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = stamp_subject(subject)
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = GMAIL_ADDRESS

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(GMAIL_ADDRESS, app_password)
            server.send_message(msg)
    except Exception as e:
        print("send_email_notification failed (non-fatal): %s" % e)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: send_email_notification.py \"<subject>\" \"<body>\"")
        sys.exit(1)
    send_email_notification(sys.argv[1], sys.argv[2])
