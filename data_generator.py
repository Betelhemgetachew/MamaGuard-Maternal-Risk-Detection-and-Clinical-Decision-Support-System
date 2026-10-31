# type: ignore
"""
Data Generator for Maternal Health Risk System
Generates synthetic data matching the Kaggle 'Maternal Health Risk Data' distribution.
Columns: Age, SystolicBP, DiastolicBP, BS (Blood Sugar mmol/L), BodyTemp (F), HeartRate, RiskLevel
"""

import random
import string
from datetime import date, timedelta

import numpy as np
import pandas as pd

np.random.seed(42)
random.seed(42)


def generate_maternal_dataset(n_samples: int = 1200) -> pd.DataFrame:
    """Generate synthetic maternal health records that mirror the Kaggle dataset."""
    records = []

    for _ in range(n_samples):
        risk_level = np.random.choice(
            ["low risk", "mid risk", "high risk"], p=[0.40, 0.33, 0.27]
        )

        if risk_level == "low risk":
            age         = int(np.clip(np.random.normal(28, 6), 15, 45))
            systolic    = int(np.clip(np.random.normal(115, 8), 90, 135))
            diastolic   = int(np.clip(np.random.normal(76, 7), 60, 90))
            bs          = round(np.clip(np.random.normal(7.5, 1.2), 6.0, 11.0), 1)
            body_temp   = round(np.clip(np.random.normal(98.2, 0.6), 97.0, 99.5), 1)
            heart_rate  = int(np.clip(np.random.normal(76, 8), 60, 100))

        elif risk_level == "mid risk":
            age         = int(np.clip(np.random.normal(32, 7), 15, 50))
            systolic    = int(np.clip(np.random.normal(130, 10), 110, 155))
            diastolic   = int(np.clip(np.random.normal(85, 8), 70, 100))
            bs          = round(np.clip(np.random.normal(8.8, 1.5), 7.0, 13.0), 1)
            body_temp   = round(np.clip(np.random.normal(98.7, 0.8), 97.5, 101.0), 1)
            heart_rate  = int(np.clip(np.random.normal(82, 9), 65, 105))

        else:  # high risk
            age         = int(np.clip(np.random.normal(38, 8), 15, 55))
            systolic    = int(np.clip(np.random.normal(148, 15), 120, 200))
            diastolic   = int(np.clip(np.random.normal(95, 12), 80, 130))
            bs          = round(np.clip(np.random.normal(11.5, 2.0), 9.0, 19.0), 1)
            body_temp   = round(np.clip(np.random.normal(100.1, 1.2), 98.0, 103.5), 1)
            heart_rate  = int(np.clip(np.random.normal(90, 12), 70, 120))

        records.append(
            {
                "Age": age,
                "SystolicBP": systolic,
                "DiastolicBP": diastolic,
                "BS": bs,
                "BodyTemp": body_temp,
                "HeartRate": heart_rate,
                "RiskLevel": risk_level,
            }
        )

    return pd.DataFrame(records)


def _random_date_between(start_months_ago=6):
    """Return a random date between N months ago and today."""
    total_days = start_months_ago * 30
    offset = random.randint(0, total_days)
    return date.today() - timedelta(days=offset)


def generate_patient_history(
    patient_id: str,
    n_visits: int = 4,
    base_risk: str = "low risk",
) -> list[dict]:
    """Generate a longitudinal visit history for one patient."""
    history = []
    base_systolic = 115 if base_risk == "low risk" else 130 if base_risk == "mid risk" else 148

    for i in range(n_visits):
        visit_date = _random_date_between(6)
        # Slight upward drift over time
        drift = i * np.random.uniform(1, 3)
        history.append(
            {
                "patient_id": patient_id,
                "visit_date": visit_date.isoformat(),
                "SystolicBP": int(base_systolic + drift + np.random.normal(0, 4)),
                "DiastolicBP": int((base_systolic * 0.65) + drift * 0.5 + np.random.normal(0, 3)),
                "BS": round(7.5 + i * 0.3 + np.random.normal(0, 0.5), 1),
                "BodyTemp": round(98.2 + np.random.normal(0, 0.5), 1),
                "HeartRate": int(76 + np.random.normal(0, 5)),
                "Weight_kg": round(65 + i * 0.8 + np.random.normal(0, 1.5), 1),
            }
        )
    return sorted(history, key=lambda x: x["visit_date"])


if __name__ == "__main__":
    df = generate_maternal_dataset(1200)
    print(df["RiskLevel"].value_counts())
    print(df.describe().round(2))
