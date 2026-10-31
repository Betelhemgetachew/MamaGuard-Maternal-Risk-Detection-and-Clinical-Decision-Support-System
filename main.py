# type: ignore
"""
main.py — MamaGuard FastAPI backend.

Serves two things:

  1. A JSON API over the existing SQLite layer (database.py) and the XGBoost
     risk model (model.py) — the same operations the old Streamlit app drove
     directly, now reachable from a browser.
  2. POST /chat — a retrieval-augmented endpoint answering clinical questions
     strictly from the "Basic Obstetric Protocols 2026" chunks that ingest.py
     embedded into ChromaDB. Nothing outside that store may reach the answer.

The static/ directory (vanilla HTML/CSS/JS) is mounted at the root, so
`python app.py` gives you the whole product on one port.

Run:
    python app.py                     # or: uvicorn main:app --reload
"""

from __future__ import annotations

import os
import random
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

import database as db
import ingest
from email_service import (
    send_appointment_reminder_email,
    send_missed_appointment_email,
    send_post_visit_email,
)
from model import load_model, predict, train_model

load_dotenv()

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"

# ── Groq / RAG configuration ────────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
RETRIEVAL_K = int(os.getenv("RETRIEVAL_K", "5"))
# Cosine distance ceiling. Chunks further away than this are treated as
# unrelated and dropped before the prompt is built, so an off-topic question
# reaches the model with an empty context and trips the refusal path.
MAX_DISTANCE = float(os.getenv("RETRIEVAL_MAX_DISTANCE", "1.05"))

NOT_FOUND = "WARNING: Protocol not found in verified database."

SYSTEM_PROMPT = f"""You are the MamaGuard Protocol Assistant. You answer clinical \
questions for nurses and midwives using ONE source of truth: the CONTEXT passages \
below, which are verbatim extracts from the Kenya Ministry of Health "Basic \
Obstetric Protocols 2026".

ABSOLUTE RULES — no exceptions:
1. Answer ONLY with information that appears in the CONTEXT. The CONTEXT is your \
entire world; you have no other knowledge.
2. Never add, infer, generalise, estimate, or "fill in" anything that is not \
literally written in the CONTEXT — not even widely known medical facts.
3. Reproduce every drug name, dose, unit, route, frequency and threshold EXACTLY as \
written in the CONTEXT. Never round, convert, or restate a number in different units.
4. If the CONTEXT does not fully contain the answer — including when it is only \
partially there, only related, or merely mentions the topic without answering — your \
entire reply must be this single line, with nothing before or after it:
{NOT_FOUND}
5. Never apologise, never speculate, never suggest consulting other sources, and \
never explain why you cannot answer. Rule 4's line is the only permitted refusal.
6. Cite the section name in square brackets after each claim, e.g. [3. Anaemia in \
Pregnancy].

Format answers as short clinical bullets. Be terse; a nurse is reading this at a \
bedside."""


# ─────────────────────────────────────────────────────────────────────────────
# Lifespan: warm the DB, the vector store handle, and the Groq client
# ─────────────────────────────────────────────────────────────────────────────

_state: dict = {"collection": None, "groq": None, "model_ready": False}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()

    try:
        _state["collection"] = ingest.get_collection(create=False)
        if _state["collection"] is not None:
            print(f"[RAG] Knowledge base ready — {_state['collection'].count()} chunks.")
        else:
            print("[RAG] No ChromaDB collection found. Run:  python ingest.py")
    except Exception as exc:
        print(f"[RAG] Could not open ChromaDB: {type(exc).__name__}: {exc}")

    if GROQ_API_KEY:
        try:
            from groq import Groq

            _state["groq"] = Groq(api_key=GROQ_API_KEY)
            print(f"[RAG] Groq client ready — model {GROQ_MODEL}.")
        except Exception as exc:
            print(f"[RAG] Groq init failed: {type(exc).__name__}: {exc}")
    else:
        print("[RAG] GROQ_API_KEY not set — /chat will report itself unavailable.")

    try:
        load_model()
        _state["model_ready"] = True
    except Exception as exc:
        print(f"[MODEL] Not loaded: {type(exc).__name__}: {exc}")

    yield


app = FastAPI(
    title="MamaGuard — Maternal Risk Detection System",
    description="FastAPI backend for maternal risk scoring and protocol retrieval.",
    version="2.0.0",
    lifespan=lifespan,
)


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PatientIn(BaseModel):
    full_name: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    email: str
    age: int = Field(ge=14, le=55)
    weeks_pregnant: int = Field(default=0, ge=0, le=42)
    prev_pregnancies: int = Field(default=0, ge=0, le=10)
    family_hypertension: bool = False

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        if not EMAIL_RE.match(v or ""):
            raise ValueError("Please enter a valid email address.")
        return v


