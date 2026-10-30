"""
Storage Engine: Google Sheets with Automatic Local SQLite/CSV Fallback.
Guarantees the application never crashes even if cloud credentials are absent or invalid.
Automatically creates required tabs/tables:
- Raw_Feedback
- Classified
- Themes
- Suggestions
- Report
- Evaluation
- Run_Log
"""

import os
import json
import sqlite3
import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


REQUIRED_TABS = [
    "Raw_Feedback",
    "Classified",
    "Themes",
    "Suggestions",
    "Report",
    "Evaluation",
    "Run_Log"
]

TAB_SCHEMAS = {
    "Raw_Feedback": ["run_id", "timestamp", "id", "original_text", "sanitized_text", "redactions_count"],
    "Classified": ["run_id", "timestamp", "id", "sentiment", "category", "urgency", "confidence", "short_reason"],
    "Themes": ["run_id", "timestamp", "theme", "category", "description", "frequency", "share_percent", "dominant_sentiment", "example_entry_ids"],
    "Suggestions": ["run_id", "timestamp", "theme", "priority", "suggested_actions", "expected_impact", "owner_suggestion"],
    "Report": ["run_id", "timestamp", "total_entries", "positive_pct", "neutral_pct", "negative_pct", "mixed_pct", "report_markdown"],
    "Evaluation": ["run_id", "timestamp", "prompt_version", "model", "accuracy", "precision_macro", "recall_macro", "f1_macro", "tested_count", "details_json"],
    "Run_Log": ["run_id", "timestamp", "entries_count", "prompt_version", "stages_completed", "duration_seconds", "status", "error_message", "storage_target", "email_status"]
}


class LocalSQLiteStorage:
    """Fallback local database storage using SQLite."""

    def __init__(self, db_path: str = "data/local_storage.db"):
        self.db_path = db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def _init_tables(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for tab_name, columns in TAB_SCHEMAS.items():
                col_defs = ", ".join([f'"{c}" TEXT' for c in columns])
                cursor.execute(f'CREATE TABLE IF NOT EXISTS "{tab_name}" ({col_defs})')
            conn.commit()

    def append_rows(self, tab_name: str, rows: List[Dict[str, Any]]):
        if not rows:
            return
        columns = TAB_SCHEMAS[tab_name]
        placeholders = ", ".join(["?"] * len(columns))
        col_names = ", ".join([f'"{c}"' for c in columns])
        query = f'INSERT INTO "{tab_name}" ({col_names}) VALUES ({placeholders})'

        with self._get_connection() as conn:
            cursor = conn.cursor()
            for row in rows:
                values = [
                    json.dumps(row.get(col, "")) if isinstance(row.get(col), (dict, list))
                    else str(row.get(col, ""))
                    for col in columns
                ]
                cursor.execute(query, values)
            conn.commit()

    def get_run_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        columns = TAB_SCHEMAS["Run_Log"]
        col_names = ", ".join([f'"{c}"' for c in columns])
        query = f'SELECT {col_names} FROM "Run_Log" ORDER BY rowid DESC LIMIT ?'
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (limit,))
            rows = cursor.fetchall()
            return [dict(zip(columns, r)) for r in rows]


class GoogleSheetsStorage:
    """Google Sheets storage backend using gspread and service account."""

    def __init__(self, credentials_info: dict, sheet_id: str):
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_info(credentials_info, scopes=scopes)
        self.client = gspread.authorize(creds)
        self.sheet_id = sheet_id
        self.spreadsheet = self.client.open_by_key(sheet_id)
        self._ensure_tabs()

    def _ensure_tabs(self):
        existing_worksheets = {ws.title: ws for ws in self.spreadsheet.worksheets()}
        for tab_name in REQUIRED_TABS:
            headers = TAB_SCHEMAS[tab_name]
            if tab_name not in existing_worksheets:
                ws = self.spreadsheet.add_worksheet(title=tab_name, rows=500, cols=len(headers) + 2)
                ws.append_row(headers)
            else:
                ws = existing_worksheets[tab_name]
                # Check if headers exist
                first_row = ws.row_values(1)
                if not first_row:
                    ws.append_row(headers)

    def append_rows(self, tab_name: str, rows: List[Dict[str, Any]]):
        if not rows:
            return
        ws = self.spreadsheet.worksheet(tab_name)
        headers = TAB_SCHEMAS[tab_name]
        data_to_append = []
        for row in rows:
            formatted_row = []
            for h in headers:
                val = row.get(h, "")
                if isinstance(val, (dict, list)):
                    formatted_row.append(json.dumps(val))
                else:
                    formatted_row.append(str(val))
            data_to_append.append(formatted_row)
        ws.append_rows(data_to_append)

    def get_run_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        ws = self.spreadsheet.worksheet("Run_Log")
        records = ws.get_all_records()
        return list(reversed(records))[:limit]


