# DentVoice AI MVP

DentVoice AI is a lightweight MVP for an AI receptionist and revenue-recovery platform for dental clinics and other service businesses. This prototype includes:

- A FastAPI backend
- A clinic dashboard UI
- A public landing page
- Real-time Twilio-style call handling
- Live appointment capture during the call
- Emergency detection
- WhatsApp follow-up stubs
- A Twilio-compatible voice webhook flow
- SQLite persistence for free local storage
- Admin booking form and clinic settings
- Editable booking slots
- CSV export for appointments and call logs
- Search and filters for appointments and calls
- Appointment notes, edit, and delete workflows
- Lead scoring for call prioritization
- Free analytics and docs pages
- Calendar view for appointments
- Patient profile summaries
- Contact/demo request form
- Self-serve free workspace signup
- Audit log for product activity
- Double-booking protection
- In-app toast notifications
- Free local admin authentication
- Saved browser-based filters for operational pages
- Receptionist follow-up task workflow
- Onboarding walkthrough for new users
- Clinic branding with custom logo text, tagline, and accent color
- Visual analytics with chart-style summaries
- ROI calculator and pricing-led landing page
- Admin setup workspace checklist
- Industry template library for dental, dermatology, physiotherapy, real estate, and salons
- Revenue-recovered analytics and trial-to-paid growth tracking
- Lead pipeline board for demo requests and SaaS conversion stages
- White-label clinic mode for agencies and multi-clinic operators
- Team management page with admin and receptionist user controls
- Password change and reset flow for local auth
- Notification center with read/unread state
- Drag-and-drop lead pipeline stage updates
- Drag-and-drop calendar rescheduling by day
- Clinic benchmark reports and HQ dashboard
- Referral and reseller workflow tracking
- Smarter reports center with downloadable summary and benchmark CSVs
- Internal comment threads for leads, tasks, and patients
- Simulated onboarding email queue
- Advanced search segmentation and tag-based workflows
- Task SLA tracking and response-state visibility
- Industry case-study pages for vertical sales conversations
- Persistent onboarding step tracking and launch-readiness scoring
- Industry demo presets for faster workspace activation
- Staff performance dashboard and access logs
- Internal announcement center
- Basic no-code automation rules for notifications, tasks, and reminders
- Team user activation/deactivation controls

## Project Structure

- `app/main.py` - FastAPI app, SQLite-backed business logic, and Twilio-style voice workflow
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

- `GET /` - Public landing page
- `GET /dashboard` - Admin dashboard
- `GET /login` - Local clinic admin login
- `GET /setup` - Admin workspace setup checklist
- `GET /leads` - Demo request CRM and lead pipeline
- `GET /team` - Team management and password controls
- `GET /notifications` - Notification center
- `GET /hq` - Franchise / multi-branch HQ dashboard
- `GET /benchmarks` - Clinic benchmark reports
- `GET /reports` - Summary reports and onboarding email queue
- `GET /solutions/{business_type}` - Industry case-study landing pages
- `GET /appointments` - Appointment management page
- `GET /calls` - Call management page
- `GET /analytics` - Analytics page
- `GET /calendar` - Calendar-style appointment view
- `GET /patients` - Patient profiles and history
- `GET /audit` - Audit log
- `GET /docs` - Product/docs page
- `GET /health` - Health check for deployment
- `GET /api/dashboard` - JSON dashboard data
- `GET /api/available-slots` - Available booking slots
- `GET /api/export/appointments.csv` - Export appointments
- `GET /api/export/calls.csv` - Export calls
- `GET /api/export/summary.csv` - Export clinic summary report
- `GET /api/export/benchmarks.csv` - Export network benchmark report
- `POST /api/contact-request` - Save landing-page demo request
- `POST /api/trial-signup` - Create a self-serve clinic workspace from the public site
- `POST /api/simulate-call` - Simulate an inbound patient call
- `POST /api/appointments` - Create an appointment request
- `POST /api/admin/appointments` - Create appointment from dashboard
- `POST /api/appointments/{appointment_id}/update` - Edit appointment details and notes
- `POST /api/appointments/{appointment_id}/delete` - Delete appointment
- `POST /api/calls/{call_id}/lead-score` - Update call priority
- `POST /api/settings` - Update clinic settings
- `POST /api/receptionist-tasks` - Create receptionist follow-up task
- `POST /api/receptionist-tasks/{task_id}/update` - Update receptionist follow-up task
- `POST /api/team/users` - Create a clinic user
- `POST /api/team/users/{user_id}/update` - Update role, display name, or password
- `POST /api/password/change` - Change the logged-in user's password
- `POST /api/team/users/{user_id}/toggle` - Activate or deactivate a team user
- `POST /api/notifications/{notification_id}/read` - Mark a notification as read
- `POST /api/announcements` - Post an internal team announcement
- `POST /api/onboarding/steps/{step_key}` - Mark onboarding setup progress
- `POST /api/onboarding/preset` - Load industry-specific demo preset data
- `POST /api/automation-rules` - Create a no-code workflow automation rule
- `POST /api/referrals` - Create a referral / reseller lead
- `POST /api/onboarding-emails` - Queue a simulated onboarding email
- `POST /api/comments` - Add an internal comment to a patient, lead, or task
- `POST /api/calendar/appointments/{appointment_id}/move` - Move an appointment to another day
- `POST /api/slots` - Add booking slot
- `POST /api/slots/{slot_id}/delete` - Remove booking slot
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
- Connect real Twilio voice and WhatsApp APIs
- Add multi-user roles and stronger production auth
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
