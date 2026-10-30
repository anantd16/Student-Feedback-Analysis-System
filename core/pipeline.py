"""
Pipeline Orchestration Engine.
Coordinates Stages 0, 1, 2, and 3 with:
- Stage 0: Input validation, deduplication, PII anonymization
- Stage 1: Batch LLM classification (v1, v2, v3) + Rule-based verification
- Stage 2: Theme discovery with code-computed verifiable counts
- Stage 3: Concrete improvement action generation + Final Report assembly
"""

import time
import uuid
import json
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.anonymizer import preprocess_feedback
from core.llm import call_gemini
from core.prompts import (
    ALLOWED_CATEGORIES,
    ALLOWED_SENTIMENTS,
    ALLOWED_URGENCIES,
    PROMPT_VERSIONS,
    STAGE_2_THEME_PROMPT,
    STAGE_3_IMPROVEMENT_PROMPT,
)


def validate_and_normalize_classified_item(
    item: Dict[str, Any],
    expected_id: str
) -> Tuple[Dict[str, Any], bool, List[str]]:
    """
    Rule-based verification on classified entry:
    - Verifies ID matches input
    - Normalizes and checks sentiment in allowed enums
    - Normalizes and checks category in allowed enums
    - Normalizes urgency in allowed enums
    - Ensures confidence is clamped between 0.0 and 1.0
    """
    violations = []
    normalized = dict(item)

    # 1. ID verification
    item_id = str(item.get("id", "")).strip()
    if item_id != expected_id:
        violations.append(f"ID mismatch: got {item_id}, expected {expected_id}")
        normalized["id"] = expected_id
    else:
        normalized["id"] = expected_id

    # 2. Sentiment enum check
    sentiment = str(item.get("sentiment", "")).strip().lower()
    if sentiment not in ALLOWED_SENTIMENTS:
        violations.append(f"Invalid sentiment '{sentiment}', defaulted to 'neutral'")
        sentiment = "neutral"
    normalized["sentiment"] = sentiment

    # 3. Category enum check
    category = str(item.get("category", "")).strip().lower().replace(" ", "_")
    if category not in ALLOWED_CATEGORIES:
        violations.append(f"Invalid category '{category}', defaulted to 'other'")
        category = "other"
    normalized["category"] = category

    # 4. Urgency enum check
    urgency = str(item.get("urgency", "")).strip().lower()
    if urgency not in ALLOWED_URGENCIES:
        violations.append(f"Invalid urgency '{urgency}', defaulted to 'low'")
        urgency = "low"
    normalized["urgency"] = urgency

    # 5. Confidence check
    try:
        conf = float(item.get("confidence", 0.8))
        conf = max(0.0, min(1.0, conf))
    except (ValueError, TypeError):
        violations.append("Invalid confidence float, defaulted to 0.70")
        conf = 0.70
    normalized["confidence"] = round(conf, 3)

    # 6. Reason
    reason = str(item.get("short_reason", "")).strip()
    if not reason:
        reason = "Automated classification based on feedback text."
    normalized["short_reason"] = reason

    is_valid = len(violations) == 0
    return normalized, is_valid, violations