class FeedbackStorageManager:
    """
    Unified storage manager that tries Google Sheets first,
    and falls back safely to Local SQLite/CSV.
    """

    def __init__(self):
        self.backend = None
        self.is_google_sheets = False
        self.sheet_id = None
        self.status_message = ""
        self.local_backend = LocalSQLiteStorage()

        self._init_backend()

    def _init_backend(self):
        creds_dict, sheet_id = self._find_gcp_credentials()
        if creds_dict and sheet_id:
            try:
                self.backend = GoogleSheetsStorage(creds_dict, sheet_id)
                self.is_google_sheets = True
                self.sheet_id = sheet_id
                self.status_message = "Connected to Google Sheets"
                return
            except Exception as e:
                self.status_message = f"Google Sheets connection failed ({e}). Active Fallback: Local SQLite Storage."
        else:
            self.status_message = "Google Sheets credentials not found. Active Fallback: Local SQLite Storage."

        self.backend = self.local_backend
        self.is_google_sheets = False

    def _find_gcp_credentials(self) -> Tuple[Optional[dict], Optional[str]]:
        sheet_id = None
        creds = None

        # 1. Streamlit secrets
        try:
            import streamlit as st
            if hasattr(st, "secrets"):
                if "GOOGLE_SHEET_ID" in st.secrets:
                    sheet_id = st.secrets["GOOGLE_SHEET_ID"].strip()
                if "gcp_service_account" in st.secrets:
                    creds = dict(st.secrets["gcp_service_account"])
        except Exception:
            pass

        # 2. Environment variables
        if not sheet_id:
            sheet_id = os.environ.get("GOOGLE_SHEET_ID")

        if not creds and os.environ.get("GCP_SERVICE_ACCOUNT_JSON"):
            try:
                creds = json.loads(os.environ["GCP_SERVICE_ACCOUNT_JSON"])
            except Exception:
                pass

        # 3. Local file check (e.g. service_account.json)
        if not creds and os.path.exists("service_account.json"):
            try:
                with open("service_account.json", "r") as f:
                    creds = json.load(f)
            except Exception:
                pass

        return creds, sheet_id

    def get_sheet_url(self) -> Optional[str]:
        if self.is_google_sheets and self.sheet_id:
            return f"https://docs.google.com/spreadsheets/d/{self.sheet_id}/edit"
        return None

    def save_run(
        self,
        run_id: str,
        raw_entries: List[Dict[str, Any]],
        classified_entries: List[Dict[str, Any]],
        themes: List[Dict[str, Any]],
        suggestions: List[Dict[str, Any]],
        report_summary: Dict[str, Any],
        duration_seconds: float,
        prompt_version: str,
        stages_completed: str,
        status: str = "Success",
        error_message: str = "",
        email_status: str = "Skipped (Not Configured)"
    ):
        """Save a complete pipeline run across all appropriate tabs."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Format Raw Feedback
        raw_rows = [
            {
                "run_id": run_id,
                "timestamp": now_iso,
                "id": item.get("id"),
                "original_text": item.get("original_text", ""),
                "sanitized_text": item.get("text", ""),
                "redactions_count": item.get("redaction_count", 0)
            }
            for item in raw_entries
        ]

        # Format Classified
        classified_rows = [
            {
                "run_id": run_id,
                "timestamp": now_iso,
                "id": c.get("id"),
                "sentiment": c.get("sentiment"),
                "category": c.get("category"),
                "urgency": c.get("urgency"),
                "confidence": c.get("confidence"),
                "short_reason": c.get("short_reason")
            }
            for c in classified_entries
        ]

        # Format Themes
        themes_rows = [
            {
                "run_id": run_id,
                "timestamp": now_iso,
                "theme": t.get("theme"),
                "category": t.get("category"),
                "description": t.get("description"),
                "frequency": t.get("frequency"),
                "share_percent": t.get("share_percent"),
                "dominant_sentiment": t.get("dominant_sentiment"),
                "example_entry_ids": t.get("example_entry_ids")
            }
            for t in themes
        ]

        # Format Suggestions
        suggestions_rows = [
            {
                "run_id": run_id,
                "timestamp": now_iso,
                "theme": s.get("theme"),
                "priority": s.get("priority"),
                "suggested_actions": s.get("suggested_actions"),
                "expected_impact": s.get("expected_impact"),
                "owner_suggestion": s.get("owner_suggestion")
            }
            for s in suggestions
        ]

        # Format Report
        report_row = [{
            "run_id": run_id,
            "timestamp": now_iso,
            "total_entries": report_summary.get("total_entries", len(raw_entries)),
            "positive_pct": report_summary.get("positive_pct", 0),
            "neutral_pct": report_summary.get("neutral_pct", 0),
            "negative_pct": report_summary.get("negative_pct", 0),
            "mixed_pct": report_summary.get("mixed_pct", 0),
            "report_markdown": report_summary.get("report_markdown", "")
        }]

        storage_target = "Google Sheets" if self.is_google_sheets else "Local SQLite Storage"

        # Format Run Log
        log_row = [{
            "run_id": run_id,
            "timestamp": now_iso,
            "entries_count": len(raw_entries),
            "prompt_version": prompt_version,
            "stages_completed": stages_completed,
            "duration_seconds": round(duration_seconds, 2),
            "status": status,
            "error_message": error_message,
            "storage_target": storage_target,
            "email_status": email_status
        }]

        # Append to backend (and always backup locally)
        try:
            self.backend.append_rows("Raw_Feedback", raw_rows)
            self.backend.append_rows("Classified", classified_rows)
            self.backend.append_rows("Themes", themes_rows)
            self.backend.append_rows("Suggestions", suggestions_rows)
            self.backend.append_rows("Report", report_row)
            self.backend.append_rows("Run_Log", log_row)
        except Exception as e:
            # If Google Sheets append fails during run, write to local fallback
            if self.is_google_sheets:
                self.local_backend.append_rows("Raw_Feedback", raw_rows)
                self.local_backend.append_rows("Classified", classified_rows)
                self.local_backend.append_rows("Themes", themes_rows)
                self.local_backend.append_rows("Suggestions", suggestions_rows)
                self.local_backend.append_rows("Report", report_row)
                log_row[0]["storage_target"] = "Local SQLite (Google Sheets Write Failed)"
                log_row[0]["error_message"] = f"Sheets error: {e}"
                self.local_backend.append_rows("Run_Log", log_row)
            else:
                raise e

        # Mirror log locally if using Google Sheets for redundancy
        if self.is_google_sheets:
            try:
                self.local_backend.append_rows("Run_Log", log_row)
            except Exception:
                pass

    def save_evaluation(
        self,
        prompt_version: str,
        model: str,
        accuracy: float,
        precision_macro: float,
        recall_macro: float,
        f1_macro: float,
        tested_count: int,
        details: dict
    ):
        """Append an evaluation benchmark run to the Evaluation tab."""
        import uuid
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        row = [{
            "run_id": f"EVAL-{uuid.uuid4().hex[:8].upper()}",
            "timestamp": now_iso,
            "prompt_version": prompt_version,
            "model": model,
            "accuracy": round(accuracy, 4),
            "precision_macro": round(precision_macro, 4),
            "recall_macro": round(recall_macro, 4),
            "f1_macro": round(f1_macro, 4),
            "tested_count": tested_count,
            "details_json": details
        }]
        try:
            self.backend.append_rows("Evaluation", row)
        except Exception:
            self.local_backend.append_rows("Evaluation", row)

    def get_run_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent run logs."""
        try:
            return self.backend.get_run_logs(limit=limit)
        except Exception:
            return self.local_backend.get_run_logs(limit=limit)

