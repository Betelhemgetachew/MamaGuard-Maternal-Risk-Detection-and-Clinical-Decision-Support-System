### Phase 1: Data Entry and Cleaning

This is where the physical meets the digital.

1. **Smart Onboarding:** The nurse scans the patient's ID. If it's a first-time visit, the system prompts for baseline history (age, previous pregnancies, family history of hypertension).
    
2. **Edge-Case Vitals Entry:** As the nurse enters blood pressure or glucose, a **Real-time Validation Engine** checks for outliers.
    
    - _If BP is 200/120:_ The system flashes a red warning immediately, even before the AI runs.
        
3. **Data Normalization:** The system automatically converts all units and handles any missing fields using a "mean-imputation" strategy based on the patient's demographic.

### Phase 2: The AI "Brain"

The **AI Risk Prediction Model** is a multi-class classifier. This means it isn't just looking for "Healthy" or "At Risk." It's trained to recognize specific conditions like: Preeclampsia, Gestational Diabetes or Anemia
Once the "Save" button is hit, the engine wakes up.

1. **Feature Extraction:** The system doesn't just look at today's BP. It calculates the **Velocity of Change**—how much has her weight or pressure increased since the last visit?
    
2. **Ensemble Classification:** * **Model A (XGBoost)** analyzes the numeric vitals.
    
    - **Model B (Random Forest)** analyzes the historical risk factors.
        
    - **Consensus:** The models "vote" to determine the final risk score.
        
3. **Explainability Layer (SHAP):** The system generates a "Reasoning Code."
    
    - _Output:_ "Preeclampsia Risk: 85%. Trigger: 15% increase in Systolic BP + Proteinuria detected."
        
4. **Prescription Mapping:** The system cross-references the risk with the **WHO Maternal Care Handbook** and generates a suggested action plan (e.g., "Schedule follow-up in 3 days, start low-dose aspirin").

It calculates the probability for each and flags the **Top-3 Risks**. This helps a busy nurse focus on the most likely threats rather than drowning in a sea of "maybe." Since it is not enough for the model to say "High Risk"; it has to explain its "reasoning" so doctors actually trust it. There’s also a huge push for **Federated Learning**, which allows the AI to learn from data across different hospitals without actually sharing private patient records, keeping everything HIPAA-compliant and secure.

### Phase 3: The Feedback Loop

The output splits into two distinct channels to keep everyone in the loop:

- **For the Clinical Staff:** They get a dashboard showing _why_ the AI flagged a risk (e.g., "High BMI + rising blood pressure"). It also suggests interventions, like "order a glucose tolerance test."
    
- **For the Mother:** This is the "Safe" zone. You don't want an automated bot texting a mom, "You have a 78% chance of a seizure." That's a great way to cause a panic attack. Instead, it sends supportive, actionable info like appointment reminders and nutrition tips to keep her on track.

### Phase 4: Long-term Monitoring

The results are pushed to the relevant people.

1. **Clinical Dashboard Update:** The patient's name moves to the top of the "High Priority" list on the nurse's tablet.
    
2. **Safety-First Communication:** An automated, supportive SMS is sent to the mother.
    
    - _Script:_ "Hi [Name], great seeing you today! Remember to drink plenty of water and take your iron supplement before bed."
        
3. **Appointment Locking:** The next appointment is automatically booked in the system and synced with the SMS reminder schedule.

4. **Missed Appointment Handling:** If she misses an appointment, the system sends a SMS to the mother. If the mother misses 3 consecutive visits, the nurses are informed by the agent for human intervention. 

## Tools to be used:
1. **The "Brain" (Machine Learning & Modeling)**
You need tools that don't just predict, but also explain their reasoning to medical professionals.

- Scikit-Learn: Your bread and butter for classical ML algorithms (Random Forest, SVM) and building the initial pipeline.

- XGBoost / LightGBM: For handling the structured clinical data. These are the "gold standard" for tabular datasets like patient vitals.

- SHAP (SHapley Additive exPlanations): This is the Explainable AI (XAI) tool. It will generate the charts that tell a nurse why a patient was flagged (e.g., "Age + 15% increase in BP").

- Pandas & NumPy: For data manipulation and cleaning that messy clinical data.

2. **The "Memory" (Database)**
You need a system that can handle both rigid medical records and flexible AI logs.

- SQLite: For the structured patient records. It's lightweight and perfect for a clinic setting where you might not have a full SQL server.

3. **The "Face" (Frontend & Dashboard)**

- Streamlit: This is the fastest way to turn a Python script into a beautiful, interactive web app. It’s perfect for the "Nurse/CHW Dashboard."

- Plotly: For those interactive "Heat Maps" and trend lines showing a mother's health over time.

4. **The "Nerve System" (Communication & API)**
This is how the system reaches out to the real world.

- FastAPI: To build the API that connects your ML model to the dashboard. It’s lightning-fast and automatically documents itself.

- Africa's Talking: For the SMS Gateway. Africa's Talking is highly reliable for the Kenyan market and handles the "Two-way SMS" logic needed for patient check-ins.

- Celery + Redis: For the "Follow-up Monitor." This is a background task manager that checks every day at 5:00 PM: "Who missed their appointment? Send an alert now."

5. **Development & DevOp Essentials**
- We will use data from Kaggle's "Maternal Health Risk Data" for training and testing.  https://www.kaggle.com/datasets/csafrit2/maternal-health-risk-data

- GitHub: Obviously. Keep it organized with environment.yml or requirements.txt so others can replicate your work.

- Docker: To "wrap" your whole project so it runs the same on your laptop as it would on a clinic's server.



| Layer     | Tool (Original)                      | Hackathon-Optimized Version                                              | Why Better                               |
|----------|--------------------------------------|---------------------------------------------------------------------------|------------------------------------------|
| Brain    | XGBoost + Random Forest + SHAP      | XGBoost only + SHAP (one model)                                          | 96% acc proven; simpler, faster          |
| Data     | Synthetic + mean-impute             | Kaggle “Maternal Health Risk Data” (1,013 records) + Faker for history   | Real IoT data + velocity calc            |
| Frontend | Streamlit                           | Streamlit (single file)                                                   | Live in 10 mins, mobile-friendly         |
| Database | PostgreSQL + SQLAlchemy             | SQLite + Pandas (or Streamlit session state)                              | Zero setup                               |
| SMS      | Africa’s Talking                    | Africa’s Talking Sandbox (free dev key)                                   | Judge’s phone gets the SMS               |
| Dashboard| Plotly                              | Plotly + Streamlit built-in                                               | Instant heatmaps                         |