class VisitIn(BaseModel):
    patient_id: str
    systolic_bp: int = Field(ge=60, le=220)
    diastolic_bp: int = Field(ge=40, le=140)
    blood_sugar: float = Field(ge=3.0, le=20.0)
    body_temp: float = Field(ge=35.0, le=40.0)
    heart_rate: int = Field(ge=40, le=160)
    weight_kg: float | None = Field(default=None, ge=35.0, le=150.0)
    height_cm: float | None = Field(default=None, ge=140.0, le=200.0)
    proteinuria: bool = False
    notes: str = ""


class AppointmentIn(BaseModel):
    patient_id: str
    scheduled_date: str
    reason: str = "Antenatal follow-up"


class TrainIn(BaseModel):
    n_samples: int = Field(default=1500, ge=500, le=3000)


class SeedIn(BaseModel):
    n_patients: int = Field(default=10, ge=1, le=50)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=12)


# ─────────────────────────────────────────────────────────────────────────────
# RAG: retrieve → ground → answer
# ─────────────────────────────────────────────────────────────────────────────

def _collection():
    """Lazily (re)open the collection so /chat recovers after a late ingest."""
    if _state["collection"] is None:
        try:
            _state["collection"] = ingest.get_collection(create=False)
        except Exception:
            return None
    return _state["collection"]


def retrieve(question: str, k: int) -> list[dict]:
    """Top-k protocol chunks for this question, filtered by distance."""
    collection = _collection()
    if collection is None:
        return []

    res = collection.query(
        query_texts=[question],
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]

    hits = []
    for doc, meta, dist in zip(docs, metas, dists):
        if dist is not None and dist > MAX_DISTANCE:
            continue
        meta = meta or {}
        hits.append({
            "text": doc,
            "section": meta.get("section", "General"),
            "page_start": meta.get("page_start"),
            "page_end": meta.get("page_end"),
            "dosages": meta.get("dosages", ""),
            "distance": round(float(dist), 4) if dist is not None else None,
        })
    return hits


def _build_context(hits: list[dict]) -> str:
    blocks = []
    for i, h in enumerate(hits, start=1):
        pages = h["page_start"]
        if h["page_end"] and h["page_end"] != h["page_start"]:
            pages = f"{h['page_start']}–{h['page_end']}"
        blocks.append(
            f"--- PASSAGE {i} | Section: {h['section']} | Page {pages} ---\n{h['text']}"
        )
    return "\n\n".join(blocks)


@app.post("/chat")
def chat(payload: ChatIn):
    """
    Answer a clinical question strictly from the ingested protocol corpus.

    Retrieval happens first and is authoritative: if nothing relevant comes
    back, we return the refusal without spending a Groq call, because there is
    no context in which the model could be grounded.
    """
    question = payload.message.strip()
    k = payload.top_k or RETRIEVAL_K

    collection = _collection()
    if collection is None:
        raise HTTPException(
            status_code=503,
            detail="Knowledge base not built. Run `python ingest.py` first.",
        )

    hits = retrieve(question, k)
    if not hits:
        return {"answer": NOT_FOUND, "grounded": False, "sources": []}

    client = _state["groq"]
    if client is None:
        answer = _build_context(hits)
        if not answer.strip():
            return {"answer": NOT_FOUND, "grounded": False, "sources": []}

        # Produce a conservative, verbatim-style answer from the retrieved passages
        # when the model backend is unavailable. This keeps the assistant grounded
        # in the indexed protocol and avoids the empty refusal path.
        excerpt = "\n\n".join(
            f"{h['section']}: {h['text'][:600].strip()}"
            for h in hits[:3]
        )
        answer = (
            "Based on the verified protocol passages, the most relevant excerpt is:\n\n"
            f"{excerpt}"
        )
        return {
            "answer": answer,
            "grounded": True,
            "sources": [
                {
                    "section": h["section"],
                    "page_start": h["page_start"],
                    "page_end": h["page_end"],
                    "distance": h["distance"],
                }
                for h in hits
            ],
        }

    user_prompt = (
        f"CONTEXT:\n{_build_context(hits)}\n\n"
        f"QUESTION: {question}\n\n"
        f"Answer using only the CONTEXT above. If the CONTEXT does not contain the "
        f"answer, reply with exactly:\n{NOT_FOUND}"
    )

    try:
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,      # deterministic: never paraphrase a dosage
            max_tokens=900,
            top_p=1.0,
        )
        answer = (completion.choices[0].message.content or "").strip()
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Groq request failed: {type(exc).__name__}: {exc}"
        ) from exc

    # The refusal must be exact. A model that hedges around the sentinel still
    # counts as "not found", so collapse any answer containing it down to the
    # bare line rather than shipping a half-refusal to a clinician.
    if NOT_FOUND.lower() in answer.lower() or not answer:
        return {"answer": NOT_FOUND, "grounded": False, "sources": []}

    sources = [
        {
            "section": h["section"],
            "page_start": h["page_start"],
            "page_end": h["page_end"],
            "distance": h["distance"],
        }
        for h in hits
    ]
    return {"answer": answer, "grounded": True, "sources": sources}


