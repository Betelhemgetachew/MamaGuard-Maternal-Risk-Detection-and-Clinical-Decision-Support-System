# type: ignore
"""
email_service.py — Email notification service for MamaGuard.
Replaces the previous SMS (Africa's Talking) integration with direct
email notifications sent via SMTP using Python's built-in smtplib.

Configure via environment variables (e.g. a .env file):
    SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_USE_TLS, CLINIC_EMAIL

Without credentials, the module falls back to MOCK mode: it prints the
email to the terminal and still logs it to the database, mirroring the
previous SMS mock behavior.
"""

import os
import smtplib
import ssl
from email.message import EmailMessage

from dotenv import load_dotenv

import database as db

# Load SMTP credentials from a .env file into the environment. Without this the
# os.getenv() calls below always see empty values and the service silently stays
# in MOCK mode (emails printed to terminal, never actually sent).
load_dotenv()

SMTP_HOST     = os.getenv("SMTP_HOST", "")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "465"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_USE_TLS  = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
CLINIC_EMAIL  = os.getenv("CLINIC_EMAIL", SMTP_USERNAME or "no-reply@mamaguard.local")
SMTP_TIMEOUT  = float(os.getenv("SMTP_TIMEOUT", "20"))

# Port 465 speaks TLS from the first byte (SMTP_SSL); port 587 starts in the clear
# and upgrades with STARTTLS. Many ISPs and campus/clinic networks block outbound
# 587 while leaving 465 open, which shows up as a connect timeout, not a refusal.
SMTP_USE_SSL  = os.getenv("SMTP_USE_SSL", "").lower() == "true" or SMTP_PORT == 465
SSL_PORT      = 465

_MOCK_MODE = not (SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD)


def _deliver(msg, port: int, use_ssl: bool) -> None:
    """Open one connection, authenticate and hand off the message."""
    context = ssl.create_default_context()
    if use_ssl:
        with smtplib.SMTP_SSL(SMTP_HOST, port, timeout=SMTP_TIMEOUT,
                              context=context) as server:
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, port, timeout=SMTP_TIMEOUT) as server:
            if SMTP_USE_TLS:
                server.starttls(context=context)
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)


def _send_email(to_email: str, subject: str, body: str) -> str:
    """Send one email. Returns 'sent', 'mock_success', or 'failed'."""
    if _MOCK_MODE:
        print(f"[MOCK EMAIL] To: {to_email} | Subject: {subject}\n{body}\n")
        return "mock_success"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = CLINIC_EMAIL
    msg["To"] = to_email
    msg.set_content(body)

    try:
        _deliver(msg, SMTP_PORT, SMTP_USE_SSL)
        return "sent"
    except (TimeoutError, OSError) as exc:
        # A timeout here is a blocked port, not a bad message. If we were on the
        # STARTTLS port, one retry over implicit SSL usually gets through.
        if SMTP_USE_SSL or SMTP_PORT == SSL_PORT:
            print(f"[EMAIL ERROR] Could not reach {SMTP_HOST}:{SMTP_PORT} "
                  f"for {to_email}: {type(exc).__name__}: {exc}")
            return "failed"

        print(f"[EMAIL WARN] {SMTP_HOST}:{SMTP_PORT} timed out "
              f"({type(exc).__name__}: {exc}) — retrying on {SSL_PORT}/SSL …")
        try:
            _deliver(msg, SSL_PORT, use_ssl=True)
            print(f"[EMAIL] Sent to {to_email} via fallback port {SSL_PORT}. "
                  f"Set SMTP_PORT={SSL_PORT} in .env to skip the timeout.")
            return "sent"
        except Exception as retry_exc:
            print(f"[EMAIL ERROR] Fallback to {SSL_PORT}/SSL also failed for "
                  f"{to_email}: {type(retry_exc).__name__}: {retry_exc}")
            return "failed"
    except Exception as exc:
        print(f"[EMAIL ERROR] Failed to send to {to_email}: "
              f"{type(exc).__name__}: {exc}")
        return "failed"


def send_post_visit_email(patient_id, full_name, to_email, next_visit_date):
    subject = "MamaGuard — Visit Summary & Next Appointment"
    body = (
        f"Dear {full_name},\n\n"
        f"Thank you for your visit today. Your vitals have been recorded and "
        f"reviewed by our AI-assisted risk system.\n\n"
        f"Your next follow-up appointment is scheduled for: {next_visit_date}.\n\n"
        f"Please contact the clinic if you have any concerns before then.\n\n"
        f"— MamaGuard Maternal Health Team"
    )
    status = _send_email(to_email, subject, body)
    db.log_email(patient_id, to_email, "post_visit", subject, body, status)


def send_appointment_reminder_email(patient_id, full_name, to_email, appt_date):
    subject = "MamaGuard — Appointment Reminder"
    body = (
        f"Dear {full_name},\n\n"
        f"This is a reminder that your antenatal appointment is scheduled for "
        f"{appt_date}. Please arrive on time and bring your clinic card.\n\n"
        f"— MamaGuard Maternal Health Team"
    )
    status = _send_email(to_email, subject, body)
    db.log_email(patient_id, to_email, "appointment_reminder", subject, body, status)


def send_missed_appointment_email(patient_id, full_name, to_email):
    subject = "MamaGuard — Missed Appointment"
    body = (
        f"Dear {full_name},\n\n"
        f"We noticed you missed your scheduled antenatal appointment. Your health "
        f"and your baby's health are important to us — please contact the clinic "
        f"as soon as possible to reschedule.\n\n"
        f"— MamaGuard Maternal Health Team"
    )
    status = _send_email(to_email, subject, body)
    db.log_email(patient_id, to_email, "missed_appointment", subject, body, status)


def send_nurse_alert_email(nurse_email, patient_name, patient_id, missed_count):
    subject = "MamaGuard — Nurse Alert: Repeated Missed Appointments"
    body = (
        f"Attention Clinical Team,\n\n"
        f"Patient {patient_name} (ID: {patient_id}) has missed {missed_count} "
        f"consecutive appointments and may need urgent follow-up.\n\n"
        f"Please review this patient's records and arrange outreach.\n\n"
        f"— MamaGuard Maternal Health System"
    )
    status = _send_email(nurse_email, subject, body)
    db.log_email(patient_id, nurse_email, "nurse_alert", subject, body, status)