"""
Reference-Based Evaluation and Metrics Module.
Calculates Accuracy, Precision, Recall, F1 (Macro & Per-Class), and Confusion Matrices
comparing Model classifications (v1, v2, v3) against human Gold Labels.
"""

import json
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd


def compute_classification_metrics(
    predictions: List[str],
    ground_truth: List[str],
    labels: List[str]
) -> Dict[str, Any]:
    """
    Compute Accuracy, Macro Precision, Macro Recall, Macro F1,
    and a Confusion Matrix for a single task dimension.
    """
    total = len(predictions)
    if total == 0:
        return {
            "accuracy": 0.0,
            "precision_macro": 0.0,
            "recall_macro": 0.0,
            "f1_macro": 0.0,
            "per_class": {},
            "confusion_matrix": {}
        }

    # Accuracy
    correct = sum(1 for p, g in zip(predictions, ground_truth) if p == g)
    accuracy = correct / total

    # Initialize Confusion Matrix
    # matrix[actual][predicted]
    conf_matrix = {act: {pred: 0 for pred in labels} for act in labels}
    for pred, actual in zip(predictions, ground_truth):
        p = pred if pred in labels else "other"
        a = actual if actual in labels else "other"
        if a in conf_matrix and p in conf_matrix[a]:
            conf_matrix[a][p] += 1

    # Per-Class Metrics
    per_class = {}
    precisions = []
    recalls = []
    f1s = []

    for label in labels:
        tp = conf_matrix.get(label, {}).get(label, 0)
        # FP: sum of column 'label' across other actual rows
        fp = sum(conf_matrix[a].get(label, 0) for a in labels if a != label)
        # FN: sum of row 'label' across other predicted columns
        fn = sum(conf_matrix[label].get(p, 0) for p in labels if p != label)

        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * prec * rec) / (prec + rec) if (prec + rec) > 0 else 0.0

        support = sum(conf_matrix[label].values())
        per_class[label] = {
            "precision": round(prec, 3),
            "recall": round(rec, 3),
            "f1": round(f1, 3),
            "support": support
        }

        # Include classes present in ground truth for macro average
        if support > 0:
            precisions.append(prec)
            recalls.append(rec)
            f1s.append(f1)

    macro_prec = sum(precisions) / len(precisions) if precisions else 0.0
    macro_rec = sum(recalls) / len(recalls) if recalls else 0.0
    macro_f1 = sum(f1s) / len(f1s) if f1s else 0.0

    return {
        "accuracy": round(accuracy, 4),
        "precision_macro": round(macro_prec, 4),
        "recall_macro": round(macro_rec, 4),
        "f1_macro": round(macro_f1, 4),
        "per_class": per_class,
        "confusion_matrix": conf_matrix
    }


def evaluate_against_gold_labels(
    classified_entries: List[Dict[str, Any]],
    gold_df: pd.DataFrame
) -> Dict[str, Any]:
    """
    Compare classified entries to ground-truth labels.
    Returns composite evaluation results for Sentiment and Category.
    """
    # Create lookup map
    gold_map = {}
    for _, row in gold_df.iterrows():
        entry_id = str(row.get("id", "")).strip()
        gold_map[entry_id] = {
            "sentiment": str(row.get("gold_sentiment", "")).strip().lower(),
            "category": str(row.get("gold_category", "")).strip().lower()
        }

    matched_pairs = []
    for c in classified_entries:
        cid = c.get("id")
        if cid in gold_map:
            matched_pairs.append({
                "id": cid,
                "text": c.get("text", ""),
                "pred_sentiment": c.get("sentiment"),
                "gold_sentiment": gold_map[cid]["sentiment"],
                "pred_category": c.get("category"),
                "gold_category": gold_map[cid]["category"]
            })

    if not matched_pairs:
        return {
            "matched_count": 0,
            "sentiment_metrics": {},
            "category_metrics": {},
            "overall_accuracy": 0.0
        }

    sent_preds = [m["pred_sentiment"] for m in matched_pairs]
    sent_gold = [m["gold_sentiment"] for m in matched_pairs]
    sent_labels = ["positive", "neutral", "negative", "mixed"]

    cat_preds = [m["pred_category"] for m in matched_pairs]
    cat_gold = [m["gold_category"] for m in matched_pairs]
    cat_labels = ["teaching", "assessment", "workload", "facilities", "administration", "course_content", "other"]

    sent_metrics = compute_classification_metrics(sent_preds, sent_gold, sent_labels)
    cat_metrics = compute_classification_metrics(cat_preds, cat_gold, cat_labels)

    # Composite overall accuracy (both correct)
    both_correct = sum(
        1 for m in matched_pairs
        if m["pred_sentiment"] == m["gold_sentiment"] and m["pred_category"] == m["gold_category"]
    )
    overall_accuracy = round(both_correct / len(matched_pairs), 4)

    return {
        "matched_count": len(matched_pairs),
        "overall_accuracy": overall_accuracy,
        "sentiment_metrics": sent_metrics,
        "category_metrics": cat_metrics,
        "matched_pairs": matched_pairs
    }

