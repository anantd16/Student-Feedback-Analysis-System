"""
PII Anonymization and Input Preprocessing Module
Stage 0 of Student Feedback Analysis System.
Complies with Responsible AI practices: never stores raw PII, redacts
names, emails, phone numbers, and student roll IDs before sending to LLMs.
"""

import re
from typing import Dict, List, Tuple, Any


# Standard regex patterns for sensitive identifiers
EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    re.IGNORECASE
)

PHONE_PATTERN = re.compile(
    r"(?:(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}|\b\d{10}\b)"
)

ROLL_ID_PATTERN = re.compile(
    r"\b(?:ROLL(?:\s*NO\.?|\s*NUM(?:BER)?)?|REG(?:\s*NO\.?|\s*NUM(?:BER)?)?|ID|STUDENT\s*ID)[\s:#-]*([A-Za-z0-9_-]{4,15})\b",
    re.IGNORECASE
)

STUDENT_CODE_PATTERN = re.compile(
    r"\b[0-9]{2}[A-Z]{2,4}[0-9]{3,5}\b|\bS[0-9]{6,8}\b",
    re.IGNORECASE
)

# Name heuristics: titles + common introduction markers
TITLE_NAME_PATTERN = re.compile(
    r"\b(?:Prof\.?|Dr\.?|Mr\.?|Mrs\.?|Ms\.?)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b"
)

INTRO_NAME_PATTERN = re.compile(
    r"\b(?:I am|My name is|This is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
    re.IGNORECASE
)


def anonymize_text(text: str) -> Tuple[str, Dict[str, int]]:
    """
    Anonymize PII from a single text entry.
    Returns:
        (sanitized_text, redactions_count_dict)
    """
    if not text:
        return "", {"email": 0, "phone": 0, "id": 0, "name": 0}

    redactions = {"email": 0, "phone": 0, "id": 0, "name": 0}

    # Redact Emails
    def _sub_email(m):
        redactions["email"] += 1
        return "[REDACTED_EMAIL]"
    sanitized = EMAIL_PATTERN.sub(_sub_email, text)

    # Redact Phone Numbers
    def _sub_phone(m):
        # Avoid matching short 4-digit years like 2024 or standalone small numbers
        matched_str = m.group(0).strip()
        digits_only = re.sub(r"\D", "", matched_str)
        if len(digits_only) >= 7:
            redactions["phone"] += 1
            return "[REDACTED_PHONE]"
        return matched_str
    sanitized = PHONE_PATTERN.sub(_sub_phone, sanitized)

    # Redact Student Roll / Reg IDs
    def _sub_roll(m):
        redactions["id"] += 1
        return "[REDACTED_ID]"
    sanitized = ROLL_ID_PATTERN.sub(_sub_roll, sanitized)
    sanitized = STUDENT_CODE_PATTERN.sub(_sub_roll, sanitized)

    # Redact Names via Heuristics
    def _sub_title(m):
        redactions["name"] += 1
        return "[REDACTED_NAME]"
    sanitized = TITLE_NAME_PATTERN.sub(_sub_title, sanitized)

    def _sub_intro(m):
        redactions["name"] += 1
        return "I am [REDACTED_NAME]"
    sanitized = INTRO_NAME_PATTERN.sub(_sub_intro, sanitized)

    return sanitized, redactions


def preprocess_feedback(
    raw_entries: List[str],
    max_batch_size: int = 100
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Preprocess raw student feedback entries:
    - Trims whitespace
    - Drops empty or null rows
    - Deduplicates entries while tracking duplicate count
    - Caps batch size at max_batch_size
    - Anonymizes PII and logs redaction statistics
    """
    total_received = len(raw_entries)
    processed_items: List[Dict[str, Any]] = []
    seen_texts = set()
    duplicates_count = 0
    empty_count = 0
    total_redactions = {"email": 0, "phone": 0, "id": 0, "name": 0}

    entry_idx = 1
    for raw in raw_entries:
        if raw is None:
            empty_count += 1
            continue

        trimmed = str(raw).strip()
        if not trimmed:
            empty_count += 1
            continue

        # Check duplicate
        normalized = trimmed.lower()
        if normalized in seen_texts:
            duplicates_count += 1
            continue
        seen_texts.add(normalized)

        # Batch size cap check
        if len(processed_items) >= max_batch_size:
            break

        # Anonymize
        sanitized, red_stats = anonymize_text(trimmed)
        for k, v in red_stats.items():
            total_redactions[k] += v

        item = {
            "id": f"FB-{entry_idx:03d}",
            "original_text": trimmed,
            "text": sanitized,
            "redaction_count": sum(red_stats.values()),
            "redactions": red_stats
        }
        processed_items.append(item)
        entry_idx += 1

    summary_stats = {
        "total_received": total_received,
        "valid_entries": len(processed_items),
        "empty_dropped": empty_count,
        "duplicates_dropped": duplicates_count,
        "batch_capped": total_received > max_batch_size,
        "max_batch_size": max_batch_size,
        "total_redactions": total_redactions,
        "total_pii_redacted": sum(total_redactions.values())
    }

    return processed_items, summary_stats

