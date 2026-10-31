# type: ignore
"""
AI Risk Prediction Model
- Primary: XGBoost classifier (falls back to RandomForest if XGBoost unavailable)
- Explainability: SHAP values (falls back to permutation importance)
- Multi-class: low risk / mid risk / high risk
- Extra: velocity-of-change features computed from visit history
"""

import json
import warnings
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder

warnings.filterwarnings("ignore")

MODEL_PATH   = Path(__file__).parent / "model.pkl"
ENCODER_PATH = Path(__file__).parent / "label_encoder.pkl"

FEATURE_COLS = [
    "Age", "SystolicBP", "DiastolicBP", "BS",
    "BodyTemp", "HeartRate",
    "Pulse_Pressure",           # Systolic - Diastolic
    "MAP",                      # Mean Arterial Pressure
    "BP_Velocity",              # Change in Systolic vs previous visit
    "Weight_Velocity",          # Weight gain since last visit
    "Family_Hypertension",
    "Proteinuria",
]

RISK_THRESHOLDS = {
    "high risk": 0.55,
    "mid risk":  0.35,
    "low risk":  0.00,
}

CONDITION_MAP = {
    "high risk": ["Preeclampsia", "Gestational Diabetes", "Severe Anemia"],
    "mid risk":  ["Gestational Hypertension", "Borderline Glucose", "Iron Deficiency"],
    "low risk":  ["Normal Progression"],
}

ACTION_MAP = {
    "high risk": (
        "🚨 URGENT: Schedule follow-up within 3 days. "
        "Consider low-dose aspirin. Order urine protein test & CBC. "
        "Refer to specialist if BP remains elevated."
    ),
    "mid risk": (
        "⚠️ MONITOR: Schedule follow-up in 1–2 weeks. "
        "Glucose tolerance test recommended. "
        "Increase dietary counselling. Monitor weight weekly."
    ),
    "low risk": (
        "✅ ROUTINE: Next antenatal visit in 4 weeks. "
        "Continue iron & folic acid supplements. "
        "Encourage hydration and balanced diet."
    ),
}