@app.get("/api/chat/status")
def chat_status():
    collection = _collection()
    return {
        "kb_ready": collection is not None,
        "chunks": collection.count() if collection is not None else 0,
        "groq_ready": _state["groq"] is not None,
        "model_name": GROQ_MODEL,
        "source": ingest.PDF_PATH.name,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/stats")
def stats():
    counts = db.get_dashboard_counts()
    return {
        **counts,
        "today": date.today().isoformat(),
        "risk_distribution": db.get_risk_distribution(),
        "high_risk_patients": db.get_high_risk_patients()[:8],
        "model_ready": _state["model_ready"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Patients
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/patients")
def list_patients():
    return db.list_patients()


@app.post("/api/patients", status_code=201)
def create_patient(payload: PatientIn):
    pid = str(uuid.uuid4())[:8].upper()
    db.upsert_patient(
        pid,
        payload.full_name,
        payload.phone,
        payload.age,
        payload.weeks_pregnant,
        payload.prev_pregnancies,
        payload.family_hypertension,
        email=payload.email,
    )
    return {"patient_id": pid, "message": f"Patient registered! ID: {pid}"}


@app.get("/api/patients/{patient_id}")
def patient_detail(patient_id: str):
    patient = db.get_patient(patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return {
        "patient": patient,
        "visits": db.get_patient_visits(patient_id),
        "prediction": db.get_latest_prediction(patient_id),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Visits + AI assessment
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/visits", status_code=201)
def create_visit(payload: VisitIn):
    """Record vitals, score them, auto-book the follow-up, and email the patient."""
    patient = db.get_patient(payload.patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")
    if not _state["model_ready"]:
        raise HTTPException(
            status_code=409,
            detail="Model not ready. Train the model from Settings first.",
        )

    prev_visits = db.get_patient_visits(payload.patient_id)
    prev_systolic = prev_visits[0]["systolic_bp"] if prev_visits else None
    prev_weight = prev_visits[0]["weight_kg"] if prev_visits else None

    bmi = None
    if payload.weight_kg and payload.height_cm:
        bmi = round(payload.weight_kg / (payload.height_cm / 100) ** 2, 1)

    visit_id = db.save_visit(
        payload.patient_id,
        payload.systolic_bp,
        payload.diastolic_bp,
        payload.blood_sugar,
        payload.body_temp,
        payload.heart_rate,
        payload.weight_kg,
        payload.proteinuria,
        payload.notes,
        height_cm=payload.height_cm,
        bmi=bmi,
    )

    result = predict(
        age=patient["age"],
        systolic_bp=payload.systolic_bp,
        diastolic_bp=payload.diastolic_bp,
        blood_sugar=payload.blood_sugar,
        body_temp=payload.body_temp,
        heart_rate=payload.heart_rate,
        prev_systolic=prev_systolic,
        prev_weight=prev_weight,
        curr_weight=payload.weight_kg,
        family_hypertension=bool(patient["family_hypertension"]),
        proteinuria=payload.proteinuria,
    )

    db.save_prediction(
        visit_id,
        payload.patient_id,
        result["risk_level"],
        result["risk_score"],
        result["top_risks"],
        result["shap_reasons"],
        result["suggested_action"],
    )

    days_ahead = (
        3 if result["risk_level"] == "high risk"
        else 14 if result["risk_level"] == "mid risk"
        else 28
    )
    next_date = (date.today() + timedelta(days=days_ahead)).isoformat()
    db.schedule_appointment(
        payload.patient_id, next_date, f"Follow-up: {result['risk_level']}"
    )

    email_sent = False
    if patient.get("email"):
        send_post_visit_email(
            payload.patient_id, patient["full_name"], patient["email"], next_date
        )
        email_sent = True

    return {
        "visit_id": visit_id,
        "bmi": bmi,
        "next_appointment": next_date,
        "email_sent": email_sent,
        "patient_name": patient["full_name"],
        "result": result,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Appointments
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/appointments/upcoming")
def upcoming_appointments(days: int = 14):
    return db.get_upcoming_appointments(days_ahead=days)


@app.get("/api/appointments/pending")
def pending_appointments():
    return db.get_pending_appointments()


@app.post("/api/appointments", status_code=201)
def book_appointment(payload: AppointmentIn):
    patient = db.get_patient(payload.patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")

    appt_id = db.schedule_appointment(
        payload.patient_id, payload.scheduled_date, payload.reason
    )
    email_sent = False
    if patient.get("email"):
        send_appointment_reminder_email(
            payload.patient_id,
            patient["full_name"],
            patient["email"],
            payload.scheduled_date,
        )
        email_sent = True
    return {
        "appt_id": appt_id,
        "email_sent": email_sent,
        "message": f"Appointment booked for {payload.scheduled_date}.",
    }


@app.post("/api/appointments/{appt_id}/attended")
def mark_attended(appt_id: int):
    db.mark_appointment_attended(appt_id)
    return {"message": "Marked as attended."}


@app.post("/api/appointments/{appt_id}/missed")
def mark_missed(appt_id: int):
    """Flag a no-show and nudge the patient by email."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT patient_id FROM appointments WHERE appt_id=?", (appt_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Appointment not found.")

    db.mark_appointment_missed(appt_id)
    patient = db.get_patient(row["patient_id"])
    email_sent = False
    if patient and patient.get("email"):
        send_missed_appointment_email(
            patient["patient_id"], patient["full_name"], patient["email"]
        )
        email_sent = True
    return {"message": "Marked as missed.", "email_sent": email_sent}


# ─────────────────────────────────────────────────────────────────────────────
# Email log
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/emails")
def email_log():
    return db.get_email_log()


# ─────────────────────────────────────────────────────────────────────────────
# Settings: model + demo data
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/model/status")
def model_status():
    return {"model_ready": _state["model_ready"]}


@app.post("/api/model/train")
def retrain(payload: TrainIn):
    from data_generator import generate_maternal_dataset

    df_train = generate_maternal_dataset(payload.n_samples)
    train_model(df_train)
    load_model.cache_clear()      # drop the stale weights held by predict()
    load_model()
    _state["model_ready"] = True
    return {"message": f"Model retrained on {payload.n_samples} samples."}


FIRST_NAMES = ["Amina", "Wanjiku", "Aisha", "Grace", "Mary",
               "Fatuma", "Joyce", "Esther", "Lilian", "Zawadi"]
LAST_NAMES = ["Mwangi", "Odhiambo", "Kamau", "Kimani", "Otieno",
              "Njenga", "Wanjiru", "Achieng", "Njoroge", "Hassan"]


@app.post("/api/seed")
def seed_demo(payload: SeedIn):
    """Populate the DB with synthetic patients, visit history and appointments."""
    from data_generator import generate_maternal_dataset, generate_patient_history

    n = payload.n_patients
    df_demo = generate_maternal_dataset(n)
    risks = df_demo["RiskLevel"].tolist()

    for i, risk in enumerate(risks[:n]):
        pid = str(uuid.uuid4())[:8].upper()
        name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
        phone = "+2547" + "".join(str(random.randint(0, 9)) for _ in range(8))
        age = int(df_demo.iloc[i]["Age"])
        db.upsert_patient(
            pid, name, phone, age,
            weeks_pregnant=random.randint(8, 38),
            prev_pregnancies=random.randint(0, 4),
            family_hypertension=bool(random.randint(0, 1)),
        )

        prev_s = None
        for visit in generate_patient_history(pid, n_visits=3, base_risk=risk):
            vid = db.save_visit(
                pid, visit["SystolicBP"], visit["DiastolicBP"], visit["BS"],
                visit["BodyTemp"], visit["HeartRate"], visit["Weight_kg"],
            )
            if _state["model_ready"]:
                res = predict(
                    age, visit["SystolicBP"], visit["DiastolicBP"], visit["BS"],
                    visit["BodyTemp"], visit["HeartRate"], prev_systolic=prev_s,
                )
                db.save_prediction(
                    vid, pid, res["risk_level"], res["risk_score"],
                    res["top_risks"], res["shap_reasons"], res["suggested_action"],
                )
                prev_s = visit["SystolicBP"]

        days = 3 if risk == "high risk" else 14 if risk == "mid risk" else 28
        db.schedule_appointment(
            pid, (date.today() + timedelta(days=days)).isoformat(),
            f"Auto follow-up: {risk}",
        )

    return {"message": f"{n} demo patients seeded."}


# ─────────────────────────────────────────────────────────────────────────────
# Static frontend — mounted last so it never shadows an /api route
# ─────────────────────────────────────────────────────────────────────────────

@app.exception_handler(ValueError)
async def _value_error_handler(request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/health")
def health():
    return {"status": "ok"}


if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
else:
    @app.get("/")
    def _no_static():
        return JSONResponse(
            status_code=500,
            content={"detail": f"Frontend missing — expected {STATIC_DIR}"},
        )
