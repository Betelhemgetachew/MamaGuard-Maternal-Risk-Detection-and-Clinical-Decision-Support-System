import json
import unittest

from fastapi.testclient import TestClient

import database as db
from main import app, _state
from model import load_model, predict


class ModelSerializationTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        load_model()
        _state["model_ready"] = True

    def test_predict_result_is_json_serializable(self):
        result = predict(
            age=30,
            systolic_bp=120,
            diastolic_bp=80,
            blood_sugar=7.2,
            body_temp=36.8,
            heart_rate=76,
        )
        json.dumps(result)

    def test_create_visit_endpoint_returns_201(self):
        client = TestClient(app)
        patient_resp = client.post(
            "/api/patients",
            json={
                "full_name": "Regression Tester",
                "phone": "+254700000001",
                "email": "regression@example.com",
                "age": 29,
                "weeks_pregnant": 24,
                "prev_pregnancies": 1,
                "family_hypertension": False,
            },
        )
        self.assertEqual(patient_resp.status_code, 201)

        visit_resp = client.post(
            "/api/visits",
            json={
                "patient_id": patient_resp.json()["patient_id"],
                "systolic_bp": 120,
                "diastolic_bp": 80,
                "blood_sugar": 7.2,
                "body_temp": 36.8,
                "heart_rate": 76,
                "weight_kg": 65,
                "height_cm": 165,
                "proteinuria": False,
                "notes": "regression test",
            },
        )
        self.assertEqual(visit_resp.status_code, 201)


if __name__ == "__main__":
    unittest.main()
