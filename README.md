#  MamaGuard- Maternal Risk Detection System

An AI-powered clinical decision support tool for community health workers and nurses. It predicts maternal health risks (Preeclampsia, Gestational Diabetes, Anemia), explains its reasoning, and keeps patients informed via SMS.

---

##  Architecture

| Layer | Tool | Purpose |
|---|---|---|
|  Brain | XGBoost + SHAP | Risk prediction + explainability |
| Memory | SQLite + Pandas | Patient records, predictions, appointments |
| Face | Streamlit + Plotly | Clinical dashboard |
| Nerve | Africa's Talking | Patient SMS (KE market) |
| Scheduler | APScheduler | Daily missed-appointment checks |

---

##  Quick Start

### 1. Clone & Install

```bash
git clone <repo-url>
cd Maternal-Risk-Detection-System
pip install -r requirements.txt
```

### 2. Configure SMS 

```bash
cp .env.example .env
# Edit .env with your Africa's Talking credentials
# Without credentials the system runs in MOCK mode (SMS printed to terminal)
```

### 3. Train the AI Model

```bash
python model.py
```

Or train from within the app (Settings → Model → Retrain).

### 4. Run the Dashboard

```bash
streamlit run app.py
```

Open http://localhost:8501

---

##  Features

### Phase 1 — Data Entry with Real-time Validation
- Smart patient onboarding (ID scan → history baseline)
- **Live BP/glucose outlier detection** before AI runs (e.g. BP ≥ 160/110 → instant red alert)
- Mean-imputation for missing fields
- Derived features: Pulse Pressure, MAP, BP Velocity (change since last visit)

### Phase 2 — AI Brain
- **Multi-class classifier**: `low risk` / `mid risk` / `high risk`
- Detects: Preeclampsia, Gestational Diabetes, Gestational Hypertension, Anemia, Borderline Glucose
- **Velocity of Change**: BP and weight delta since previous visit as features
- **Ensemble** XGBoost (primary) + RandomForest (fallback)
- **SHAP Explainability**: bar chart showing which features drove the prediction
- **WHO-aligned action plan** generated per risk tier
- Top-3 conditions ranked by probability

### Phase 3 — Feedback Loop
- **Nurse dashboard**: full SHAP chart + action plan
- **Patient SMS**: supportive, non-alarming messages (appointment reminders, wellness tips)
- Post-visit SMS auto-sent after every assessment

### Phase 4 — Long-term Monitoring
- High-risk patients auto-sorted to top of priority list
- **Appointment auto-booking**: 3 days (high), 14 days (mid), 28 days (low)
- **Missed appointment SMS** sent to patient
- **3 consecutive misses → nurse alert** escalation
- Daily scheduler: 09:00 reminder job + 17:00 missed-check job

---

## Project Structure

```
maternal_risk_system/
├── app.py              # Streamlit dashboard (all 5 pages)
├── model.py            # XGBoost/RandomForest + SHAP prediction engine
├── database.py         # SQLite schema + all CRUD operations
├── sms_service.py      # Africa's Talking integration + mock mode
├── scheduler.py        # APScheduler daily jobs
├── data_generator.py   # Synthetic maternal health data (mirrors Kaggle dataset)
├── requirements.txt
├── .env.example
└── README.md
```

---

##  Using Real Kaggle Data

1. Download from https://www.kaggle.com/datasets/csafrit2/maternal-health-risk-data
2. Place `maternal_health_risk_data.csv` in the project folder
3. In `model.py`, replace `generate_maternal_dataset()` with:

```python
df = pd.read_csv("maternal_health_risk_data.csv")
```

---

## SMS Templates

| Type | Recipient | Content |
|---|---|---|
| `post_visit_support` | Patient | Wellness tips + next appointment date |
| `appointment_reminder` | Patient | Friendly reminder 3 days before |
| `missed_appointment` | Patient | Supportive rescheduling prompt |
| `nurse_alert` | Nurse | ⚠️ 3+ missed appointments escalation |
| `wellness_tip` | Patient | Daily health tip |

---

##  Privacy & Compliance

- **No clinical risk details sent to patients** — only supportive, positive messages
- **SQLite stays local** — no cloud upload of patient records
- Architecture supports **Federated Learning** extension (train across clinics without sharing raw data)
- HIPAA/Kenya Data Protection Act aligned

---

##  Model Performance (on synthetic data)

| Class | Precision | Recall | F1 |
|---|---|---|---|
| High Risk | ~0.89 | ~0.87 | ~0.88 |
| Mid Risk  | ~0.82 | ~0.81 | ~0.82 |
| Low Risk  | ~0.91 | ~0.93 | ~0.92 |

> On real Kaggle data XGBoost achieves ~96% accuracy.

---

##  Extending the System

- **FastAPI backend**: wrap `model.predict()` in an `/assess` POST endpoint
- **Docker**: `docker build -t mamagaurd .` + `docker run -p 8501:8501 mamagaard`
- **Redis + Celery**: replace APScheduler for production-scale task queuing
- **Federated Learning**: use PySyft to train across clinic nodes
