# MamaGuard: Maternal Risk Detection & Clinical Decision Support

A prototype decision-support tool for antenatal clinics. A nurse records a mother's vitals, and MamaGuard returns a **risk tier with an explanation**, books the **next follow-up automatically**, keeps the mother engaged with **supportive email reminders**, and lets staff ask a **protocol assistant** questions that are answered only from the Kenya Ministry of Health obstetric protocols.

**Live demo:** https://mamaguard-7p1h.onrender.com (free hosting, so the first load can take up to a minute; the demo patients are synthetic and reset on restart).

> ⚠️ **Research prototype.** The risk model is trained on synthetic data and has not been clinically validated. It is not for use in patient care.

<!-- Add a screenshot or GIF of the dashboard here: ![MamaGuard dashboard](docs/screenshot.png) -->

---

## The problem

Many preventable maternal complications in Kenya and similar settings are linked to three gaps in routine antenatal care:

1. **Warning signs are missed.** Rising blood pressure or glucose can go unnoticed in a busy clinic.
2. **Follow-up breaks down.** Missed appointments are not tracked, so high-risk mothers drop out of care.
3. **Guidance is hard to reach.** Protocol documents are long, and staff need quick, reliable answers at the point of care.

MamaGuard tackles each gap with one component.

## What it does

| Component | What it does | How |
|---|---|---|
| **Risk assessment** | Classifies each visit as low, mid or high risk and shows the top factors behind the result | XGBoost classifier + SHAP explanations |
| **Safety override** | Forces "high risk" for severe-range blood pressure (≥160 systolic or ≥110 diastolic), whatever the model says | Rule applied after the model |
| **Care continuity** | Books the next visit (3 / 14 / 28 days by tier), sends reminders, and alerts nurses after 3 consecutive missed appointments | SQLite + APScheduler (daily 09:00 reminders, 17:00 missed-visit check) + SMTP email |
| **Protocol assistant** | Answers clinical questions using only the MoH *Basic Obstetric Protocols*, cites the pages, and refuses when the protocol doesn't cover the question | RAG: ChromaDB retrieval + Llama 3.3 70B via Groq |
| **Dashboard** | Patient registry, visit entry, risk distribution, appointments, email log | FastAPI backend + HTML/CSS/JavaScript frontend |

**Design choice: patients never receive risk scores.** Emails to mothers carry only appointment details and supportive messages. Risk information stays on the clinician dashboard so automated messages can't cause alarm.

## Technical highlights

- **Dosage-safe chunking for RAG.** A chunk that ends in "give 400" followed by one starting "µg folic acid" would let the assistant quote half a dose. `ingest.py` uses a dosage grammar (mg, µg, IU, mL/hr, mmHg, …) to make sure no chunk boundary ever splits a dose, and it checks every dose from the source PDF against the finished chunks.
- **Refusal before generation.** If retrieval finds nothing close enough (cosine-distance threshold), the assistant refuses *without calling the LLM*. When the model is called, it runs at temperature 0 and must answer only from the retrieved context.
- **Explainability.** Each prediction comes with its top 5 SHAP feature contributions, so a nurse can see *why* a visit was flagged.
- **Feature engineering.** Pulse pressure, mean arterial pressure (MAP), and change in systolic BP and weight since the previous visit.
- **Mock modes.** Without SMTP or Groq credentials, the app still runs end to end: emails are printed to the terminal and the assistant returns the retrieved protocol passages instead of a generated answer.

## Results and limitations

On a held-out 20% split of **synthetic** data (1,500 records; body temperature in °C, matching the clinic form), the XGBoost model reaches:

| Class | Precision | Recall | F1 |
|---|---|---|---|
| High risk | 0.91 | 0.95 | 0.93 |
| Mid risk | 0.86 | 0.84 | 0.85 |
| Low risk | 0.93 | 0.93 | 0.93 |
| **Accuracy** | | | **0.90** |

These numbers show the pipeline works. They are **not** evidence of clinical performance:

- The training data is generated from hand-picked distributions for each risk class, so the classes are easier to separate than real patients would be.
- Proteinuria and family history are simulated, and the visit-to-visit change features are always zero in training, so the model has not learned to use BP or weight trends yet.
- "Likely conditions" (e.g. preeclampsia) are a fixed list for each risk tier, not separate predictions.

**Next steps:** retrain on the real [Maternal Health Risk dataset](https://archive.ics.uci.edu/dataset/863/maternal+health+risk) (UCI/Kaggle, collected in Bangladesh) and report cross-validated results; validate with clinicians against WHO and MoH guidance; add longitudinal data so the trend features carry signal.

## Run it locally

```bash
git clone https://github.com/Betelhemgetachew/MamaGuard-Maternal-Risk-Detection-and-Clinical-Decision-Support-System.git
cd MamaGuard-Maternal-Risk-Detection-and-Clinical-Decision-Support-System
pip install -r requirements.txt

cp .env.example .env      # optional: add GROQ_API_KEY and SMTP settings
python model.py           # train the risk model (also happens automatically on first run)
python app.py             # opens http://127.0.0.1:8000  (API docs at /docs)
```

To enable the protocol assistant, put the MoH *Basic Obstetric Protocols* PDF in the project folder and run `python ingest.py` once to build the knowledge base (`chroma_db/`). Everything else works without it.

To try the dashboard with demo data, use the **Seed Demo Data** tab in the app (or `POST /api/seed`).

Run the tests:

```bash
python -m unittest discover tests
```

## Deploy

The repo includes a `render.yaml` for Render's free plan. In Render choose **New > Blueprint**, pick this repo, and paste a Groq API key when asked (optional; without it the protocol assistant is switched off).

On a fresh server the app trains the model on synthetic data and, because `SEED_DEMO` is on, fills the empty database with synthetic demo patients. The free plan wipes the disk on each restart, so the demo resets to that state every time.

## Project structure

```
├── app.py               # Launcher: checks setup, starts the server
├── main.py              # FastAPI backend: patients, visits, appointments, model, /chat
├── model.py             # Feature engineering, XGBoost training, SHAP explanations
├── ingest.py            # Protocol PDF → dosage-safe chunks → ChromaDB
├── database.py          # SQLite schema and queries
├── scheduler.py         # Daily reminder and missed-appointment jobs
├── email_service.py     # SMTP email with mock mode
├── data_generator.py    # Synthetic patients and visit histories
├── static/              # Frontend (HTML, CSS, JavaScript)
├── tests/
└── docs/                # Concept note and system design
```

## Privacy

- Patient records are stored in a local SQLite database, and no patient data is uploaded to the cloud. The protocol assistant only sends the clinician's question and protocol passages to the LLM, never patient records.
- The repository contains synthetic data only.
- A real deployment would need a compliance review under Kenya's Data Protection Act, 2019, plus access control and encryption at rest. These are not implemented in this prototype.

## Author

**Betelhem Getachew Kebede**: Data Science & Analytics, USIU-Africa
<!-- Add your LinkedIn / portfolio link here -->

Licensed under the terms in [LICENSE](LICENSE).