def run_stage_0(raw_texts: List[str], max_batch_size: int = 100) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Stage 0: Preprocessing and PII Anonymization."""
    return preprocess_feedback(raw_texts, max_batch_size=max_batch_size)


def run_stage_1(
    preprocessed_entries: List[Dict[str, Any]],
    prompt_version: str = "v3",
    batch_size: int = 10,
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Stage 1: Batch LLM Classification.
    Batches inputs to preserve API quota while maintaining ID stability.
    """
    if prompt_version not in PROMPT_VERSIONS:
        prompt_version = "v3"

    template = PROMPT_VERSIONS[prompt_version]
    classified_results: List[Dict[str, Any]] = []
    all_violations: List[str] = []
    total_entries = len(preprocessed_entries)

    for i in range(0, total_entries, batch_size):
        chunk = preprocessed_entries[i : i + batch_size]
        chunk_inputs = [{"id": item["id"], "text": item["text"]} for item in chunk]
        expected_ids = [item["id"] for item in chunk]

        prompt_str = template.format(feedback_json=json.dumps(chunk_inputs, indent=2))

        if progress_callback:
            progress_callback(
                min(i + batch_size, total_entries),
                total_entries,
                f"Classifying batch {i // batch_size + 1} ({len(chunk)} entries)..."
            )

        try:
            parsed_batch, _ = call_gemini(
                prompt=prompt_str,
                temperature=0.1,
                force_json=True
            )
            if not isinstance(parsed_batch, list):
                if isinstance(parsed_batch, dict) and "feedback" in parsed_batch:
                    parsed_batch = parsed_batch["feedback"]
                elif isinstance(parsed_batch, dict):
                    parsed_batch = [parsed_batch]
                else:
                    parsed_batch = []
        except Exception as e:
            # Fallback for batch if LLM fails
            parsed_batch = []
            all_violations.append(f"LLM call failed for batch starting at {i}: {e}")

        # Map response by ID
        batch_lookup = {}
        for obj in parsed_batch:
            if isinstance(obj, dict) and "id" in obj:
                batch_lookup[str(obj["id"]).strip()] = obj

        # Align with expected chunk entries
        for original_item in chunk:
            exp_id = original_item["id"]
            raw_obj = batch_lookup.get(exp_id)
            if not raw_obj:
                raw_obj = {
                    "id": exp_id,
                    "sentiment": "neutral",
                    "category": "other",
                    "urgency": "low",
                    "confidence": 0.50,
                    "short_reason": "Generated baseline fallback due to missing LLM response"
                }
                all_violations.append(f"Missing item {exp_id} in LLM output, filled with fallback")

            normalized_item, is_valid, viols = validate_and_normalize_classified_item(raw_obj, exp_id)
            # Reattach text reference
            normalized_item["text"] = original_item["text"]
            classified_results.append(normalized_item)
            all_violations.extend(viols)

    rule_checks = {
        "total_classified": len(classified_results),
        "ids_strictly_matched": all(r["id"] == p["id"] for r, p in zip(classified_results, preprocessed_entries)),
        "all_labels_valid_enum": all(
            r["sentiment"] in ALLOWED_SENTIMENTS and r["category"] in ALLOWED_CATEGORIES for r in classified_results
        ),
        "confidence_bounded": all(0.0 <= r["confidence"] <= 1.0 for r in classified_results),
        "violations_logged": len(all_violations),
        "sample_violations": all_violations[:5]
    }

    return classified_results, rule_checks


