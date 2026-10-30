"""
Automated Action Engine: Gmail SMTP Trigger-Action Notifications.
Sends formatted Feedback Intelligence summaries automatically upon pipeline completion.
Gracefully skips if SMTP credentials are not configured.
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional, Tuple


def get_smtp_config() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Retrieve SMTP configuration from Streamlit secrets or environment variables.
    Returns: (smtp_user, smtp_app_password, recipient_email)
    """
    smtp_user = None
    smtp_pwd = None
    recipient = None

    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            smtp_user = st.secrets.get("SMTP_USER")
            smtp_pwd = st.secrets.get("SMTP_APP_PASSWORD")
            recipient = st.secrets.get("REPORT_RECIPIENT")
    except Exception:
        pass

    if not smtp_user:
        smtp_user = os.environ.get("SMTP_USER")
    if not smtp_pwd:
        smtp_pwd = os.environ.get("SMTP_APP_PASSWORD")
    if not recipient:
        recipient = os.environ.get("REPORT_RECIPIENT")

    # Filter out template / placeholder values
    if smtp_user and ("your_email" in smtp_user or "example" in smtp_user):
        smtp_user = None
    if smtp_pwd and ("your_16" in smtp_pwd or "placeholder" in smtp_pwd):
        smtp_pwd = None

    return smtp_user, smtp_pwd, recipient


def format_email_body(
    run_id: str,
    total_entries: int,
    prompt_version: str,
    sentiment_distribution: Dict[str, float],
    top_themes: List[Dict[str, Any]],
    suggestions: List[Dict[str, Any]],
    sheet_url: Optional[str]
) -> Tuple[str, str]:
    """Generate both Plain Text and HTML email bodies."""
    pos = sentiment_distribution.get("positive", 0)
    neu = sentiment_distribution.get("neutral", 0)
    neg = sentiment_distribution.get("negative", 0)
    mix = sentiment_distribution.get("mixed", 0)

    # Plain Text
    text_lines = [
        f"STUDENT FEEDBACK INTELLIGENCE REPORT [Run: {run_id}]",
        "======================================================",
        f"Total Feedback Analyzed: {total_entries}",
        f"Classification Engine: Prompt {prompt_version}",
        f"Sentiment Split: Positive: {pos:.1f}% | Neutral: {neu:.1f}% | Negative: {neg:.1f}% | Mixed: {mix:.1f}%",
        "",
        "TOP RECURRING THEMES & ACTIONS:",
    ]
    for idx, t in enumerate(top_themes[:3], 1):
        text_lines.append(f"{idx}. {t.get('theme')} ({t.get('frequency')} entries, {t.get('dominant_sentiment')})")
        text_lines.append(f"   Context: {t.get('description')}")

    if sheet_url:
        text_lines.append(f"\nView Full Data in Google Sheet: {sheet_url}")

    plain_text = "\n".join(text_lines)

    # HTML
    theme_rows_html = ""
    for t in top_themes[:4]:
        theme_rows_html += f"""
        <tr style="border-bottom: 1px solid #e2e8f0;">
            <td style="padding: 10px; font-weight: bold; color: #1e293b;">{t.get('theme')}</td>
            <td style="padding: 10px; color: #475569;">{t.get('category')}</td>
            <td style="padding: 10px; text-align: center;"><b>{t.get('frequency')}</b> ({t.get('share_percent', 0):.1f}%)</td>
            <td style="padding: 10px; color: #64748b;">{t.get('description')}</td>
        </tr>
        """

    sheet_button_html = (
        f'<p style="margin-top: 25px;"><a href="{sheet_url}" style="background-color: #2563EB; color: white; padding: 10px 18px; text-decoration: none; border-radius: 6px; font-weight: bold;">Open Live Google Sheet</a></p>'
        if sheet_url else ""
    )

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 20px; }}
            .container {{ max-width: 650px; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; padding: 24px; margin: auto; }}
            .header {{ border-bottom: 2px solid #2563EB; padding-bottom: 12px; margin-bottom: 20px; }}
            .metric-box {{ display: inline-block; width: 22%; background: #f1f5f9; padding: 10px; border-radius: 6px; text-align: center; margin-right: 2%; box-sizing: border-box; }}
            .metric-val {{ font-size: 18px; font-weight: bold; color: #0f172a; }}
            .metric-lbl {{ font-size: 11px; text-transform: uppercase; color: #64748b; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 13px; }}
            th {{ background: #f8fafc; text-align: left; padding: 10px; color: #475569; border-bottom: 2px solid #cbd5e1; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h2 style="margin: 0; color: #1e293b;">🎓 Student Feedback Analysis Report</h2>
                <p style="margin: 4px 0 0; color: #64748b; font-size: 13px;">Run ID: <code>{run_id}</code> | Prompt: <b>{prompt_version}</b> | Analyzed: <b>{total_entries} entries</b></p>
            </div>

            <div style="margin-bottom: 20px;">
                <div class="metric-box"><div class="metric-val" style="color: #16a34a;">{pos:.1f}%</div><div class="metric-lbl">Positive</div></div>
                <div class="metric-box"><div class="metric-val" style="color: #0284c7;">{neu:.1f}%</div><div class="metric-lbl">Neutral</div></div>
                <div class="metric-box"><div class="metric-val" style="color: #dc2626;">{neg:.1f}%</div><div class="metric-lbl">Negative</div></div>
                <div class="metric-box"><div class="metric-val" style="color: #d97706;">{mix:.1f}%</div><div class="metric-lbl">Mixed</div></div>
            </div>

            <h3 style="color: #1e293b; margin-top: 24px;">Key Recurring Thematic Clusters</h3>
            <table>
                <thead>
                    <tr>
                        <th>Theme</th>
                        <th>Category</th>
                        <th style="text-align: center;">Frequency</th>
                        <th>Context</th>
                    </tr>
                </thead>
                <tbody>
                    {theme_rows_html}
                </tbody>
            </table>

            {sheet_button_html}

            <p style="margin-top: 30px; font-size: 11px; color: #94a3b8; border-top: 1px solid #f1f5f9; padding-top: 12px;">
                🤖 Automated alert generated by the Student Feedback Intelligence System. PII was redacted prior to analysis.
            </p>
        </div>
    </body>
    </html>
    """
    return plain_text, html


def send_automated_report_email(
    run_id: str,
    total_entries: int,
    prompt_version: str,
    sentiment_distribution: Dict[str, float],
    top_themes: List[Dict[str, Any]],
    suggestions: List[Dict[str, Any]],
    sheet_url: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Automated SMTP Trigger-Action: Sends report email if credentials are configured.
    Returns: (success: bool, status_message: str)
    """
    smtp_user, smtp_pwd, recipient = get_smtp_config()

    if not smtp_user or not smtp_pwd or not recipient:
        return False, "Skipped (SMTP credentials not configured in secrets)"

    plain_body, html_body = format_email_body(
        run_id=run_id,
        total_entries=total_entries,
        prompt_version=prompt_version,
        sentiment_distribution=sentiment_distribution,
        top_themes=top_themes,
        suggestions=suggestions,
        sheet_url=sheet_url
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎓 Student Feedback Intelligence Alert: Run {run_id}"
    msg["From"] = smtp_user
    msg["To"] = recipient

    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_pwd)
            server.sendmail(smtp_user, [recipient], msg.as_string())
        return True, f"Sent successfully to {recipient}"
    except Exception as e:
        return False, f"Failed: {str(e)[:100]}"

