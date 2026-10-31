# type: ignore
"""
Database Layer — SQLite
Tables: patients, visits, predictions, appointments, email_log
"""

import sqlite3
import json
import numpy as np
from datetime import date, datetime, timedelta
from pathlib import Path


class _NumpyEncoder(json.JSONEncoder):
    """Converts numpy scalars/arrays to native Python types before JSON serialisation."""
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def _dumps(obj) -> str:
    return json.dumps(obj, cls=_NumpyEncoder)


DB_PATH = Path(__file__).parent / "maternal_risk.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create all tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS patients (
                patient_id      TEXT PRIMARY KEY,
                full_name       TEXT NOT NULL,
                phone           TEXT,
                email           TEXT,
                age             INTEGER,
                weeks_pregnant  INTEGER DEFAULT 0,
                prev_pregnancies INTEGER DEFAULT 0,
                family_hypertension INTEGER DEFAULT 0,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS visits (
                visit_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id      TEXT REFERENCES patients(patient_id),
                visit_date      TEXT DEFAULT (date('now')),
                systolic_bp     INTEGER,
                diastolic_bp    INTEGER,
                blood_sugar     REAL,
                body_temp       REAL,
                heart_rate      INTEGER,
                weight_kg       REAL,
                height_cm       REAL,
                bmi             REAL,
                proteinuria     INTEGER DEFAULT 0,
                notes           TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS predictions (
                pred_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                visit_id        INTEGER REFERENCES visits(visit_id),
                patient_id      TEXT REFERENCES patients(patient_id),
                risk_level      TEXT,
                risk_score      REAL,
                top_risks       TEXT,
                shap_reasons    TEXT,
                suggested_action TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS appointments (
                appt_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id      TEXT REFERENCES patients(patient_id),
                scheduled_date  TEXT NOT NULL,
                reason          TEXT,
                status          TEXT DEFAULT 'scheduled',
                missed_count    INTEGER DEFAULT 0,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS email_log (
                email_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id      TEXT,
                email           TEXT,
                message_type    TEXT,
                subject         TEXT,
                message_body    TEXT,
                status          TEXT DEFAULT 'queued',
                sent_at         TEXT DEFAULT (datetime('now'))
            );
            """
        )
        _run_migrations(conn)


def _run_migrations(conn) -> None:
    """Apply idempotent schema migrations to databases created before a column existed.

    CREATE TABLE IF NOT EXISTS never alters an existing table, so pre-existing
    databases (e.g. those built before the SMS→email switch) are patched here.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(patients)")}
    if "email" not in cols:
        conn.execute("ALTER TABLE patients ADD COLUMN email TEXT")

    # Height & BMI added to visits after the initial release.
    visit_cols = {row["name"] for row in conn.execute("PRAGMA table_info(visits)")}
    for col in ("height_cm", "bmi"):
        if col not in visit_cols:
            conn.execute(f"ALTER TABLE visits ADD COLUMN {col} REAL")

    # Legacy table from the pre-email (SMS) design — superseded by email_log.
    conn.execute("DROP TABLE IF EXISTS sms_log")


# ── Patient CRUD ─────────────────────────────────────────────────────────────

def upsert_patient(patient_id, full_name, phone, age,
                   weeks_pregnant=0, prev_pregnancies=0,
                   family_hypertension=False, email=None) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO patients
               (patient_id, full_name, phone, age, weeks_pregnant,
                prev_pregnancies, family_hypertension, email)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(patient_id) DO UPDATE SET
                 full_name=excluded.full_name,
                 phone=excluded.phone,
                 age=excluded.age,
                 weeks_pregnant=excluded.weeks_pregnant,
                 prev_pregnancies=excluded.prev_pregnancies,
                 family_hypertension=excluded.family_hypertension,
                 email=excluded.email
            """,
            (patient_id, full_name, phone, age, weeks_pregnant,
             prev_pregnancies, int(family_hypertension), email),
        )


def get_patient(patient_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM patients WHERE patient_id=?", (patient_id,)
        ).fetchone()
    return dict(row) if row else None


def list_patients() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM patients ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Visit CRUD ────────────────────────────────────────────────────────────────

def save_visit(patient_id, systolic_bp, diastolic_bp, blood_sugar,
               body_temp, heart_rate, weight_kg=None,
               proteinuria=False, notes="", height_cm=None, bmi=None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO visits
               (patient_id, systolic_bp, diastolic_bp, blood_sugar,
                body_temp, heart_rate, weight_kg, height_cm, bmi,
                proteinuria, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (patient_id, systolic_bp, diastolic_bp, blood_sugar,
             body_temp, heart_rate, weight_kg, height_cm, bmi,
             int(proteinuria), notes),
        )
    return cur.lastrowid


def get_patient_visits(patient_id: str, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM visits WHERE patient_id=?
               ORDER BY visit_date DESC LIMIT ?""",
            (patient_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Prediction CRUD ───────────────────────────────────────────────────────────

def save_prediction(visit_id, patient_id, risk_level, risk_score,
                    top_risks: list, shap_reasons: dict,
                    suggested_action: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO predictions
               (visit_id, patient_id, risk_level, risk_score,
                top_risks, shap_reasons, suggested_action)
               VALUES (?,?,?,?,?,?,?)""",
            (visit_id, patient_id, risk_level, risk_score,
             json.dumps(top_risks), _dumps(shap_reasons),
             suggested_action),
        )
    return cur.lastrowid


def get_latest_prediction(patient_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM predictions WHERE patient_id=?
               ORDER BY created_at DESC LIMIT 1""",
            (patient_id,),
        ).fetchone()
    if row:
        d = dict(row)
        d["top_risks"]    = json.loads(d["top_risks"])
        d["shap_reasons"] = json.loads(d["shap_reasons"])
        return d
    return None


def get_risk_distribution() -> dict[str, int]:
    """Count patients per risk tier, using only each patient's latest prediction."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT risk_level, COUNT(*) AS cnt FROM predictions
               WHERE pred_id IN (
                   SELECT MAX(pred_id) FROM predictions GROUP BY patient_id
               )
               GROUP BY risk_level"""
        ).fetchall()
    return {r["risk_level"]: r["cnt"] for r in rows}


def get_dashboard_counts() -> dict[str, int]:
    """Headline numbers for the dashboard cards, in a single connection."""
    with get_conn() as conn:
        patients = conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
        high_risk = conn.execute(
            """SELECT COUNT(DISTINCT patient_id) FROM predictions
               WHERE pred_id IN (
                   SELECT MAX(pred_id) FROM predictions GROUP BY patient_id
               )
               AND risk_level='high risk'"""
        ).fetchone()[0]
        today_visits = conn.execute(
            "SELECT COUNT(*) FROM visits WHERE visit_date=date('now')"
        ).fetchone()[0]
        pending_appts = conn.execute(
            """SELECT COUNT(*) FROM appointments
               WHERE status='scheduled' AND scheduled_date >= date('now')"""
        ).fetchone()[0]
    return {
        "patients": patients,
        "high_risk": high_risk,
        "today_visits": today_visits,
        "pending_appointments": pending_appts,
    }


def get_pending_appointments() -> list[dict]:
    """Every still-scheduled appointment, oldest first — drives the attendance page."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT a.appt_id, a.scheduled_date, a.reason,
                      p.full_name, p.patient_id, p.email, p.phone
               FROM appointments a JOIN patients p ON a.patient_id = p.patient_id
               WHERE a.status='scheduled'
               ORDER BY a.scheduled_date"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_high_risk_patients() -> list[dict]:
    """Return patients whose latest prediction is high risk."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT p.*, pr.risk_level, pr.risk_score, pr.suggested_action,
                      pr.created_at AS pred_date
               FROM patients p
               JOIN predictions pr ON p.patient_id = pr.patient_id
               WHERE pr.pred_id IN (
                   SELECT MAX(pred_id) FROM predictions GROUP BY patient_id
               )
               AND pr.risk_level = 'high risk'
               ORDER BY pr.risk_score DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


# ── Appointment CRUD ──────────────────────────────────────────────────────────

def schedule_appointment(patient_id: str, scheduled_date: str,
                          reason: str = "Routine follow-up") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO appointments (patient_id, scheduled_date, reason)
               VALUES (?,?,?)""",
            (patient_id, scheduled_date, reason),
        )
    return cur.lastrowid


def mark_appointment_attended(appt_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE appointments SET status='attended' WHERE appt_id=?",
            (appt_id,),
        )


def mark_appointment_missed(appt_id: int):
    with get_conn() as conn:
        conn.execute(
            """UPDATE appointments
               SET status='missed', missed_count = missed_count + 1
               WHERE appt_id=?""",
            (appt_id,),
        )


def get_patients_with_consecutive_misses(threshold: int = 3) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT p.patient_id, p.full_name, p.phone, p.email,
                      COUNT(a.appt_id) AS missed_count
               FROM patients p
               JOIN appointments a ON p.patient_id = a.patient_id
               WHERE a.status = 'missed'
               GROUP BY p.patient_id
               HAVING COUNT(a.appt_id) >= ?
            """,
            (threshold,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_upcoming_appointments(days_ahead: int = 7) -> list[dict]:
    target = (date.today() + timedelta(days=days_ahead)).isoformat()
    today  = date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT a.*, p.full_name, p.phone, p.email FROM appointments a
               JOIN patients p ON a.patient_id = p.patient_id
               WHERE a.scheduled_date BETWEEN ? AND ?
               AND a.status = 'scheduled'
               ORDER BY a.scheduled_date""",
            (today, target),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Email Log ─────────────────────────────────────────────────────────────────

def log_email(patient_id, email, message_type, subject, message_body,
              status="queued"):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO email_log
               (patient_id, email, message_type, subject, message_body, status)
               VALUES (?,?,?,?,?,?)""",
            (patient_id, email, message_type, subject, message_body, status),
        )


def get_email_log(patient_id: str = None) -> list[dict]:
    with get_conn() as conn:
        if patient_id:
            rows = conn.execute(
                "SELECT * FROM email_log WHERE patient_id=? ORDER BY sent_at DESC",
                (patient_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM email_log ORDER BY sent_at DESC LIMIT 50"
            ).fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
    print("Database initialised at", DB_PATH)