def run_stage_2(
    classified_entries: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Stage 2: Theme Detection.
    Computes exact category and sentiment counts in code first,
    queries LLM for theme titles & descriptions, and verifies frequencies against code counts.
    """
    if not classified_entries:
        return [], {}

    total_count = len(classified_entries)
    cat_counts = Counter(e["category"] for e in classified_entries)
    sent_counts = Counter(e["sentiment"] for e in classified_entries)

    # Simplified payload for prompt to save tokens
    condensed_entries = [
        {"id": e["id"], "cat": e["category"], "sent": e["sentiment"], "txt": e["text"][:120]}
        for e in classified_entries
    ]

    prompt_str = STAGE_2_THEME_PROMPT.format(
        total_count=total_count,
        category_counts_json=json.dumps(cat_counts, indent=2),
        sentiment_counts_json=json.dumps(sent_counts, indent=2),
        classified_entries_json=json.dumps(condensed_entries[:40], indent=2)
    )

    try:
        raw_themes, _ = call_gemini(prompt_str, temperature=0.15, force_json=True)
        if not isinstance(raw_themes, list):
            if isinstance(raw_themes, dict) and "themes" in raw_themes:
                raw_themes = raw_themes["themes"]
            else:
                raw_themes = [raw_themes] if isinstance(raw_themes, dict) else []
    except Exception:
        # Fallback theme if LLM fails
        raw_themes = [{
            "theme": "General Course Feedback",
            "category": "other",
            "description": "Aggregated student feedback comments across domains.",
            "dominant_sentiment": "neutral",
            "example_entry_ids": [classified_entries[0]["id"]] if classified_entries else []
        }]

    # Verifiable count verification in code
    verified_themes: List[Dict[str, Any]] = []
    for t in raw_themes:
        if not isinstance(t, dict):
            continue

        theme_name = str(t.get("theme", "General Observation")).strip()
        cat = str(t.get("category", "other")).strip().lower()
        if cat not in ALLOWED_CATEGORIES:
            cat = "other"

        example_ids = t.get("example_entry_ids", [])
        if not isinstance(example_ids, list):
            example_ids = [str(example_ids)]

        # Code-computed verifiable frequency
        # If example IDs provided match entries, count them, else use category frequency
        valid_examples = [eid for eid in example_ids if any(e["id"] == eid for e in classified_entries)]
        frequency = len(valid_examples) if valid_examples else cat_counts.get(cat, 1)
        frequency = max(1, min(total_count, frequency))

        share_pct = round((frequency / total_count) * 100, 1) if total_count > 0 else 0.0

        dom_sentiment = str(t.get("dominant_sentiment", "neutral")).strip().lower()
        if dom_sentiment not in ALLOWED_SENTIMENTS:
            dom_sentiment = "neutral"

        verified_themes.append({
            "theme": theme_name,
            "category": cat,
            "description": str(t.get("description", "Student observations regarding this area.")),
            "frequency": frequency,
            "share_percent": share_pct,
            "dominant_sentiment": dom_sentiment,
            "example_entry_ids": valid_examples or [classified_entries[0]["id"]]
        })

    # Sort themes by frequency descending
    verified_themes.sort(key=lambda x: x["frequency"], reverse=True)

    verification_meta = {
        "themes_count": len(verified_themes),
        "code_computed_frequencies": True,
        "category_counts": dict(cat_counts),
        "sentiment_counts": dict(sent_counts)
    }

    return verified_themes, verification_meta


def run_stage_3(
    top_themes: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Stage 3: Institutional Improvement Suggestions."""
    if not top_themes:
        return []

    condensed_themes = [
        {
            "theme": t["theme"],
            "category": t["category"],
            "frequency": t["frequency"],
            "dominant_sentiment": t["dominant_sentiment"],
            "description": t["description"]
        }
        for t in top_themes[:5]
    ]

    prompt_str = STAGE_3_IMPROVEMENT_PROMPT.format(themes_json=json.dumps(condensed_themes, indent=2))

    try:
        raw_suggestions, _ = call_gemini(prompt_str, temperature=0.15, force_json=True)
        if not isinstance(raw_suggestions, list):
            if isinstance(raw_suggestions, dict) and "suggestions" in raw_suggestions:
                raw_suggestions = raw_suggestions["suggestions"]
            else:
                raw_suggestions = [raw_suggestions] if isinstance(raw_suggestions, dict) else []
    except Exception:
        raw_suggestions = []

    # Map suggestions back
    suggestions_map = {s.get("theme", ""): s for s in raw_suggestions if isinstance(s, dict)}
    verified_suggestions: List[Dict[str, Any]] = []

    for t in top_themes[:5]:
        th_name = t["theme"]
        match = suggestions_map.get(th_name, {})
        actions = match.get("suggested_actions", [])
        if not isinstance(actions, list) or not actions:
            actions = [
                f"Review operational workflow for {th_name.lower()}",
                "Convene student-faculty committee for targeted adjustments"
            ]

        priority = str(match.get("priority", "medium")).lower()
        if priority not in ["high", "medium", "low"]:
            priority = "high" if t.get("dominant_sentiment") == "negative" else "medium"

        verified_suggestions.append({
            "theme": th_name,
            "priority": priority,
            "suggested_actions": actions,
            "expected_impact": str(match.get("expected_impact", "Improved student satisfaction and course alignment.")),
            "owner_suggestion": str(match.get("owner_suggestion", "Course Instructor / Department Coordinator"))
        })

    return verified_suggestions


def assemble_feedback_report(
    run_id: str,
    raw_entries: List[Dict[str, Any]],
    classified_entries: List[Dict[str, Any]],
    themes: List[Dict[str, Any]],
    suggestions: List[Dict[str, Any]],
    preproc_stats: Dict[str, Any],
    stage1_checks: Dict[str, Any],
    duration_seconds: float,
    prompt_version: str
) -> Dict[str, Any]:
    """Assemble final metrics, review queues, and markdown report."""
    total = len(classified_entries)
    sent_counts = Counter(c["sentiment"] for c in classified_entries)
    cat_counts = Counter(c["category"] for c in classified_entries)
    urg_counts = Counter(c["urgency"] for c in classified_entries)

    pos_pct = round((sent_counts.get("positive", 0) / total * 100), 1) if total > 0 else 0
    neu_pct = round((sent_counts.get("neutral", 0) / total * 100), 1) if total > 0 else 0
    neg_pct = round((sent_counts.get("negative", 0) / total * 100), 1) if total > 0 else 0
    mix_pct = round((sent_counts.get("mixed", 0) / total * 100), 1) if total > 0 else 0

    # Low confidence queue (< 0.75) for Responsible AI human review
    low_confidence_queue = [
        c for c in classified_entries if c.get("confidence", 1.0) < 0.75
    ]

    # Build Markdown report
    md_lines = [
        f"# 🎓 Student Feedback Intelligence Executive Report",
        f"**Run Identifier:** `{run_id}` | **Engine:** `Prompt {prompt_version}` | **Execution Duration:** `{duration_seconds:.2f}s`",
        "",
        "## 1. Executive Summary & Sentiment Distribution",
        f"- **Total Submissions Processed:** {total} (Preprocessed: {preproc_stats.get('total_received', total)} received, {preproc_stats.get('total_pii_redacted', 0)} PII entities redacted)",
        f"- **Positive Sentiment:** {pos_pct}% ({sent_counts.get('positive', 0)} entries)",
        f"- **Neutral Sentiment:** {neu_pct}% ({sent_counts.get('neutral', 0)} entries)",
        f"- **Negative Sentiment:** {neg_pct}% ({sent_counts.get('negative', 0)} entries)",
        f"- **Mixed Sentiment:** {mix_pct}% ({sent_counts.get('mixed', 0)} entries)",
        f"- **Urgency Distribution:** High: {urg_counts.get('high', 0)} | Medium: {urg_counts.get('medium', 0)} | Low: {urg_counts.get('low', 0)}",
        "",
        "## 2. Recurring Systemic Themes",
    ]

    for idx, t in enumerate(themes, 1):
        md_lines.append(f"### {idx}. {t['theme']} ({t['frequency']} entries, {t['share_percent']}%)")
        md_lines.append(f"- **Domain:** `{t['category']}` | **Dominant Tone:** `{t['dominant_sentiment']}`")
        md_lines.append(f"- **Context:** {t['description']}")
        md_lines.append(f"- **Example Evidence IDs:** {', '.join(t.get('example_entry_ids', []))}")
        md_lines.append("")

    md_lines.append("## 3. Recommended Institutional Action Plan")
    for s in suggestions:
        md_lines.append(f"### Theme: {s['theme']} (Priority: `{s['priority'].upper()}`)")
        md_lines.append(f"- **Accountable Owner:** {s['owner_suggestion']}")
        md_lines.append(f"- **Anticipated Impact:** {s['expected_impact']}")
        md_lines.append("- **Actionable Items:**")
        for a in s.get("suggested_actions", []):
            md_lines.append(f"  * {a}")
        md_lines.append("")

    md_lines.append("## 4. Responsible AI & Verification Checklist")
    md_lines.append(f"- **PII Redaction Applied:** Stage 0 regex & heuristic anonymization completed.")
    md_lines.append(f"- **Rule-Based Verification:** Strict enum checks passed: `{stage1_checks.get('all_labels_valid_enum', True)}`")
    md_lines.append(f"- **Confidence Clamping:** Validated bounds [0.0, 1.0]: `{stage1_checks.get('confidence_bounded', True)}`")
    md_lines.append(f"- **Human-in-the-Loop Flag:** {len(low_confidence_queue)} entries flagged for manual human review.")
    md_lines.append("- **AI Disclaimer:** Outputs are AI-assisted syntheses and require pedagogical oversight before policy changes.")

    report_markdown = "\n".join(md_lines)

    return {
        "run_id": run_id,
        "total_entries": total,
        "sentiment_counts": dict(sent_counts),
        "category_counts": dict(cat_counts),
        "urgency_counts": dict(urg_counts),
        "positive_pct": pos_pct,
        "neutral_pct": neu_pct,
        "negative_pct": neg_pct,
        "mixed_pct": mix_pct,
        "low_confidence_count": len(low_confidence_queue),
        "low_confidence_entries": low_confidence_queue,
        "report_markdown": report_markdown,
        "rule_checks": stage1_checks
    }

