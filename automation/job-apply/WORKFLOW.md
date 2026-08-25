# How the Job Application Helper Works

```mermaid
flowchart TD
    A["📋 You give me<br/>a job link"] --> B{"Have I done this<br/>company+platform<br/>before? (SOP exists?)"}
    B -->|yes| C["📖 Follow my<br/>saved steps"]
    B -->|no| D["🔍 Explore the form<br/>fresh, page by page"]

    C --> E["🌐 Open the form<br/>in a browser"]
    D --> E

    E --> F{"Logged in?"}
    F -->|no| G["🔑 You log in"]
    F -->|yes| H
    G --> H{"Saved profile<br/>available?"}
    H -->|yes| I["Reuse last<br/>saved application"]
    H -->|no| J["Fill fresh from<br/>your resume"]

    I --> K["✍️ I fill in each<br/>page of the form"]
    J --> K

    K --> L["👀 I show you the<br/>whole filled form"]
    L --> M{"auto_submit_config:<br/>this company set<br/>to auto?"}
    M -->|no, default| N["Wait for your<br/>explicit yes"]
    M -->|yes| O["Skip the wait"]
    N --> P
    O --> P["🚀 Click Submit"]

    P --> Q{"Blocked by a<br/>safety check?"}
    Q -->|yes| R["✋ You click it<br/>yourself"]
    Q -->|no| S["✅ Confirm it<br/>really went through"]

    S --> T{"First time on this<br/>company+platform?"}
    T -->|yes| U["📝 Save the steps<br/>for next time"]
    T -->|no, found something new| V["📝 Update the<br/>saved steps"]
    T -->|no, nothing changed| W["🎉 Done"]
    U --> W
    V --> W
```

## What gets touched

```
~/.job-apply-sessions/<job-id>/     ← throwaway browser session for this one run
automation/job-apply/
└── _internal/                       ← not meant for casual reading — engine + data
    ├── playwright_script.py            ← the engine that drives the browser
    ├── applicant_profile.json          ← your info (read, not written)
    ├── auto_submit_config.json         ← per-company auto vs. ask-first (read)
    └── sop/<company>-<platform>.md     ← saved steps for that company's form
                                            (written the first time, reused after)
```

Nothing gets submitted without you seeing the filled form first — unless that specific company is explicitly flagged `auto` in `auto_submit_config.json`, and even then you still get shown what was sent, right after.

Reorganized 2026-08-17: everything the agent reads/writes moved into `_internal/`; only this WORKFLOW.md stays at the top level.
