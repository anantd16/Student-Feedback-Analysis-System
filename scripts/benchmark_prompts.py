"""
Benchmark Script to evaluate Prompt v1, v2, and v3 on Gold Labels.
Saves empirical accuracy and per-class metrics to data/benchmark_results.json.
"""

import sys
import os
import json
import time
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.pipeline import run_stage_0, run_stage_1
from core.evaluation import evaluate_against_gold_labels
from core.storage import FeedbackStorageManager


def run_benchmark():
    print("=" * 70)
    print("📊 BENCHMARKING PROMPT VERSIONS v1 vs v2 vs v3 ON GOLD LABELS")
    print("=" * 70)

    sample_df = pd.read_csv("data/sample_feedback.csv")
    gold_df = pd.read_csv("data/gold_labels.csv")

    # Map sample texts
    sample_map = dict(zip(sample_df["id"], sample_df["feedback"]))

    # Prepare inputs for gold label IDs
    gold_ids = gold_df["id"].tolist()
    gold_inputs = [{"id": gid, "text": sample_map[gid]} for gid in gold_ids if gid in sample_map]

    print(f"Total benchmark entries: {len(gold_inputs)}")

    storage = FeedbackStorageManager()
    benchmark_results = {}

    for version in ["v1", "v2", "v3"]:
        print(f"\nEvaluating Prompt {version}...")
        start_t = time.time()
        # Stage 0
        raw_texts = [item["text"] for item in gold_inputs]
        preproc, _ = run_stage_0(raw_texts)
        # Restore original IDs
        for p, g in zip(preproc, gold_inputs):
            p["id"] = g["id"]

        classified, _ = run_stage_1(preproc, prompt_version=version, batch_size=10)
        elapsed = round(time.time() - start_t, 2)

        eval_res = evaluate_against_gold_labels(classified, gold_df)
        sent_metrics = eval_res["sentiment_metrics"]
        cat_metrics = eval_res["category_metrics"]

        print(f"[{version}] Overall Accuracy: {eval_res['overall_accuracy'] * 100:.1f}%")
        print(f"[{version}] Sentiment Acc: {sent_metrics['accuracy'] * 100:.1f}%, F1: {sent_metrics['f1_macro']:.3f}")
        print(f"[{version}] Category Acc: {cat_metrics['accuracy'] * 100:.1f}%, F1: {cat_metrics['f1_macro']:.3f}")
        print(f"[{version}] Execution Time: {elapsed}s")

        benchmark_results[version] = {
            "overall_accuracy": eval_res["overall_accuracy"],
            "sentiment_accuracy": sent_metrics["accuracy"],
            "sentiment_f1": sent_metrics["f1_macro"],
            "category_accuracy": cat_metrics["accuracy"],
            "category_f1": cat_metrics["f1_macro"],
            "elapsed_seconds": elapsed,
            "sentiment_confusion_matrix": sent_metrics["confusion_matrix"],
            "category_confusion_matrix": cat_metrics["confusion_matrix"],
            "per_class_sentiment": sent_metrics["per_class"],
            "per_class_category": cat_metrics["per_class"],
            "matched_count": eval_res["matched_count"]
        }

        # Save to storage evaluation tab
        storage.save_evaluation(
            prompt_version=version,
            model="gemini-3.5-flash-lite",
            accuracy=eval_res["overall_accuracy"],
            precision_macro=sent_metrics["precision_macro"],
            recall_macro=sent_metrics["recall_macro"],
            f1_macro=sent_metrics["f1_macro"],
            tested_count=eval_res["matched_count"],
            details={
                "sentiment_accuracy": sent_metrics["accuracy"],
                "category_accuracy": cat_metrics["accuracy"]
            }
        )

    # Save benchmark cache
    out_file = "data/benchmark_results.json"
    with open(out_file, "w") as f:
        json.dump(benchmark_results, f, indent=2)

    print(f"\nSaved benchmark results to {out_file}")
    return benchmark_results


if __name__ == "__main__":
    run_benchmark()

