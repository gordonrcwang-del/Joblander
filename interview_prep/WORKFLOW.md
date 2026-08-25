# How the Interview Prep Helper Works

```mermaid
flowchart TD
    A["🏢 You give me a<br/>company + role"] --> B{"Company folder<br/>already exists?"}
    B -->|yes| C["Reuse it"]
    B -->|no| D["Create /Company/"]
    C --> E["Create /Company/Position/"]
    D --> E

    E --> F{"company_brief.md<br/>exists?"}
    F -->|yes| G["Skip — reuse"]
    F -->|no| H["🔍 Research company<br/>→ write company_brief.md"]

    G --> I
    H --> I{"position_intro.md<br/>exists?"}
    I -->|yes| J["Skip — reuse"]
    I -->|no| K{"Job link in<br/>my job tracker?"}
    K -->|yes| L["Fetch the posting"]
    K -->|no| M["Ask you for<br/>the JD text"]
    L --> N["💬 Check Dcard/PTT/104<br/>for what people say"]
    M --> N
    N --> O["📄 write position_intro.md"]

    J --> P
    O --> P{"模擬面試_QA.md<br/>exists?"}
    P -->|yes| Q["Skip — reuse"]
    P -->|no| R["❓ write 模擬面試_QA.md<br/>(practice Q&A)"]

    Q --> S
    R --> S{"基本知識.md<br/>exists?"}
    S -->|yes| T["Skip — reuse"]
    S -->|no| U["📚 write 基本知識.md<br/>(background knowledge)"]

    T --> V["✅ Report back:<br/>which files were made<br/>vs. already existed"]
    U --> V
```

## What gets created

```
interview_prep/
└── <Company>/
    ├── company_brief.md          ← what the company does (once per company)
    └── <Position>/
        ├── position_intro.md     ← the job itself + forum sentiment
        ├── 模擬面試_QA.md         ← practice interview Q&A
        └── 基本知識.md            ← background knowledge cheat sheet
```

Each file is written once and reused after that — re-running the skill on the same company/role never overwrites existing files unless you explicitly ask for a refresh.
