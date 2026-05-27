# DentVoice AI MVP

DentVoice AI is a lightweight MVP for an AI receptionist built for dental clinics. This prototype includes:

- A FastAPI backend
- A clinic dashboard UI
- Real-time Twilio-style call handling
- Live appointment capture during the call
- Emergency detection
- WhatsApp follow-up stubs
- A Twilio-compatible voice webhook flow

## Project Structure

- `app/main.py` - FastAPI app and in-memory business logic
- `templates/dashboard.html` - Dashboard UI
- `static/styles.css` - Dashboard styling
- `static/app.js` - Demo actions
- `requirements.txt` - Python dependencies
- `Dockerfile` - Container deployment setup
- `render.yaml` - Render deployment config
- `LEARN-DEPLOY-LINKEDIN-GUIDE.md` - Beginner-friendly product, deployment, and LinkedIn guide

## Run Locally

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Start the app:

```bash
uvicorn app.main:app --reload
```

4. Open [http://127.0.0.1:8000](http://127.0.0.1:8000)

## Main Endpoints

- `GET /` - Dashboard
- `GET /health` - Health check for deployment
- `GET /api/dashboard` - JSON dashboard data
- `GET /api/available-slots` - Available booking slots
- `POST /api/simulate-call` - Simulate an inbound patient call
- `POST /api/appointments` - Create an appointment request
- `POST /voice/incoming` - Twilio voice webhook
- `POST /voice/process-main-menu` - Handle live menu selection
- `POST /voice/process-booking-name` - Capture patient name by voice
- `POST /voice/process-booking-slot` - Capture appointment slot
- `POST /voice/process-booking-reason` - Capture visit reason and book
- `POST /api/demo/seed` - Seed the dashboard with demo data

## Example Simulated Call

```json
{
  "caller_number": "+919876543210",
  "patient_name": "Demo Patient",
  "transcript": "I want to book a consultation for tooth pain tomorrow",
  "preferred_date": "2026-05-15",
  "preferred_time": "11:00 AM",
  "reason_for_visit": "Tooth pain"
}
```

## Suggested Next Steps

- Replace menu-based slot selection with OpenAI-powered natural extraction
- Add Supabase or MongoDB persistence
- Connect real Twilio voice and WhatsApp APIs
- Add login, clinic settings, and user roles
- Integrate calendar and appointment confirmation workflows

## Deploy on Render

This repo now includes:

- `Dockerfile`
- `render.yaml`
- `GET /health`

Manual Render settings:

```text
Build Command: pip install -r requirements.txt
Start Command: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## Beginner-Friendly Guide

If you are learning while building, start here:

- [LEARN-DEPLOY-LINKEDIN-GUIDE.md](/Users/apple/Documents/Codex/2026-05-14-build-me-a-product-brief-ai/LEARN-DEPLOY-LINKEDIN-GUIDE.md)

## Connect a Real Twilio Number

1. Expose your local app with a public HTTPS URL such as ngrok.
2. In the Twilio phone number configuration, set the incoming call webhook to:

```text
POST https://your-public-url/voice/incoming
```

3. Twilio will then guide callers through:

- Main menu selection
- Name capture
- Slot selection
- Visit reason capture
- On-call appointment confirmation

4. Completed bookings immediately appear on the dashboard and trigger a WhatsApp confirmation stub.
