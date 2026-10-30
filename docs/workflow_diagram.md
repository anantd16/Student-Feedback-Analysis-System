# System Workflow Diagram

This document illustrates the end-to-end architecture and autonomous trigger-action execution pipeline of the **Student Feedback Analysis System**.

```mermaid
flowchart TD
    %% User Inputs & Ingestion
    subgraph S_INPUT["1. Feedback Ingestion Layer"]
        A1["Typed Text Input"]
        A2["Batch Pasted Text"]
        A3["CSV File Upload ('feedback' column)"]
        A4["Pre-loaded Sample Dataset (50 items)"]
        UI_SELECT["Select Prompt Version: v1 / v2 / v3"]
        BTN_RUN["User clicks 'Run Analysis' Trigger"]
    end

    A1 --> BTN_RUN
    A2 --> BTN_RUN
    A3 --> BTN_RUN
    A4 --> BTN_RUN
    UI_SELECT --> BTN_RUN

    %% Stage 0: Preprocessing & Anonymization
    subgraph S_STAGE0["2. Stage 0: Sanitization & Responsible AI"]
        S0_CLEAN["Whitespace Trimming & Empty Row Dropping"]
        S0_DEDUP["Deduplication Engine (tracks duplicates)"]
        S0_CAP["Batch Capping (<= 100 entries)"]
        S0_ANON["PII Regex & Heuristic Redaction:<br/>- Student Roll / Reg IDs<br/>- Personal Emails<br/>- Phone Numbers<br/>- Student / Faculty Names"]
    end

    BTN_RUN --> S0_CLEAN --> S0_DEDUP --> S0_CAP --> S0_ANON

    %% Stage 1: Classification & Verification
    subgraph S_STAGE1["3. Stage 1: Multi-Dimensional Classification"]
        S1_BATCH["Batch Generator (10 entries/call)"]
        S1_LLM["Gemini 3.5 Flash Model<br/>(Temperature: 0.1)"]
        S1_RULES["Rule-Based Verification Engine:<br/>- Stable ID matching<br/>- Strict Enums (sentiment, category, urgency)<br/>- Confidence Clamping [0.0, 1.0]<br/>- Markdown strip & JSON Repair"]
        S1_FLAGS["Human-in-the-Loop Queue<br/>(Confidence < 0.75)"]
    end

    S0_ANON --> S1_BATCH --> S1_LLM --> S1_RULES
    S1_RULES --> S1_FLAGS

    %% Stage 2: Theme Detection
    subgraph S_STAGE2["4. Stage 2: Systemic Theme Discovery"]
        S2_COUNT["Code-Computed Ground Truth:<br/>Exact Category & Sentiment Frequencies"]
        S2_LLM["LLM Theme Synthesis:<br/>Title, Descriptive Context, Example IDs"]
        S2_VERIF["Frequency Verifier:<br/>Binds theme counts to verifiable data"]
    end

    S1_RULES --> S2_COUNT --> S2_LLM --> S2_VERIF

    %% Stage 3: Institutional Improvement Planning
    subgraph S_STAGE3["5. Stage 3: Actionable Improvement Planning"]
        S3_LLM["Pedagogical Consultant LLM Prompt"]
        S3_ACTIONS["Action Synthesis:<br/>- Priority (High/Medium/Low)<br/>- 2-3 Concrete Interventions<br/>- Measurable Expected Impact<br/>- Suggested Institutional Owner"]
        S3_REPORT["Executive Report Generator<br/>(Markdown + HTML + Metrics)"]
    end

    S2_VERIF --> S3_LLM --> S3_ACTIONS --> S3_REPORT

    %% Storage & Automated Trigger-Actions
    subgraph S_PERSIST["6. Persistence & Automated Trigger Actions"]
        STORAGE_ROUTER{"Google Sheets<br/>Configured?"}
        GS_BACKEND["Google Sheets API (gspread):<br/>- Raw_Feedback<br/>- Classified<br/>- Themes<br/>- Suggestions<br/>- Report<br/>- Evaluation<br/>- Run_Log"]
        LOCAL_BACKEND["Local SQLite Storage Fallback:<br/>- data/local_storage.db<br/>- Visible UI Banner"]
        SMTP_CHECK{"Gmail SMTP<br/>Configured?"}
        EMAIL_ACTION["Automated Notification Email<br/>to Academic Dean / Lead"]
        RUN_LOG["Run_Log Record Written:<br/>run_id, duration, status, targets"]
    end

    S3_REPORT --> STORAGE_ROUTER
    STORAGE_ROUTER -->|Yes| GS_BACKEND
    STORAGE_ROUTER -->|No / Failed| LOCAL_BACKEND
    GS_BACKEND --> RUN_LOG
    LOCAL_BACKEND --> RUN_LOG

    S3_REPORT --> SMTP_CHECK
    SMTP_CHECK -->|Yes| EMAIL_ACTION
    SMTP_CHECK -->|No| RUN_LOG
    EMAIL_ACTION --> RUN_LOG

    %% UI Presentation Layer
    subgraph S_UI["7. User Interface (Streamlit)"]
        UI_TRACKER["Live Stage Progress Tracker (Stages 0 -> 3)"]
        UI_METRICS["Summary KPI Cards (Totals, %, Urgency)"]
        UI_CHARTS["Interactive Plotly Charts (Sentiment & Categories)"]
        UI_THEMES["Theme Matrix & Improvement Action Table"]
        UI_EVAL["Benchmark & Confusion Matrix View"]
        UI_LOGS["Live Run_Log Table & Google Sheet Link"]
    end

    RUN_LOG --> UI_TRACKER
    RUN_LOG --> UI_METRICS
    RUN_LOG --> UI_CHARTS
    RUN_LOG --> UI_THEMES
    RUN_LOG --> UI_LOGS
```

## Stage Descriptions

1. **Stage 0 (Ingestion & Anonymization)**:
   Raw student inputs are trimmed, empty rows dropped, and duplicates filtered. A deterministic regex and heuristic pipeline redacts emails, phone numbers, student roll IDs, and personal names. Raw personal data is never transmitted to the LLM.

2. **Stage 1 (Classification)**:
   Preprocessed feedback is batched in groups of 10 to optimize token throughput and latency. One of three prompt versions (`v1 Baseline`, `v2 Structured`, `v3 Few-Shot + Strict JSON`) classifies each entry into sentiment, category, urgency, and confidence. Rule-based checks verify enum values and JSON formatting. Entries with confidence $< 0.75$ are placed into a Human-in-the-Loop review queue.

3. **Stage 2 (Theme Discovery)**:
   Frequency counts are computed directly in Python code from classified entries. The LLM receives verifiable counts and identifies 3 to 6 overarching institutional themes, attaching representative feedback IDs. Theme frequencies are mathematically bounded by code.

4. **Stage 3 (Improvement Planning & Report)**:
   For each identified theme, the system generates 2 to 3 concrete interventions, priority ratings, expected impacts, and assigns responsible institutional owners. An executive report is compiled in Markdown and HTML.

5. **Automation & Persistence**:
   Trigger-action execution persists raw inputs, classified entries, recurring themes, suggestions, report summaries, and evaluation runs into Google Sheets (or the local SQLite fallback). If Gmail SMTP is configured, an executive summary email is dispatched automatically. Every execution appends a verifiable entry to `Run_Log`.