def _to_native(value):
    """Convert numpy scalars and nested containers to plain Python types."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {str(k): _to_native(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_native(v) for v in value]
    return value


# Feature Engineering

def engineer_features(
    age, systolic_bp, diastolic_bp, blood_sugar,
    body_temp, heart_rate,
    prev_systolic=None, prev_weight=None, curr_weight=None,
    family_hypertension=False, proteinuria=False,
) -> pd.DataFrame:
    """Compute derived features and return a one-row DataFrame."""
    pulse_pressure = systolic_bp - diastolic_bp
    map_val        = diastolic_bp + (pulse_pressure / 3)
    bp_velocity    = (systolic_bp - prev_systolic) if prev_systolic else 0.0
    w_velocity     = (curr_weight - prev_weight) if (prev_weight and curr_weight) else 0.0

    row = {
        "Age":               age,
        "SystolicBP":        systolic_bp,
        "DiastolicBP":       diastolic_bp,
        "BS":                blood_sugar,
        "BodyTemp":          body_temp,
        "HeartRate":         heart_rate,
        "Pulse_Pressure":    pulse_pressure,
        "MAP":               round(map_val, 2),
        "BP_Velocity":       round(bp_velocity, 2),
        "Weight_Velocity":   round(w_velocity, 2),
        "Family_Hypertension": int(family_hypertension),
        "Proteinuria":       int(proteinuria),
    }
    return pd.DataFrame([row])


# Model Training

def train_model(df: pd.DataFrame = None, save: bool = True):
    """Train classifier on the provided DataFrame (or generate synthetic data)."""
    if df is None:
        from data_generator import generate_maternal_dataset
        df = generate_maternal_dataset(1500)

    # Add derived columns
    df["Pulse_Pressure"]      = df["SystolicBP"] - df["DiastolicBP"]
    df["MAP"]                 = df["DiastolicBP"] + df["Pulse_Pressure"] / 3
    df["BP_Velocity"]         = 0.0   # No history in synthetic set
    df["Weight_Velocity"]     = 0.0
    df["Family_Hypertension"] = np.random.binomial(1, 0.25, len(df))
    df["Proteinuria"]         = np.where(df["RiskLevel"] == "high risk",
                                         np.random.binomial(1, 0.45, len(df)),
                                         np.random.binomial(1, 0.05, len(df)))

    le = LabelEncoder()
    y  = le.fit_transform(df["RiskLevel"])     # 0=high, 1=low, 2=mid (alphabetical)
    X  = df[FEATURE_COLS]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # Try XGBoost, fall back to RandomForest
    try:
        from xgboost import XGBClassifier
        clf = XGBClassifier(
            n_estimators=300,
            max_depth=5,
            learning_rate=0.05,
            use_label_encoder=False,
            eval_metric="mlogloss",
            random_state=42,
        )
        model_name = "XGBoost"
    except ImportError:
        clf = RandomForestClassifier(
            n_estimators=300,
            max_depth=10,
            min_samples_leaf=5,
            random_state=42,
            n_jobs=-1,
        )
        model_name = "RandomForest"

    clf.fit(X_train, y_train)
    y_pred = clf.predict(X_test)
    print(f"\n{'='*50}")
    print(f"Model: {model_name}")
    print(classification_report(y_test, y_pred, target_names=le.classes_))

    if save:
        joblib.dump(clf, MODEL_PATH)
        joblib.dump(le, ENCODER_PATH)
        print(f"Model saved → {MODEL_PATH}")

    return clf, le


@lru_cache(maxsize=1)
def load_model():
    """Load saved model and encoder, training if not found.

    Cached because predict() calls this on every request; under the FastAPI
    server that would otherwise re-read the pickles from disk per prediction.
    Call load_model.cache_clear() after retraining to pick up the new weights.
    """
    if not MODEL_PATH.exists() or not ENCODER_PATH.exists():
        print("No saved model found. Training now …")
        return train_model()
    return joblib.load(MODEL_PATH), joblib.load(ENCODER_PATH)


# Explainability (SHAP or Permutation Importance)

def explain_prediction(clf, X_row: pd.DataFrame, le: LabelEncoder) -> dict:
    """
    Return SHAP-style feature contributions for this prediction.
    Falls back to feature importance × delta-from-mean when SHAP unavailable.
    """
    try:
        import shap
        explainer = shap.TreeExplainer(clf)
        shap_vals = explainer.shap_values(X_row)   # shape: (n_classes, 1, n_features)
        pred_class_idx = int(clf.predict(X_row)[0])

        if isinstance(shap_vals, list):
            class_shap = shap_vals[pred_class_idx][0]
        else:
            class_shap = shap_vals[0]

        contributions = {
            feat: round(float(val), 4)
            for feat, val in zip(FEATURE_COLS, class_shap)
        }
    except Exception:
        # Fallback: feature_importance × normalised distance from safe mean
        safe_means = {
            "Age": 28, "SystolicBP": 115, "DiastolicBP": 75,
            "BS": 7.5, "BodyTemp": 98.2, "HeartRate": 76,
            "Pulse_Pressure": 40, "MAP": 90,
            "BP_Velocity": 0, "Weight_Velocity": 0,
            "Family_Hypertension": 0, "Proteinuria": 0,
        }
        fi = clf.feature_importances_
        row_vals = X_row.iloc[0]
        contributions = {}
        for i, feat in enumerate(FEATURE_COLS):
            delta = float(row_vals[feat]) - safe_means.get(feat, 0)
            contributions[feat] = round(fi[i] * delta, 4)

    # Sort by absolute magnitude, return top 5
    top5 = dict(
        sorted(contributions.items(), key=lambda x: abs(x[1]), reverse=True)[:5]
    )
    return top5


# Public Prediction API

def predict(
    age, systolic_bp, diastolic_bp, blood_sugar,
    body_temp, heart_rate,
    prev_systolic=None, prev_weight=None, curr_weight=None,
    family_hypertension=False, proteinuria=False,
) -> dict:
    """
    Run the full prediction pipeline.
    Returns a dict with: risk_level, risk_score, top_risks,
                         shap_reasons, suggested_action, probabilities
    """
    clf, le = load_model()

    X = engineer_features(
        age, systolic_bp, diastolic_bp, blood_sugar,
        body_temp, heart_rate,
        prev_systolic, prev_weight, curr_weight,
        family_hypertension, proteinuria,
    )

    proba   = clf.predict_proba(X)[0]
    pred_i  = int(np.argmax(proba))
    classes = le.classes_                        # alphabetical: high, low, mid

    risk_level  = classes[pred_i]
    risk_score  = round(float(proba[pred_i]) * 100, 1)   # as %

    # Build probability dict readable by humans
    probabilities = {cls: round(float(p) * 100, 1) for cls, p in zip(classes, proba)}

    # Top-3 conditions for the predicted risk tier
    top_risks = CONDITION_MAP.get(risk_level, [])

    # SHAP / feature-importance explanation
    shap_reasons = explain_prediction(clf, X, le)

    # Real-time critical override (before model even runs, mirror Phase 1)
    if systolic_bp >= 160 or diastolic_bp >= 110:
        risk_level = "high risk"
        risk_score = max(risk_score, 90.0)
        top_risks  = CONDITION_MAP["high risk"]
        shap_reasons["SystolicBP"] = shap_reasons.get("SystolicBP", 0) + 0.5

    suggested_action = ACTION_MAP[risk_level]

    return _to_native({
        "risk_level":        risk_level,
        "risk_score":        risk_score,
        "probabilities":     probabilities,
        "top_risks":         top_risks,
        "shap_reasons":      shap_reasons,
        "suggested_action":  suggested_action,
        "bp_velocity":       (systolic_bp - prev_systolic) if prev_systolic else 0,
    })


if __name__ == "__main__":
    clf, le = train_model()
    result = predict(35, 148, 95, 11.2, 99.5, 88,
                     prev_systolic=130, family_hypertension=True, proteinuria=True)
    print("\nSample prediction:")
    for k, v in result.items():
        print(f"  {k}: {v}")
