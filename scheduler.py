"""
Background Scheduler — APScheduler
Runs two daily jobs:
  1. 17:00 — Check for missed appointments, send email + nurse alerts
  2. 09:00 — Send appointment reminders for next 3 days
"""

import os
import logging
from datetime import date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import database as db
from email_service import (
    send_missed_appointment_email,
    send_appointment_reminder_email,
    send_nurse_alert_email,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

NURSE_EMAILS = [e.strip() for e in
                os.getenv("NURSE_EMAILS", "nurse@mamaguard.local").split(",")
                if e.strip()]
CONSECUTIVE_MISS_THRESHOLD = 3


def job_check_missed_appointments():
    """Run daily at 17:00: find overdue appointments and alert patients/nurses."""
    log.info("🕔 Running missed-appointment check …")
    today = date.today().isoformat()

    with db.get_conn() as conn:
        overdue = conn.execute(
            """SELECT a.appt_id, a.patient_id, a.scheduled_date,
                      p.full_name, p.email
               FROM appointments a
               JOIN patients p ON a.patient_id = p.patient_id
               WHERE a.scheduled_date < ?
                 AND a.status = 'scheduled'""",
            (today,),
        ).fetchall()

    for row in overdue:
        appt_id    = row["appt_id"]
        patient_id = row["patient_id"]
        name       = row["full_name"]
        email      = row["email"]

        db.mark_appointment_missed(appt_id)
        if email:
            send_missed_appointment_email(patient_id, name, email)
        log.info(f"  Missed: {name} (appt {appt_id})")

    # Check for patients with >= 3 consecutive misses → nurse alert
    repeat_missers = db.get_patients_with_consecutive_misses(CONSECUTIVE_MISS_THRESHOLD)
    for patient in repeat_missers:
        log.warning(f"  ⚠️  {patient['full_name']} has {patient['missed_count']} misses!")
        for nurse_email in NURSE_EMAILS:
            send_nurse_alert_email(
                nurse_email,
                patient["full_name"],
                patient["patient_id"],
                patient["missed_count"],
            )


def job_send_reminders():
    """Run daily at 09:00: send reminders for appointments in next 3 days."""
    log.info("🔔 Sending appointment reminders …")
    upcoming = db.get_upcoming_appointments(days_ahead=3)
    for appt in upcoming:
        if not appt.get("email"):
            continue
        send_appointment_reminder_email(
            appt["patient_id"],
            appt["full_name"],
            appt["email"],
            appt["scheduled_date"],
        )
        log.info(f"  Reminder sent to {appt['full_name']} for {appt['scheduled_date']}")


_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="Africa/Nairobi")
    _scheduler.add_job(
        job_check_missed_appointments,
        CronTrigger(hour=17, minute=0),
        id="missed_check",
        replace_existing=True,
    )
    _scheduler.add_job(
        job_send_reminders,
        CronTrigger(hour=9, minute=0),
        id="reminders",
        replace_existing=True,
    )
    _scheduler.start()
    log.info("✅ Scheduler started (jobs: missed_check @17:00, reminders @09:00 EAT)")
    return _scheduler


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown()
        log.info("Scheduler stopped.")


if __name__ == "__main__":
    # Run both jobs immediately for testing
    job_check_missed_appointments()
    job_send_reminders()
