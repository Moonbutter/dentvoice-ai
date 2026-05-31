from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from os import getenv
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus
from uuid import uuid4

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware


class FAQAnswer(BaseModel):
    question: str
    answer: str


class AppointmentRequest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    patient_name: str
    phone_number: str
    preferred_date: str
    preferred_time: str
    reason_for_visit: str
    status: Literal["new", "confirmed", "completed", "cancelled", "needs_follow_up"] = "confirmed"
    source: Literal["api", "simulated_call", "voice_call", "admin"] = "api"
    notes: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class CallRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    caller_number: str
    patient_name: str | None = None
    intent: Literal[
        "appointment_booking",
        "reschedule",
        "pricing",
        "directions",
        "faq",
        "emergency",
        "general",
    ] = "general"
    summary: str
    urgent: bool = False
    lead_score: Literal["hot", "warm", "cold"] = "warm"
    internal_notes: str = ""
    appointment_request: AppointmentRequest | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class WhatsAppMessage(BaseModel):
    phone_number: str
    message: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class SimulatedCallPayload(BaseModel):
    caller_number: str
    transcript: str
    patient_name: str | None = None
    preferred_date: str | None = None
    preferred_time: str | None = None
    reason_for_visit: str | None = None


class CallSession(BaseModel):
    call_sid: str
    caller_number: str
    intent: str | None = None
    patient_name: str | None = None
    preferred_date: str | None = None
    preferred_time: str | None = None
    reason_for_visit: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class SlotInput(BaseModel):
    date: str
    time: str


class ClinicSettingsInput(BaseModel):
    clinic_name: str
    clinic_timings: str
    clinic_address: str
    brand_tagline: str
    accent_color: str
    logo_text: str
    business_type: str
    avg_booking_value: int
    white_label_enabled: bool
    white_label_name: str
    reseller_code: str
    working_days: str
    working_hours: str
    auto_callback_enabled: bool


BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / "dentvoice.db"
ASSET_VERSION = "20260531-3"

FAQS = [
    FAQAnswer(question="What are your clinic timings?", answer="We are open Monday to Saturday from 9 AM to 8 PM."),
    FAQAnswer(question="Do you offer braces and aligners?", answer="Yes, the clinic offers orthodontic consultations for braces and clear aligners."),
    FAQAnswer(question="Where is the clinic located?", answer="We are located near the main market with parking available for patients."),
    FAQAnswer(question="Is a consultation available today?", answer="Same-day consultation depends on doctor availability, and we can help request a slot."),
]

INDUSTRY_TEMPLATES = {
    "dental": {
        "label": "Dental Clinics",
        "accent_color": "#146c78",
        "tagline": "AI front desk for missed patient calls and chairside booking recovery",
        "working_days": "Mon,Tue,Wed,Thu,Fri,Sat",
        "working_hours": "09:00-20:00",
        "avg_booking_value": 8000,
        "timings_label": "Monday to Saturday, 9 AM to 8 PM",
        "faqs": [
            FAQAnswer(question="Do you handle braces and aligners?", answer="Yes, the clinic offers braces, aligners, and smile-design consultations."),
            FAQAnswer(question="Can I book a cleaning or pain consultation today?", answer="Same-day appointments depend on doctor availability, but the receptionist can request the earliest available slot."),
            FAQAnswer(question="Do you offer implants and cosmetic dentistry?", answer="Yes, the clinic supports implants, cosmetic dentistry, and treatment-planning consultations."),
        ],
    },
    "dermatology": {
        "label": "Dermatology / Cosmetic Clinics",
        "accent_color": "#9b5de5",
        "tagline": "AI front desk for high-value skin, hair, and aesthetic inquiries",
        "working_days": "Mon,Tue,Wed,Thu,Fri,Sat",
        "working_hours": "10:00-20:00",
        "avg_booking_value": 25000,
        "timings_label": "Monday to Saturday, 10 AM to 8 PM",
        "faqs": [
            FAQAnswer(question="Do you offer skin and hair consultations?", answer="Yes, the clinic handles skin, hair, and cosmetic consultation requests."),
            FAQAnswer(question="Can I ask about treatment pricing?", answer="Yes, pricing guidance can be shared and a consultation slot can be requested for a detailed plan."),
            FAQAnswer(question="Do you handle after-hours inquiry follow-up?", answer="Yes, the clinic can capture your details and route the follow-up to the team."),
        ],
    },
    "physiotherapy": {
        "label": "Physiotherapy Clinics",
        "accent_color": "#2a9d8f",
        "tagline": "AI appointment desk for repeat therapy scheduling and callback recovery",
        "working_days": "Mon,Tue,Wed,Thu,Fri,Sat",
        "working_hours": "08:00-19:00",
        "avg_booking_value": 3000,
        "timings_label": "Monday to Saturday, 8 AM to 7 PM",
        "faqs": [
            FAQAnswer(question="Do you handle sports injury and pain sessions?", answer="Yes, the clinic can capture therapy requests for pain relief, mobility, and sports recovery."),
            FAQAnswer(question="Can I reschedule a therapy session?", answer="Yes, the receptionist can help request a new session time."),
            FAQAnswer(question="Do repeat sessions need reminders?", answer="Yes, reminder workflows can support follow-up therapy appointments."),
        ],
    },
    "real_estate": {
        "label": "Real Estate Teams",
        "accent_color": "#c75c2a",
        "tagline": "AI lead desk for missed property inquiries and site-visit scheduling",
        "working_days": "Mon,Tue,Wed,Thu,Fri,Sat,Sun",
        "working_hours": "09:00-21:00",
        "avg_booking_value": 50000,
        "timings_label": "All week, 9 AM to 9 PM",
        "faqs": [
            FAQAnswer(question="Can I ask about budget and location fit?", answer="Yes, the receptionist can capture budget, preferred location, and site-visit requests."),
            FAQAnswer(question="Can a broker call me back later?", answer="Yes, missed lead recovery tasks can be created automatically for broker follow-up."),
            FAQAnswer(question="Can I book a site visit?", answer="Yes, the workflow can capture and route site-visit interest."),
        ],
    },
    "salon": {
        "label": "Salons / Spas",
        "accent_color": "#d97706",
        "tagline": "AI booking desk for peak-hour appointments and repeat customer recovery",
        "working_days": "Mon,Tue,Wed,Thu,Fri,Sat,Sun",
        "working_hours": "10:00-21:00",
        "avg_booking_value": 2500,
        "timings_label": "All week, 10 AM to 9 PM",
        "faqs": [
            FAQAnswer(question="Can I book a weekend slot?", answer="Yes, the receptionist can capture weekend booking requests and preferred times."),
            FAQAnswer(question="Do you handle bridal or premium services?", answer="Yes, premium service inquiries can be captured and routed for follow-up."),
            FAQAnswer(question="Can someone confirm my booking later?", answer="Yes, reminder and callback workflows can support the booking process."),
        ],
    },
}

APPOINTMENT_STATUSES = ["new", "confirmed", "completed", "cancelled", "needs_follow_up"]
CALL_INTENTS = ["appointment_booking", "reschedule", "pricing", "directions", "faq", "emergency", "general"]
LEAD_SCORES = ["hot", "warm", "cold"]
TASK_STATUSES = ["open", "in_progress", "done"]
TASK_PRIORITIES = ["high", "medium", "low"]
APPOINTMENT_SOURCES = ["admin", "voice_call", "simulated_call", "api"]
CONTACT_STATUSES = ["new", "contacted", "qualified", "demo_booked", "trial_active", "paid", "closed"]
BUSINESS_TYPES = list(INDUSTRY_TEMPLATES.keys())

call_sessions: dict[str, CallSession] = {}

app = FastAPI(title="DentVoice AI MVP")
app.add_middleware(SessionMiddleware, secret_key=getenv("DENTVOICE_SESSION_SECRET", "dentvoice-local-secret"))
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def get_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def column_exists(db: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = db.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


def next_business_day(start: date, *, days_ahead: int = 0) -> date:
    current = start
    if days_ahead:
        current += timedelta(days=days_ahead)
    while current.weekday() == 6:
        current += timedelta(days=1)
    return current


def default_slots(settings: dict[str, str] | None = None) -> list[dict[str, str]]:
    today = datetime.now(UTC).date()
    first_day = next_business_day(today + timedelta(days=1))
    second_day = next_business_day(first_day, days_ahead=1)
    working_hours = (settings or {}).get("working_hours", "09:00-20:00")
    start_time = "10:00 AM"
    late_time = "5:30 PM"
    middle_time = "11:00 AM"
    if "-" in working_hours:
        start, end = working_hours.split("-", 1)
        try:
            start_hour = int(start.split(":")[0])
            end_hour = int(end.split(":")[0])
            start_time = datetime.strptime(f"{start_hour}:00", "%H:%M").strftime("%I:00 %p").lstrip("0")
            late_base = max(start_hour + 5, end_hour - 2)
            late_time = datetime.strptime(f"{late_base}:30", "%H:%M").strftime("%I:%M %p").lstrip("0")
            middle_time = datetime.strptime(f"{min(start_hour + 1, 23)}:00", "%H:%M").strftime("%I:00 %p").lstrip("0")
        except ValueError:
            pass
    return [
        {"date": first_day.isoformat(), "time": start_time},
        {"date": first_day.isoformat(), "time": late_time},
        {"date": second_day.isoformat(), "time": middle_time},
    ]


def init_db() -> None:
    with get_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS clinics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                clinic_name TEXT NOT NULL,
                clinic_timings TEXT NOT NULL,
                clinic_address TEXT NOT NULL,
                brand_tagline TEXT NOT NULL,
                accent_color TEXT NOT NULL,
                logo_text TEXT NOT NULL,
                business_type TEXT NOT NULL DEFAULT 'dental',
                avg_booking_value INTEGER NOT NULL DEFAULT 5000,
                white_label_enabled INTEGER NOT NULL DEFAULT 0,
                white_label_name TEXT NOT NULL DEFAULT '',
                reseller_code TEXT NOT NULL DEFAULT '',
                working_days TEXT NOT NULL DEFAULT 'Mon,Tue,Wed,Thu,Fri,Sat',
                working_hours TEXT NOT NULL DEFAULT '09:00-20:00',
                auto_callback_enabled INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS clinic_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                clinic_id INTEGER NOT NULL,
                username TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                role TEXT NOT NULL,
                display_name TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (clinic_id) REFERENCES clinics(id)
            );

            CREATE TABLE IF NOT EXISTS clinic_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                clinic_name TEXT NOT NULL,
                clinic_timings TEXT NOT NULL,
                clinic_address TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS appointments (
                id TEXT PRIMARY KEY,
                patient_name TEXT NOT NULL,
                phone_number TEXT NOT NULL,
                preferred_date TEXT NOT NULL,
                preferred_time TEXT NOT NULL,
                reason_for_visit TEXT NOT NULL,
                status TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS call_records (
                id TEXT PRIMARY KEY,
                caller_number TEXT NOT NULL,
                patient_name TEXT,
                intent TEXT NOT NULL,
                summary TEXT NOT NULL,
                urgent INTEGER NOT NULL,
                appointment_id TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS whatsapp_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_number TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_date TEXT NOT NULL,
                slot_time TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS contact_requests (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                clinic_name TEXT NOT NULL,
                phone_number TEXT NOT NULL,
                message TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS faq_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS receptionist_tasks (
                id TEXT PRIMARY KEY,
                patient_name TEXT NOT NULL,
                phone_number TEXT NOT NULL,
                note TEXT NOT NULL,
                due_date TEXT NOT NULL,
                status TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'medium',
                tags TEXT NOT NULL DEFAULT '',
                related_appointment_id TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reminder_queue (
                id TEXT PRIMARY KEY,
                appointment_id TEXT NOT NULL,
                patient_name TEXT NOT NULL,
                phone_number TEXT NOT NULL,
                reminder_type TEXT NOT NULL,
                scheduled_for TEXT NOT NULL,
                status TEXT NOT NULL,
                note TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_notifications (
                id TEXT PRIMARY KEY,
                clinic_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                href TEXT NOT NULL DEFAULT '',
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS comments (
                id TEXT PRIMARY KEY,
                clinic_id INTEGER NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                author_name TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS referrals (
                id TEXT PRIMARY KEY,
                clinic_id INTEGER NOT NULL,
                referrer_name TEXT NOT NULL,
                referrer_phone TEXT NOT NULL,
                referred_business TEXT NOT NULL,
                status TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS onboarding_emails (
                id TEXT PRIMARY KEY,
                clinic_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS onboarding_state (
                clinic_id INTEGER NOT NULL,
                step_key TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                PRIMARY KEY (clinic_id, step_key)
            );

            CREATE TABLE IF NOT EXISTS team_announcements (
                id TEXT PRIMARY KEY,
                clinic_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS automation_rules (
                id TEXT PRIMARY KEY,
                clinic_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                condition_key TEXT NOT NULL DEFAULT '',
                condition_value TEXT NOT NULL DEFAULT '',
                action_type TEXT NOT NULL,
                action_value TEXT NOT NULL DEFAULT '',
                is_enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS access_logs (
                id TEXT PRIMARY KEY,
                clinic_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                ip_address TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )

        if not column_exists(db, "appointments", "notes"):
            db.execute("ALTER TABLE appointments ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
        if not column_exists(db, "call_records", "lead_score"):
            db.execute("ALTER TABLE call_records ADD COLUMN lead_score TEXT NOT NULL DEFAULT 'warm'")
        if not column_exists(db, "clinic_settings", "brand_tagline"):
            db.execute("ALTER TABLE clinic_settings ADD COLUMN brand_tagline TEXT NOT NULL DEFAULT 'AI receptionist for dental clinics'")
        if not column_exists(db, "clinic_settings", "accent_color"):
            db.execute("ALTER TABLE clinic_settings ADD COLUMN accent_color TEXT NOT NULL DEFAULT '#146c78'")
        if not column_exists(db, "clinic_settings", "logo_text"):
            db.execute("ALTER TABLE clinic_settings ADD COLUMN logo_text TEXT NOT NULL DEFAULT 'DV'")
        if not column_exists(db, "clinic_settings", "admin_username"):
            db.execute("ALTER TABLE clinic_settings ADD COLUMN admin_username TEXT NOT NULL DEFAULT 'admin'")
        if not column_exists(db, "clinic_settings", "admin_password"):
            db.execute("ALTER TABLE clinic_settings ADD COLUMN admin_password TEXT NOT NULL DEFAULT 'dentvoice123'")
        if not column_exists(db, "clinics", "business_type"):
            db.execute("ALTER TABLE clinics ADD COLUMN business_type TEXT NOT NULL DEFAULT 'dental'")
        if not column_exists(db, "clinics", "avg_booking_value"):
            db.execute("ALTER TABLE clinics ADD COLUMN avg_booking_value INTEGER NOT NULL DEFAULT 5000")
        if not column_exists(db, "clinics", "white_label_enabled"):
            db.execute("ALTER TABLE clinics ADD COLUMN white_label_enabled INTEGER NOT NULL DEFAULT 0")
        if not column_exists(db, "clinics", "white_label_name"):
            db.execute("ALTER TABLE clinics ADD COLUMN white_label_name TEXT NOT NULL DEFAULT ''")
        if not column_exists(db, "clinics", "reseller_code"):
            db.execute("ALTER TABLE clinics ADD COLUMN reseller_code TEXT NOT NULL DEFAULT ''")
        if not column_exists(db, "clinic_users", "is_active"):
            db.execute("ALTER TABLE clinic_users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        if not column_exists(db, "contact_requests", "status"):
            db.execute("ALTER TABLE contact_requests ADD COLUMN status TEXT NOT NULL DEFAULT 'new'")
        if not column_exists(db, "contact_requests", "owner_notes"):
            db.execute("ALTER TABLE contact_requests ADD COLUMN owner_notes TEXT NOT NULL DEFAULT ''")
        if not column_exists(db, "contact_requests", "business_type"):
            db.execute("ALTER TABLE contact_requests ADD COLUMN business_type TEXT NOT NULL DEFAULT ''")
        if not column_exists(db, "contact_requests", "tags"):
            db.execute("ALTER TABLE contact_requests ADD COLUMN tags TEXT NOT NULL DEFAULT ''")
        if not column_exists(db, "receptionist_tasks", "priority"):
            db.execute("ALTER TABLE receptionist_tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'medium'")
        if not column_exists(db, "receptionist_tasks", "tags"):
            db.execute("ALTER TABLE receptionist_tasks ADD COLUMN tags TEXT NOT NULL DEFAULT ''")
        if not column_exists(db, "call_records", "internal_notes"):
            db.execute("ALTER TABLE call_records ADD COLUMN internal_notes TEXT NOT NULL DEFAULT ''")
        for table_name in ["appointments", "call_records", "whatsapp_messages", "slots", "audit_logs", "receptionist_tasks", "reminder_queue", "faq_entries"]:
            if not column_exists(db, table_name, "clinic_id"):
                db.execute(f"ALTER TABLE {table_name} ADD COLUMN clinic_id INTEGER NOT NULL DEFAULT 1")

        existing_settings = db.execute("SELECT COUNT(*) AS count FROM clinic_settings").fetchone()["count"]
        if existing_settings == 0:
            db.execute(
                """
                INSERT INTO clinic_settings (
                    id, clinic_name, clinic_timings, clinic_address, brand_tagline, accent_color, logo_text, admin_username, admin_password
                )
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Smile Dental Clinic",
                    "Monday to Saturday, 9 AM to 8 PM",
                    "Near the main market with parking available",
                    "AI receptionist for dental clinics",
                    "#146c78",
                    "DV",
                    "admin",
                    "dentvoice123",
                ),
            )

        existing_clinics = db.execute("SELECT COUNT(*) AS count FROM clinics").fetchone()["count"]
        if existing_clinics == 0:
            current = db.execute(
                """
                SELECT clinic_name, clinic_timings, clinic_address, brand_tagline, accent_color, logo_text
                FROM clinic_settings WHERE id = 1
                """
            ).fetchone()
            db.execute(
                """
                INSERT INTO clinics (id, slug, clinic_name, clinic_timings, clinic_address, brand_tagline, accent_color, logo_text, business_type, avg_booking_value, working_days, working_hours, auto_callback_enabled)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, 'dental', 5000, ?, ?, 1)
                """,
                (
                    "smile-dental-clinic",
                    current["clinic_name"],
                    current["clinic_timings"],
                    current["clinic_address"],
                    current["brand_tagline"],
                    current["accent_color"],
                    current["logo_text"],
                    "Mon,Tue,Wed,Thu,Fri,Sat",
                    "09:00-20:00",
                ),
            )
            db.execute(
                """
                INSERT INTO clinics (slug, clinic_name, clinic_timings, clinic_address, brand_tagline, accent_color, logo_text, working_days, working_hours, auto_callback_enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    "bandra-smiles",
                    "Bandra Smiles Studio",
                    "Monday to Saturday, 10 AM to 7 PM",
                    "Bandra West, near Linking Road",
                    "Cosmetic and family dentistry for busy urban clinics",
                    "#7c3f1d",
                    "BS",
                    "Mon,Tue,Wed,Thu,Fri,Sat",
                    "10:00-19:00",
                ),
            )

        existing_users = db.execute("SELECT COUNT(*) AS count FROM clinic_users").fetchone()["count"]
        if existing_users == 0:
            db.execute(
                "INSERT INTO clinic_users (clinic_id, username, password, role, display_name) VALUES (1, ?, ?, ?, ?)",
                ("admin", "dentvoice123", "admin", "Clinic Admin"),
            )
            db.execute(
                "INSERT INTO clinic_users (clinic_id, username, password, role, display_name) VALUES (1, ?, ?, ?, ?)",
                ("reception", "dentvoice123", "receptionist", "Reception Desk"),
            )

        existing_slots = db.execute("SELECT COUNT(*) AS count FROM slots").fetchone()["count"]
        if existing_slots == 0:
            for slot in default_slots():
                db.execute("INSERT INTO slots (slot_date, slot_time, clinic_id) VALUES (?, ?, ?)", (slot["date"], slot["time"], 1))

        existing_faqs = db.execute("SELECT COUNT(*) AS count FROM faq_entries").fetchone()["count"]
        if existing_faqs == 0:
            created_at = datetime.now(UTC).isoformat()
            for index, item in enumerate(FAQS):
                db.execute(
                    "INSERT INTO faq_entries (question, answer, sort_order, created_at, clinic_id) VALUES (?, ?, ?, ?, ?)",
                    (item.question, item.answer, index, created_at, 1),
                )

        db.commit()


def reset_slots_if_outdated(db: sqlite3.Connection, clinic_id: int = 1) -> None:
    settings = fetch_clinic_settings(clinic_id)
    rows = db.execute("SELECT id, slot_date FROM slots WHERE clinic_id = ? ORDER BY slot_date, slot_time", (clinic_id,)).fetchall()
    if not rows:
        for slot in default_slots(settings):
            db.execute("INSERT INTO slots (slot_date, slot_time, clinic_id) VALUES (?, ?, ?)", (slot["date"], slot["time"], clinic_id))
        db.commit()
        return

    latest_slot = max(datetime.fromisoformat(row["slot_date"]).date() for row in rows)
    if latest_slot < datetime.now(UTC).date():
        db.execute("DELETE FROM slots WHERE clinic_id = ?", (clinic_id,))
        for slot in default_slots(settings):
            db.execute("INSERT INTO slots (slot_date, slot_time, clinic_id) VALUES (?, ?, ?)", (slot["date"], slot["time"], clinic_id))
        db.commit()


def fetch_clinics() -> list[dict[str, object]]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, slug, clinic_name, clinic_timings, clinic_address, brand_tagline, accent_color, logo_text, business_type, avg_booking_value, white_label_enabled, white_label_name, reseller_code, working_days, working_hours, auto_callback_enabled
            FROM clinics
            ORDER BY clinic_name ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def fetch_clinic_settings(clinic_id: int = 1) -> dict[str, str]:
    with get_db() as db:
        row = db.execute(
            """
            SELECT id, slug, clinic_name, clinic_timings, clinic_address, brand_tagline, accent_color, logo_text, business_type, avg_booking_value, white_label_enabled, white_label_name, reseller_code, working_days, working_hours, auto_callback_enabled
            FROM clinics
            WHERE id = ?
            """
        ,
            (clinic_id,),
        ).fetchone()
        return dict(row)


def fetch_clinic_by_slug(slug: str) -> dict[str, str] | None:
    with get_db() as db:
        row = db.execute(
            """
            SELECT id, slug, clinic_name, clinic_timings, clinic_address, brand_tagline, accent_color, logo_text, business_type, avg_booking_value, white_label_enabled, white_label_name, reseller_code, working_days, working_hours, auto_callback_enabled
            FROM clinics
            WHERE slug = ?
            """,
            (slug,),
        ).fetchone()
        return dict(row) if row else None


def create_clinic_workspace(
    *,
    slug: str,
    clinic_name: str,
    clinic_timings: str,
    clinic_address: str,
    brand_tagline: str,
    accent_color: str,
    logo_text: str,
    business_type: str,
    avg_booking_value: int,
    working_days: str,
    working_hours: str,
) -> dict[str, object]:
    with get_db() as db:
        cursor = db.execute(
            """
            INSERT INTO clinics (slug, clinic_name, clinic_timings, clinic_address, brand_tagline, accent_color, logo_text, business_type, avg_booking_value, white_label_enabled, white_label_name, reseller_code, working_days, working_hours, auto_callback_enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '', '', ?, ?, 1)
            """,
            (slug, clinic_name, clinic_timings, clinic_address, brand_tagline, accent_color, logo_text, business_type, avg_booking_value, working_days, working_hours),
        )
        clinic_id = int(cursor.lastrowid)
        admin_username = f"{slug}-admin"
        receptionist_username = f"{slug}-desk"
        db.execute(
            "INSERT INTO clinic_users (clinic_id, username, password, role, display_name) VALUES (?, ?, ?, ?, ?)",
            (clinic_id, admin_username, "dentvoice123", "admin", f"{clinic_name} Admin"),
        )
        db.execute(
            "INSERT INTO clinic_users (clinic_id, username, password, role, display_name) VALUES (?, ?, ?, ?, ?)",
            (clinic_id, receptionist_username, "dentvoice123", "receptionist", f"{clinic_name} Reception"),
        )
        for slot in default_slots({"working_hours": working_hours}):
            db.execute("INSERT INTO slots (slot_date, slot_time, clinic_id) VALUES (?, ?, ?)", (slot["date"], slot["time"], clinic_id))
        created_at = datetime.now(UTC).isoformat()
        template_faqs = INDUSTRY_TEMPLATES.get(business_type, INDUSTRY_TEMPLATES["dental"])["faqs"]
        for index, item in enumerate(template_faqs):
            db.execute(
                "INSERT INTO faq_entries (question, answer, sort_order, created_at, clinic_id) VALUES (?, ?, ?, ?, ?)",
                (item.question, item.answer, index, created_at, clinic_id),
            )
        db.commit()
    log_audit("create", "clinic", str(clinic_id), f"Created clinic workspace {clinic_name}.", clinic_id=clinic_id)
    return {"clinic_id": clinic_id, "admin_username": admin_username, "receptionist_username": receptionist_username}


def fetch_slots(clinic_id: int = 1) -> list[dict[str, str]]:
    with get_db() as db:
        reset_slots_if_outdated(db, clinic_id)
        rows = db.execute(
            """
            SELECT id, slot_date, slot_time
            FROM slots
            WHERE clinic_id = ?
            ORDER BY slot_date ASC, slot_time ASC
            """
        ,
            (clinic_id,),
        ).fetchall()
        return [{"id": row["id"], "option": str(index + 1), "date": row["slot_date"], "time": row["slot_time"]} for index, row in enumerate(rows)]


def fetch_appointments(
    *,
    limit: int = 20,
    search: str = "",
    status: str = "",
    source: str = "",
    preferred_date: str = "",
    sort: str = "created_desc",
    clinic_id: int = 1,
) -> list[AppointmentRequest]:
    conditions: list[str] = ["clinic_id = ?"]
    params: list[object] = [clinic_id]

    if search:
        conditions.append("(patient_name LIKE ? OR phone_number LIKE ? OR reason_for_visit LIKE ? OR notes LIKE ?)")
        pattern = f"%{search}%"
        params.extend([pattern, pattern, pattern, pattern])
    if status:
        conditions.append("status = ?")
        params.append(status)
    if source:
        conditions.append("source = ?")
        params.append(source)
    if preferred_date:
        conditions.append("preferred_date = ?")
        params.append(preferred_date)

    order_clause = "ORDER BY datetime(created_at) DESC"
    if sort == "date_asc":
        order_clause = "ORDER BY preferred_date ASC, preferred_time ASC"
    elif sort == "date_desc":
        order_clause = "ORDER BY preferred_date DESC, preferred_time DESC"
    elif sort == "name_asc":
        order_clause = "ORDER BY patient_name ASC"

    where_clause = f"WHERE {' AND '.join(conditions)}"
    query = f"""
        SELECT id, patient_name, phone_number, preferred_date, preferred_time, reason_for_visit, status, source, notes, created_at
        FROM appointments
        {where_clause}
        {order_clause}
        LIMIT ?
    """
    params.append(limit)

    with get_db() as db:
        rows = db.execute(query, params).fetchall()
        return [AppointmentRequest(**dict(row)) for row in rows]


def fetch_call_records(
    *,
    limit: int = 20,
    search: str = "",
    intent: str = "",
    urgent_only: bool = False,
    lead_score: str = "",
    sort: str = "created_desc",
    clinic_id: int = 1,
) -> list[CallRecord]:
    appointments = {item.id: item for item in fetch_appointments(limit=500, clinic_id=clinic_id)}
    conditions: list[str] = ["clinic_id = ?"]
    params: list[object] = [clinic_id]

    if search:
        conditions.append("(caller_number LIKE ? OR COALESCE(patient_name, '') LIKE ? OR summary LIKE ?)")
        pattern = f"%{search}%"
        params.extend([pattern, pattern, pattern])
    if intent:
        conditions.append("intent = ?")
        params.append(intent)
    if urgent_only:
        conditions.append("urgent = 1")
    if lead_score:
        conditions.append("lead_score = ?")
        params.append(lead_score)

    order_clause = "ORDER BY datetime(created_at) DESC"
    if sort == "lead_desc":
        order_clause = """
        ORDER BY
            CASE lead_score WHEN 'hot' THEN 1 WHEN 'warm' THEN 2 ELSE 3 END,
            datetime(created_at) DESC
        """
    elif sort == "intent_asc":
        order_clause = "ORDER BY intent ASC, datetime(created_at) DESC"

    where_clause = f"WHERE {' AND '.join(conditions)}"
    query = f"""
        SELECT id, caller_number, patient_name, intent, summary, urgent, lead_score, internal_notes, appointment_id, created_at
        FROM call_records
        {where_clause}
        {order_clause}
        LIMIT ?
    """
    params.append(limit)

    with get_db() as db:
        rows = db.execute(query, params).fetchall()

    records: list[CallRecord] = []
    for row in rows:
        data = dict(row)
        appointment_id = data.pop("appointment_id")
        data["urgent"] = bool(data["urgent"])
        records.append(CallRecord(**data, appointment_request=appointments.get(appointment_id)))
    return records


def fetch_messages(limit: int = 20, clinic_id: int = 1) -> list[WhatsAppMessage]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT phone_number, message, created_at
            FROM whatsapp_messages
            WHERE clinic_id = ?
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (clinic_id, limit),
        ).fetchall()
        return [WhatsAppMessage(**dict(row)) for row in rows]


def fetch_contact_requests(limit: int = 20, search: str = "", status: str = "", sort: str = "newest") -> list[dict[str, str]]:
    conditions: list[str] = []
    params: list[object] = []

    if search:
        pattern = f"%{search}%"
        conditions.append("(name LIKE ? OR clinic_name LIKE ? OR phone_number LIKE ? OR message LIKE ? OR owner_notes LIKE ? OR tags LIKE ?)")
        params.extend([pattern, pattern, pattern, pattern, pattern, pattern])
    if status:
        conditions.append("status = ?")
        params.append(status)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    order_clause = "ORDER BY datetime(created_at) DESC"
    if sort == "status":
        order_clause = "ORDER BY status ASC, datetime(created_at) DESC"
    elif sort == "name":
        order_clause = "ORDER BY name ASC"

    query = f"""
        SELECT id, name, clinic_name, phone_number, message, status, owner_notes, business_type, tags, created_at
        FROM contact_requests
        {where_clause}
        {order_clause}
        LIMIT ?
    """
    params.append(limit)

    with get_db() as db:
        rows = db.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def fetch_clinic_users(clinic_id: int = 1) -> list[dict[str, object]]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, clinic_id, username, role, display_name, is_active
            FROM clinic_users
            WHERE clinic_id = ?
            ORDER BY role ASC, username ASC
            """,
            (clinic_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_onboarding_state(clinic_id: int = 1) -> set[str]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT step_key
            FROM onboarding_state
            WHERE clinic_id = ?
            """,
            (clinic_id,),
        ).fetchall()
    return {str(row["step_key"]) for row in rows}


def fetch_announcements(clinic_id: int = 1, active_only: bool = True, limit: int = 20) -> list[dict[str, object]]:
    conditions = ["clinic_id = ?"]
    params: list[object] = [clinic_id]
    if active_only:
        conditions.append("is_active = 1")
    with get_db() as db:
        rows = db.execute(
            f"""
            SELECT id, title, body, is_active, created_at
            FROM team_announcements
            WHERE {' AND '.join(conditions)}
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_automation_rules(clinic_id: int = 1, limit: int = 50) -> list[dict[str, object]]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, name, trigger_type, condition_key, condition_value, action_type, action_value, is_enabled, created_at
            FROM automation_rules
            WHERE clinic_id = ?
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (clinic_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_access_logs(clinic_id: int = 1, limit: int = 100) -> list[dict[str, object]]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, username, role, action, detail, ip_address, created_at
            FROM access_logs
            WHERE clinic_id = ?
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (clinic_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_notifications(clinic_id: int = 1, unread_only: bool = False, limit: int = 100) -> list[dict[str, object]]:
    conditions = ["clinic_id = ?"]
    params: list[object] = [clinic_id]
    if unread_only:
        conditions.append("is_read = 0")
    with get_db() as db:
        rows = db.execute(
            f"""
            SELECT id, title, message, href, is_read, created_at
            FROM app_notifications
            WHERE {' AND '.join(conditions)}
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_comments(entity_type: str, entity_id: str, clinic_id: int = 1, limit: int = 100) -> list[dict[str, str]]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, author_name, body, created_at
            FROM comments
            WHERE clinic_id = ? AND entity_type = ? AND entity_id = ?
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (clinic_id, entity_type, entity_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_referrals(clinic_id: int = 1, limit: int = 100) -> list[dict[str, str]]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, referrer_name, referrer_phone, referred_business, status, notes, created_at
            FROM referrals
            WHERE clinic_id = ?
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (clinic_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_onboarding_emails(clinic_id: int = 1, limit: int = 50) -> list[dict[str, str]]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, subject, body, status, created_at
            FROM onboarding_emails
            WHERE clinic_id = ?
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (clinic_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_faq_entries(limit: int = 50, clinic_id: int = 1) -> list[dict[str, object]]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, question, answer, sort_order, created_at
            FROM faq_entries
            WHERE clinic_id = ?
            ORDER BY sort_order ASC, id ASC
            LIMIT ?
            """,
            (clinic_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def fetch_audit_logs(limit: int = 50, clinic_id: int = 1) -> list[dict[str, str]]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, action, entity_type, entity_id, summary, created_at
            FROM audit_logs
            WHERE clinic_id = ?
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (clinic_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def fetch_receptionist_tasks(limit: int = 100, status: str = "", search: str = "", priority: str = "", clinic_id: int = 1) -> list[dict[str, str]]:
    conditions: list[str] = ["clinic_id = ?"]
    params: list[object] = [clinic_id]
    if status:
        conditions.append("status = ?")
        params.append(status)
    if search:
        pattern = f"%{search}%"
        conditions.append("(patient_name LIKE ? OR phone_number LIKE ? OR note LIKE ? OR tags LIKE ?)")
        params.extend([pattern, pattern, pattern, pattern])
    if priority:
        conditions.append("priority = ?")
        params.append(priority)

    where_clause = f"WHERE {' AND '.join(conditions)}"
    query = f"""
        SELECT id, patient_name, phone_number, note, due_date, status, priority, tags, related_appointment_id, created_at
        FROM receptionist_tasks
        {where_clause}
        ORDER BY
            CASE priority
                WHEN 'high' THEN 1
                WHEN 'medium' THEN 2
                ELSE 3
            END,
            CASE status
                WHEN 'open' THEN 1
                WHEN 'in_progress' THEN 2
                ELSE 3
            END,
            due_date ASC,
            datetime(created_at) DESC
        LIMIT ?
    """
    params.append(limit)

    with get_db() as db:
        rows = db.execute(query, params).fetchall()
        results = [dict(row) for row in rows]
    today_iso = datetime.now(UTC).date().isoformat()
    for item in results:
        item["sla_status"] = "overdue" if item["status"] != "done" and item["due_date"] < today_iso else "on_track"
    return results


def fetch_reminders(limit: int = 200, status: str = "", clinic_id: int = 1) -> list[dict[str, str]]:
    conditions: list[str] = ["clinic_id = ?"]
    params: list[object] = [clinic_id]
    if status:
        conditions.append("status = ?")
        params.append(status)

    where_clause = f"WHERE {' AND '.join(conditions)}"
    query = f"""
        SELECT id, appointment_id, patient_name, phone_number, reminder_type, scheduled_for, status, note, created_at
        FROM reminder_queue
        {where_clause}
        ORDER BY
            CASE status
                WHEN 'pending' THEN 1
                WHEN 'ready' THEN 2
                WHEN 'sent' THEN 3
                ELSE 4
            END,
            scheduled_for ASC,
            datetime(created_at) DESC
        LIMIT ?
    """
    params.append(limit)

    with get_db() as db:
        rows = db.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def fetch_upcoming_reminder_candidates(limit: int = 20, clinic_id: int = 1) -> list[dict[str, str]]:
    appointments = [
        item for item in fetch_appointments(limit=1000, clinic_id=clinic_id)
        if item.status in {"new", "confirmed", "needs_follow_up"}
    ]
    existing = {item["appointment_id"] for item in fetch_reminders(limit=1000, status="pending", clinic_id=clinic_id)} | {
        item["appointment_id"] for item in fetch_reminders(limit=1000, status="ready", clinic_id=clinic_id)
    }
    appointments.sort(key=lambda item: (item.preferred_date, item.preferred_time))
    candidates = []
    for item in appointments:
        if item.id in existing:
            continue
        candidates.append(
            {
                "appointment_id": item.id,
                "patient_name": item.patient_name,
                "phone_number": item.phone_number,
                "preferred_date": item.preferred_date,
                "preferred_time": item.preferred_time,
                "reason_for_visit": item.reason_for_visit,
                "suggested_note": f"Reminder: {item.reason_for_visit} on {item.preferred_date} at {item.preferred_time}.",
            }
        )
    return candidates[:limit]


def fetch_patient_profiles(limit: int = 200, search: str = "", clinic_id: int = 1, sort: str = "latest_desc") -> list[dict[str, object]]:
    appointments = fetch_appointments(limit=1000, clinic_id=clinic_id)
    grouped: dict[str, list[AppointmentRequest]] = defaultdict(list)
    for item in appointments:
        key = item.phone_number or item.patient_name
        grouped[key].append(item)

    profiles: list[dict[str, object]] = []
    for items in grouped.values():
        latest = max(items, key=lambda item: item.created_at)
        notes = [item.notes for item in items if item.notes]
        profile = {
            "patient_name": latest.patient_name,
            "phone_number": latest.phone_number,
            "patient_query": quote_plus(latest.phone_number),
            "appointment_count": len(items),
            "latest_status": latest.status,
            "latest_visit_date": latest.preferred_date,
            "latest_reason": latest.reason_for_visit,
            "notes_preview": notes[-1] if notes else "",
        }
        if search:
            haystack = f"{profile['patient_name']} {profile['phone_number']} {profile['latest_reason']} {profile['notes_preview']}".lower()
            if search.lower() not in haystack:
                continue
        profiles.append(profile)

    if sort == "visits_desc":
        profiles.sort(key=lambda item: int(item["appointment_count"]), reverse=True)
    elif sort == "name_asc":
        profiles.sort(key=lambda item: str(item["patient_name"]).lower())
    else:
        profiles.sort(key=lambda item: str(item["latest_visit_date"]), reverse=True)
    return profiles[:limit]


def fetch_patient_detail(phone_number: str, clinic_id: int = 1) -> dict[str, object] | None:
    appointments = [item for item in fetch_appointments(limit=1000, clinic_id=clinic_id) if item.phone_number == phone_number]
    if not appointments:
        return None

    appointments.sort(key=lambda item: item.created_at, reverse=True)
    patient_name = appointments[0].patient_name
    calls = [item for item in fetch_call_records(limit=1000, clinic_id=clinic_id) if item.caller_number == phone_number or (item.patient_name and item.patient_name == patient_name)]
    tasks = [item for item in fetch_receptionist_tasks(limit=1000, clinic_id=clinic_id) if item["phone_number"] == phone_number]
    contacts = [item for item in fetch_contact_requests(limit=1000) if item["phone_number"] == phone_number]
    comments = fetch_comments("patient", phone_number, clinic_id=clinic_id)
    latest = appointments[0]
    timeline = []
    for item in appointments:
        timeline.append({"kind": "appointment", "date": item.created_at, "title": f"{item.reason_for_visit} booked", "detail": f"{item.preferred_date} at {item.preferred_time} · {item.status.replace('_', ' ').title()}"})
    for item in calls:
        timeline.append({"kind": "call", "date": item.created_at, "title": item.intent.replace("_", " ").title(), "detail": item.summary})
    for item in tasks:
        timeline.append({"kind": "task", "date": item["created_at"], "title": "Follow-up task", "detail": item["note"]})
    timeline.sort(key=lambda entry: entry["date"], reverse=True)

    return {
        "profile": {
            "patient_name": patient_name,
            "phone_number": phone_number,
            "appointment_count": len(appointments),
            "latest_status": latest.status,
            "latest_visit_date": latest.preferred_date,
            "latest_reason": latest.reason_for_visit,
            "notes_preview": latest.notes,
            "completed_appointments": sum(1 for item in appointments if item.status == "completed"),
            "open_tasks": sum(1 for item in tasks if item["status"] != "done"),
        },
        "appointments": appointments,
        "calls": calls,
        "tasks": tasks,
        "contacts": contacts,
        "comments": comments,
        "timeline": timeline,
        "patient_query": quote_plus(phone_number),
    }


def fetch_missed_leads(limit: int = 100, search: str = "", lead_score: str = "", clinic_id: int = 1) -> list[dict[str, object]]:
    records = []
    for item in fetch_call_records(limit=1000, search=search, lead_score=lead_score, clinic_id=clinic_id):
        if item.appointment_request is not None:
            continue
        if item.intent not in {"appointment_booking", "reschedule", "pricing", "general", "faq", "directions"}:
            continue

        records.append(
            {
                "id": item.id,
                "caller_number": item.caller_number,
                "patient_name": item.patient_name or "Unknown caller",
                "intent": item.intent,
                "summary": item.summary,
                "urgent": item.urgent,
                "lead_score": item.lead_score,
                "created_at": item.created_at,
                "recommended_note": f"Follow up on {item.intent.replace('_', ' ')} inquiry and offer the next available appointment slot.",
            }
        )

    records.sort(key=lambda item: item["created_at"], reverse=True)
    return records[:limit]


def fetch_global_search_results(query: str, clinic_id: int = 1) -> dict[str, list[dict[str, str]]]:
    if not query.strip():
        return {"appointments": [], "calls": [], "patients": [], "leads": [], "tasks": [], "faqs": [], "reminders": []}

    appointments = fetch_appointments(limit=8, search=query, clinic_id=clinic_id)
    calls = fetch_call_records(limit=8, search=query, clinic_id=clinic_id)
    patients = fetch_patient_profiles(limit=8, search=query, clinic_id=clinic_id)
    leads = fetch_contact_requests(limit=8, search=query)
    tasks = fetch_receptionist_tasks(limit=8, search=query, clinic_id=clinic_id)
    reminders = [item for item in fetch_reminders(limit=50, clinic_id=clinic_id) if query.lower() in f"{item['patient_name']} {item['phone_number']} {item['note']} {item['reminder_type']}".lower()][:8]
    faqs = [item for item in fetch_faq_entries(limit=50, clinic_id=clinic_id) if query.lower() in f"{item['question']} {item['answer']}".lower()][:8]

    return {
        "appointments": [
            {
                "id": item.id,
                "title": item.patient_name,
                "meta": f"{item.preferred_date} · {item.preferred_time}",
                "detail": item.reason_for_visit,
                "href": "/appointments",
            }
            for item in appointments
        ],
        "calls": [
            {
                "id": item.id,
                "title": item.patient_name or item.caller_number,
                "meta": item.intent.replace("_", " ").title(),
                "detail": item.summary,
                "href": "/calls",
            }
            for item in calls
        ],
        "patients": [
            {
                "id": item["phone_number"],
                "title": str(item["patient_name"]),
                "meta": str(item["phone_number"]),
                "detail": f"{item['appointment_count']} appointment(s)",
                "href": f"/patients/detail?phone={item['patient_query']}",
            }
            for item in patients
        ],
        "leads": [
            {
                "id": item["id"],
                "title": item["name"],
                "meta": item["clinic_name"],
                "detail": f"{item['message']} {('· Tags: ' + item['tags']) if item.get('tags') else ''}",
                "href": "/leads",
            }
            for item in leads
        ],
        "tasks": [
            {
                "id": item["id"],
                "title": item["patient_name"],
                "meta": f"{item['priority'].title()} priority · {item['status'].replace('_', ' ').title()}",
                "detail": f"{item['note']} {('· Tags: ' + item['tags']) if item.get('tags') else ''}",
                "href": "/inbox",
            }
            for item in tasks
        ],
        "faqs": [
            {
                "id": str(item["id"]),
                "title": str(item["question"]),
                "meta": "FAQ manager",
                "detail": str(item["answer"]),
                "href": "/dashboard",
            }
            for item in faqs
        ],
        "reminders": [
            {
                "id": item["id"],
                "title": item["patient_name"],
                "meta": f"{item['reminder_type'].replace('_', ' ').title()} · {item['status'].title()}",
                "detail": item["note"],
                "href": "/reminders",
            }
            for item in reminders
        ],
    }


def fetch_calendar_entries(clinic_id: int = 1) -> list[dict[str, object]]:
    grouped: dict[str, list[AppointmentRequest]] = defaultdict(list)
    for item in fetch_appointments(limit=1000, clinic_id=clinic_id):
        grouped[item.preferred_date].append(item)

    calendar_rows = []
    for day, entries in sorted(grouped.items()):
        calendar_rows.append(
            {
                "date": day,
                "appointments": sorted(entries, key=lambda item: item.preferred_time),
                "count": len(entries),
            }
        )
    return calendar_rows


def fetch_calendar_views(clinic_id: int = 1) -> dict[str, object]:
    days = fetch_calendar_entries(clinic_id)
    weekly: dict[str, list[dict[str, object]]] = defaultdict(list)
    monthly: dict[str, dict[str, object]] = {}
    for item in days:
        current = datetime.fromisoformat(item["date"]).date()
        week_key = f"{current.isocalendar().year}-W{current.isocalendar().week:02d}"
        weekly[week_key].append(item)
        month_key = current.strftime("%Y-%m")
        monthly.setdefault(month_key, {"month": month_key, "appointments": 0, "days": 0})
        monthly[month_key]["appointments"] += item["count"]
        monthly[month_key]["days"] += 1
    return {"days": days, "weeks": dict(weekly), "months": list(monthly.values())}


def fetch_analytics(clinic_id: int = 1) -> dict[str, object]:
    appointments = fetch_appointments(limit=1000, clinic_id=clinic_id)
    calls = fetch_call_records(limit=1000, clinic_id=clinic_id)
    messages = fetch_messages(limit=1000, clinic_id=clinic_id)
    contacts = fetch_contact_requests(limit=1000)
    tasks = fetch_receptionist_tasks(limit=1000, clinic_id=clinic_id)
    reminders = fetch_reminders(limit=1000, clinic_id=clinic_id)
    settings = fetch_clinic_settings(clinic_id)
    avg_booking_value = int(settings.get("avg_booking_value", 5000) or 5000)

    appointments_by_status = Counter(item.status for item in appointments)
    appointments_by_source = Counter(item.source for item in appointments)
    calls_by_intent = Counter(item.intent for item in calls)
    calls_by_lead_score = Counter(item.lead_score for item in calls)
    tasks_by_status = Counter(item["status"] for item in tasks)
    tasks_by_priority = Counter(item["priority"] for item in tasks)
    contacts_by_status = Counter(item["status"] for item in contacts)
    reminders_by_status = Counter(item["status"] for item in reminders)

    recent_days: list[dict[str, object]] = []
    today = datetime.now(UTC).date()
    for offset in range(6, -1, -1):
        current_day = today - timedelta(days=offset)
        iso_day = current_day.isoformat()
        recent_days.append(
            {
                "date": iso_day,
                "appointments": sum(1 for item in appointments if item.created_at[:10] == iso_day),
                "calls": sum(1 for item in calls if item.created_at[:10] == iso_day),
                "contacts": sum(1 for item in contacts if item["created_at"][:10] == iso_day),
            }
        )

    protected_appointments = [item for item in appointments if item.status != "cancelled"]
    completed_appointments = [item for item in appointments if item.status == "completed"]
    hot_calls = [item for item in calls if item.lead_score == "hot"]
    estimated_revenue_recovered = len(protected_appointments) * avg_booking_value
    estimated_revenue_realized = len(completed_appointments) * avg_booking_value
    pipeline_revenue_at_risk = len(hot_calls) * avg_booking_value

    return {
        "totals": {
            "appointments": len(appointments),
            "calls": len(calls),
            "messages": len(messages),
            "emergencies": sum(1 for item in calls if item.urgent),
            "contacts": len(contacts),
            "patients": len(fetch_patient_profiles(limit=1000, clinic_id=clinic_id)),
            "open_tasks": sum(1 for item in tasks if item["status"] != "done"),
            "missed_leads": len(fetch_missed_leads(limit=1000, clinic_id=clinic_id)),
            "pending_reminders": sum(1 for item in reminders if item["status"] in {"pending", "ready"}),
        },
        "appointments_by_status": dict(appointments_by_status),
        "appointments_by_source": dict(appointments_by_source),
        "calls_by_intent": dict(calls_by_intent),
        "calls_by_lead_score": dict(calls_by_lead_score),
        "tasks_by_status": dict(tasks_by_status),
        "tasks_by_priority": dict(tasks_by_priority),
        "contacts_by_status": dict(contacts_by_status),
        "reminders_by_status": dict(reminders_by_status),
        "recent_days": recent_days,
        "conversion_rate": round((len(appointments) / len(calls)) * 100, 1) if calls else 0.0,
        "completion_rate": round((appointments_by_status.get("completed", 0) / len(appointments)) * 100, 1) if appointments else 0.0,
        "hot_lead_rate": round((calls_by_lead_score.get("hot", 0) / len(calls)) * 100, 1) if calls else 0.0,
        "avg_booking_value": avg_booking_value,
        "estimated_revenue_recovered": estimated_revenue_recovered,
        "estimated_revenue_realized": estimated_revenue_realized,
        "pipeline_revenue_at_risk": pipeline_revenue_at_risk,
    }


def build_chart(counter_map: dict[str, int], ordered_keys: list[str] | None = None) -> list[dict[str, object]]:
    if ordered_keys:
        normalized = {key: int(counter_map.get(key, 0)) for key in ordered_keys}
    else:
        normalized = dict(counter_map)

    if not normalized:
        return []

    peak = max(normalized.values()) or 1
    rows = []
    for label, value in normalized.items():
        rows.append(
            {
                "label": label.replace("_", " ").title(),
                "value": value,
                "percent": round((value / peak) * 100, 1) if value else 6,
            }
        )
    return rows


def build_trend_chart(days: list[dict[str, object]]) -> list[dict[str, object]]:
    peak = max((max(int(item["appointments"]), int(item["calls"]), int(item["contacts"])) for item in days), default=1)
    peak = peak or 1
    return [
        {
            "date": item["date"],
            "appointments": item["appointments"],
            "calls": item["calls"],
            "contacts": item["contacts"],
            "appointment_percent": round((int(item["appointments"]) / peak) * 100, 1) if int(item["appointments"]) else 4,
            "call_percent": round((int(item["calls"]) / peak) * 100, 1) if int(item["calls"]) else 4,
            "contact_percent": round((int(item["contacts"]) / peak) * 100, 1) if int(item["contacts"]) else 4,
        }
        for item in days
    ]


def build_chartjs_datasets(analytics: dict[str, object]) -> dict[str, object]:
    return {
        "appointments_by_status": {
            "labels": [item.replace("_", " ").title() for item in APPOINTMENT_STATUSES],
            "values": [analytics["appointments_by_status"].get(item, 0) for item in APPOINTMENT_STATUSES],
        },
        "appointments_by_source": {
            "labels": [item.replace("_", " ").title() for item in APPOINTMENT_SOURCES],
            "values": [analytics["appointments_by_source"].get(item, 0) for item in APPOINTMENT_SOURCES],
        },
        "calls_by_intent": {
            "labels": [item.replace("_", " ").title() for item in CALL_INTENTS],
            "values": [analytics["calls_by_intent"].get(item, 0) for item in CALL_INTENTS],
        },
        "calls_by_lead_score": {
            "labels": [item.title() for item in LEAD_SCORES],
            "values": [analytics["calls_by_lead_score"].get(item, 0) for item in LEAD_SCORES],
        },
        "tasks_by_priority": {
            "labels": [item.title() for item in TASK_PRIORITIES],
            "values": [analytics["tasks_by_priority"].get(item, 0) for item in TASK_PRIORITIES],
        },
        "contacts_by_status": {
            "labels": [item.title() for item in CONTACT_STATUSES],
            "values": [analytics["contacts_by_status"].get(item, 0) for item in CONTACT_STATUSES],
        },
        "reminders_by_status": {
            "labels": ["Pending", "Ready", "Sent", "Cancelled"],
            "values": [analytics["reminders_by_status"].get(item, 0) for item in ["pending", "ready", "sent", "cancelled"]],
        },
        "recent_days": {
            "labels": [item["date"][5:] for item in analytics["recent_days"]],
            "appointments": [item["appointments"] for item in analytics["recent_days"]],
            "calls": [item["calls"] for item in analytics["recent_days"]],
            "contacts": [item["contacts"] for item in analytics["recent_days"]],
        },
    }


def log_audit(action: str, entity_type: str, entity_id: str | None, summary: str, clinic_id: int = 1) -> None:
    with get_db() as db:
        db.execute(
            """
            INSERT INTO audit_logs (action, entity_type, entity_id, summary, created_at, clinic_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (action, entity_type, entity_id, summary, datetime.now(UTC).isoformat(), clinic_id),
        )
        db.commit()


def log_access_event(request: Request | None, clinic_id: int, username: str, role: str, action: str, detail: str = "") -> None:
    ip_address = ""
    if request is not None and request.client is not None:
        ip_address = request.client.host or ""
    with get_db() as db:
        db.execute(
            """
            INSERT INTO access_logs (id, clinic_id, username, role, action, detail, ip_address, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid4()), clinic_id, username, role, action, detail, ip_address, datetime.now(UTC).isoformat()),
        )
        db.commit()


def create_notification(clinic_id: int, title: str, message: str, href: str = "") -> None:
    with get_db() as db:
        db.execute(
            """
            INSERT INTO app_notifications (id, clinic_id, title, message, href, is_read, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            """,
            (str(uuid4()), clinic_id, title, message, href, datetime.now(UTC).isoformat()),
        )
        db.commit()


def mark_onboarding_step(clinic_id: int, step_key: str) -> None:
    with get_db() as db:
        db.execute(
            """
            INSERT OR REPLACE INTO onboarding_state (clinic_id, step_key, completed_at)
            VALUES (?, ?, ?)
            """,
            (clinic_id, step_key, datetime.now(UTC).isoformat()),
        )
        db.commit()


def build_staff_performance(clinic_id: int = 1) -> list[dict[str, object]]:
    team_users = fetch_clinic_users(clinic_id)
    access_logs = fetch_access_logs(clinic_id, limit=1000)
    performance_rows: list[dict[str, object]] = []
    for user in team_users:
        username = str(user["username"])
        user_logs = [item for item in access_logs if item["username"] == username]
        action_counts = Counter(item["action"] for item in user_logs)
        follow_up_actions = sum(action_counts.get(key, 0) for key in ["task_created", "task_updated", "task_completed", "reminder_created", "reminder_updated", "lead_updated", "comment_added"])
        last_seen = user_logs[0]["created_at"] if user_logs else ""
        performance_rows.append(
            {
                "username": username,
                "display_name": user["display_name"],
                "role": user["role"],
                "is_active": bool(user["is_active"]),
                "login_count": action_counts.get("login", 0),
                "follow_up_actions": follow_up_actions,
                "task_completions": action_counts.get("task_completed", 0),
                "reminder_actions": action_counts.get("reminder_updated", 0),
                "comment_count": action_counts.get("comment_added", 0),
                "last_seen": last_seen,
            }
        )
    performance_rows.sort(key=lambda item: (item["follow_up_actions"], item["login_count"]), reverse=True)
    return performance_rows


def build_sla_dashboard(clinic_id: int = 1) -> dict[str, object]:
    tasks = fetch_receptionist_tasks(limit=1000, clinic_id=clinic_id)
    reminders = fetch_reminders(limit=1000, clinic_id=clinic_id)
    overdue_tasks = sum(1 for item in tasks if item["sla_status"] == "overdue")
    closed_tasks = sum(1 for item in tasks if item["status"] == "done")
    pending_reminders = sum(1 for item in reminders if item["status"] in {"pending", "ready"})
    sent_reminders = sum(1 for item in reminders if item["status"] == "sent")
    return {
        "overdue_tasks": overdue_tasks,
        "closed_tasks": closed_tasks,
        "task_closure_rate": round((closed_tasks / len(tasks)) * 100, 1) if tasks else 0.0,
        "pending_reminders": pending_reminders,
        "sent_reminders": sent_reminders,
        "reminder_completion_rate": round((sent_reminders / len(reminders)) * 100, 1) if reminders else 0.0,
    }


def apply_automation_rules(
    clinic_id: int,
    *,
    trigger_type: str,
    payload: dict[str, object],
) -> None:
    for rule in fetch_automation_rules(clinic_id):
        if not rule["is_enabled"] or rule["trigger_type"] != trigger_type:
            continue
        condition_key = str(rule["condition_key"] or "")
        condition_value = str(rule["condition_value"] or "")
        if condition_key and condition_value:
            value = str(payload.get(condition_key, ""))
            if value != condition_value:
                continue
        action_type = str(rule["action_type"])
        action_value = str(rule["action_value"] or "")
        if action_type == "create_notification":
            create_notification(clinic_id, f"Automation: {rule['name']}", action_value or "Rule triggered.", "/notifications")
        elif action_type == "create_task":
            task_id = str(uuid4())
            with get_db() as db:
                db.execute(
                    """
                    INSERT INTO receptionist_tasks (id, patient_name, phone_number, note, due_date, status, priority, tags, related_appointment_id, created_at, clinic_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        str(payload.get("patient_name") or "Workflow contact"),
                        str(payload.get("phone_number") or ""),
                        action_value or "Automation follow-up task",
                        datetime.now(UTC).date().isoformat(),
                        "open",
                        "high",
                        "automation",
                        str(payload.get("appointment_id") or "") or None,
                        datetime.now(UTC).isoformat(),
                        clinic_id,
                    ),
                )
                db.commit()
            log_audit("create", "automation_task", task_id, f"Automation rule {rule['name']} created a task.", clinic_id=clinic_id)
        elif action_type == "create_reminder" and payload.get("appointment_id"):
            reminder_id = str(uuid4())
            with get_db() as db:
                db.execute(
                    """
                    INSERT INTO reminder_queue (id, appointment_id, patient_name, phone_number, reminder_type, scheduled_for, status, note, created_at, clinic_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        reminder_id,
                        str(payload["appointment_id"]),
                        str(payload.get("patient_name") or "Patient"),
                        str(payload.get("phone_number") or ""),
                        "automation_follow_up",
                        datetime.now(UTC).isoformat(),
                        "pending",
                        action_value or "Automation reminder",
                        datetime.now(UTC).isoformat(),
                        clinic_id,
                    ),
                )
                db.commit()
            log_audit("create", "automation_reminder", reminder_id, f"Automation rule {rule['name']} queued a reminder.", clinic_id=clinic_id)


def build_benchmark_report() -> list[dict[str, object]]:
    clinics = fetch_clinics()
    rows: list[dict[str, object]] = []
    for clinic in clinics:
        clinic_id = int(clinic["id"])
        analytics = fetch_analytics(clinic_id)
        sla = build_sla_dashboard(clinic_id)
        health_score = max(
            0,
            min(
                100,
                int(
                    40
                    + analytics["conversion_rate"] * 0.6
                    + analytics["completion_rate"] * 0.3
                    - sla["overdue_tasks"] * 3
                ),
            ),
        )
        rows.append(
            {
                "clinic_name": clinic["clinic_name"],
                "business_type": str(clinic.get("business_type", "")).replace("_", " ").title(),
                "appointments": analytics["totals"]["appointments"],
                "calls": analytics["totals"]["calls"],
                "conversion_rate": analytics["conversion_rate"],
                "estimated_revenue_recovered": analytics["estimated_revenue_recovered"],
                "open_tasks": analytics["totals"]["open_tasks"],
                "front_desk_health_score": health_score,
                "forecast_next_month": analytics["estimated_revenue_recovered"] + analytics["pipeline_revenue_at_risk"],
            }
        )
    rows.sort(key=lambda item: (item["front_desk_health_score"], item["estimated_revenue_recovered"], item["conversion_rate"]), reverse=True)
    return rows


def build_report_summary(clinic_id: int = 1) -> list[dict[str, object]]:
    analytics = fetch_analytics(clinic_id)
    settings = fetch_clinic_settings(clinic_id)
    sla = build_sla_dashboard(clinic_id)
    forecast_next_month = analytics["estimated_revenue_recovered"] + analytics["pipeline_revenue_at_risk"]
    front_desk_health_score = max(
        0,
        min(
            100,
            int(40 + analytics["conversion_rate"] * 0.6 + analytics["completion_rate"] * 0.3 - sla["overdue_tasks"] * 3),
        ),
    )
    return [
        {"metric": "Clinic Name", "value": settings["clinic_name"]},
        {"metric": "Business Type", "value": str(settings.get("business_type", "")).replace("_", " ").title()},
        {"metric": "Appointments", "value": analytics["totals"]["appointments"]},
        {"metric": "Calls", "value": analytics["totals"]["calls"]},
        {"metric": "Conversion Rate", "value": f"{analytics['conversion_rate']}%"},
        {"metric": "Completed Rate", "value": f"{analytics['completion_rate']}%"},
        {"metric": "Estimated Revenue Recovered", "value": analytics["estimated_revenue_recovered"]},
        {"metric": "Pipeline Revenue At Risk", "value": analytics["pipeline_revenue_at_risk"]},
        {"metric": "Next 30 Day Revenue Forecast", "value": forecast_next_month},
        {"metric": "Front Desk Health Score", "value": f"{front_desk_health_score}/100"},
        {"metric": "Open Tasks", "value": analytics["totals"]["open_tasks"]},
        {"metric": "Pending Reminders", "value": analytics["totals"]["pending_reminders"]},
    ]


def create_auto_callback_task(record: CallRecord, clinic_id: int = 1) -> None:
    with get_db() as db:
        existing = db.execute(
            """
            SELECT id FROM receptionist_tasks
            WHERE clinic_id = ? AND phone_number = ? AND status != 'done' AND date(created_at) = date('now')
            LIMIT 1
            """,
            (clinic_id, record.caller_number),
        ).fetchone()
        if existing:
            return
        task_id = str(uuid4())
        db.execute(
            """
            INSERT INTO receptionist_tasks (id, patient_name, phone_number, note, due_date, status, priority, related_appointment_id, created_at, clinic_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                record.patient_name or "Unknown caller",
                record.caller_number,
                f"Auto callback from {record.intent.replace('_', ' ')} call. Review notes and call back.",
                datetime.now(UTC).date().isoformat(),
                "open",
                "high" if record.urgent or record.lead_score == "hot" else "medium",
                None,
                datetime.now(UTC).isoformat(),
                clinic_id,
            ),
        )
        db.commit()


def is_authenticated(request: Request) -> bool:
    return bool(request.session.get("dentvoice_authenticated"))


def get_active_clinic_id(request: Request | None = None) -> int:
    if request and request.session.get("dentvoice_clinic_id"):
        return int(request.session["dentvoice_clinic_id"])
    return 1


def get_current_role(request: Request | None = None) -> str:
    if request and request.session.get("dentvoice_role"):
        return str(request.session["dentvoice_role"])
    return "admin"


def require_authenticated_page(request: Request) -> RedirectResponse | None:
    if is_authenticated(request):
        return None
    return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)


def require_authenticated_api(request: Request) -> None:
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Please log in to continue.")


def require_admin(request: Request) -> None:
    require_authenticated_api(request)
    if get_current_role(request) != "admin":
        raise HTTPException(status_code=403, detail="Only admin users can perform this action.")


def valid_hex_color(value: str) -> bool:
    if len(value) != 7 or not value.startswith("#"):
        return False
    return all(character in "0123456789abcdefABCDEF" for character in value[1:])


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "clinic"


def ensure_unique_slug(base_slug: str) -> str:
    slug = slugify(base_slug)
    suffix = 1
    with get_db() as db:
        while db.execute("SELECT 1 FROM clinics WHERE slug = ?", (slug,)).fetchone():
            suffix += 1
            slug = f"{slugify(base_slug)}-{suffix}"
    return slug


def normalize_branding(settings: dict[str, str]) -> dict[str, str]:
    accent = settings.get("accent_color", "#146c78")
    if not valid_hex_color(accent):
        accent = "#146c78"
    logo_text = (settings.get("logo_text") or "DV")[:4]
    if settings.get("white_label_enabled") and settings.get("white_label_name"):
        initials = "".join(part[:1] for part in str(settings["white_label_name"]).split()[:2]).upper()
        logo_text = (initials or logo_text)[:4]
    return {
        "logo_text": logo_text,
        "brand_tagline": settings.get("brand_tagline") or "AI receptionist for dental clinics",
        "accent_color": accent,
    }


def build_default_tagline(clinic_name: str, business_type: str) -> str:
    template = INDUSTRY_TEMPLATES.get(business_type)
    if template:
        return str(template["tagline"]).replace("the clinic", clinic_name)
    return f"AI front desk for {clinic_name}"


def check_double_booking(preferred_date: str, preferred_time: str, *, exclude_appointment_id: str | None = None, clinic_id: int = 1) -> None:
    with get_db() as db:
        query = """
            SELECT id
            FROM appointments
            WHERE preferred_date = ? AND preferred_time = ? AND status != 'cancelled' AND clinic_id = ?
        """
        params: list[object] = [preferred_date, preferred_time, clinic_id]
        if exclude_appointment_id:
            query += " AND id != ?"
            params.append(exclude_appointment_id)
        conflict = db.execute(query, params).fetchone()
        if conflict:
            raise HTTPException(status_code=409, detail="This appointment slot is already booked.")


def escape_xml(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def say(text: str) -> str:
    return f"<Say voice=\"alice\">{escape_xml(text)}</Say>"


def gather(action: str, prompt: str, *, num_digits: int | None = None) -> str:
    attrs = [
        f'action="{escape_xml(action)}"',
        'method="POST"',
        'input="speech dtmf"',
        'speechTimeout="auto"',
        'timeout="5"',
    ]
    if num_digits is not None:
        attrs.append(f'numDigits="{num_digits}"')
    return f"<Gather {' '.join(attrs)}>{say(prompt)}</Gather>"


def redirect(url: str) -> str:
    return f"<Redirect method=\"POST\">{escape_xml(url)}</Redirect>"


def twiml(*parts: str) -> str:
    body = "".join(parts)
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'


def infer_lead_score(intent: str, urgent: bool) -> str:
    if urgent or intent == "emergency":
        return "hot"
    if intent in {"appointment_booking", "reschedule"}:
        return "warm"
    return "cold"


def lookup_slot(option: str | None, speech_result: str | None = None, clinic_id: int = 1) -> dict[str, str] | None:
    available_slots = fetch_slots(clinic_id)
    if option:
        for slot in available_slots:
            if slot["option"] == option:
                return slot
    if speech_result:
        text = speech_result.lower()
        if "first" in text or "one" in text or "1" in text:
            return available_slots[0] if len(available_slots) > 0 else None
        if "second" in text or "two" in text or "2" in text:
            return available_slots[1] if len(available_slots) > 1 else None
        if "third" in text or "three" in text or "3" in text:
            return available_slots[2] if len(available_slots) > 2 else None
    return None


def classify_intent(transcript: str) -> str:
    text = transcript.lower()
    if any(word in text for word in ["pain", "bleeding", "swelling", "emergency", "urgent"]):
        return "emergency"
    if any(word in text for word in ["book", "appointment", "visit", "checkup", "consultation"]):
        return "appointment_booking"
    if any(word in text for word in ["reschedule", "change time", "postpone", "cancel"]):
        return "reschedule"
    if any(word in text for word in ["price", "pricing", "cost", "fees", "charge"]):
        return "pricing"
    if any(word in text for word in ["location", "where", "address", "directions"]):
        return "directions"
    if any(word in text for word in ["timing", "hours", "open", "service", "available"]):
        return "faq"
    return "general"


def create_summary(intent: str, payload: SimulatedCallPayload) -> str:
    if intent == "emergency":
        return f"Urgent dental concern reported by {payload.patient_name or 'caller'}. Immediate clinic follow-up recommended."
    if intent == "appointment_booking":
        return (
            f"Appointment request from {payload.patient_name or 'caller'} for "
            f"{payload.preferred_date or 'requested date'} at {payload.preferred_time or 'requested time'}."
        )
    if intent == "reschedule":
        return "Caller wants to reschedule an existing appointment. Follow-up needed for confirmation."
    if intent == "pricing":
        return "Caller asked for treatment pricing details. Staff should share consultation-based pricing."
    if intent == "directions":
        return "Caller requested clinic location and directions."
    if intent == "faq":
        return "Caller asked a routine clinic question handled by the AI receptionist."
    return "General patient inquiry captured for staff review."


def send_whatsapp_confirmation(phone_number: str, patient_name: str | None, details: str, clinic_id: int = 1) -> WhatsAppMessage:
    clinic_name = fetch_clinic_settings(clinic_id)["clinic_name"]
    item = WhatsAppMessage(
        phone_number=phone_number,
        message=(
            f"Hello {patient_name or 'there'}, thanks for contacting {clinic_name}. "
            f"We have received your request: {details}. Our team will confirm shortly."
        ),
    )
    with get_db() as db:
        db.execute(
            "INSERT INTO whatsapp_messages (phone_number, message, created_at, clinic_id) VALUES (?, ?, ?, ?)",
            (item.phone_number, item.message, item.created_at, clinic_id),
        )
        db.commit()
    return item


def create_appointment_record(
    *,
    patient_name: str,
    phone_number: str,
    preferred_date: str,
    preferred_time: str,
    reason_for_visit: str,
    source: Literal["api", "simulated_call", "voice_call", "admin"],
    status: Literal["new", "confirmed", "completed", "cancelled", "needs_follow_up"] = "confirmed",
    notes: str = "",
    clinic_id: int = 1,
) -> AppointmentRequest:
    check_double_booking(preferred_date, preferred_time, clinic_id=clinic_id)
    appointment = AppointmentRequest(
        patient_name=patient_name,
        phone_number=phone_number,
        preferred_date=preferred_date,
        preferred_time=preferred_time,
        reason_for_visit=reason_for_visit,
        source=source,
        status=status,
        notes=notes,
    )
    with get_db() as db:
        db.execute(
            """
            INSERT INTO appointments (
                id, patient_name, phone_number, preferred_date, preferred_time, reason_for_visit, status, source, notes, created_at, clinic_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                appointment.id,
                appointment.patient_name,
                appointment.phone_number,
                appointment.preferred_date,
                appointment.preferred_time,
                appointment.reason_for_visit,
                appointment.status,
                appointment.source,
                appointment.notes,
                appointment.created_at,
                clinic_id,
            ),
        )
        db.commit()
    send_whatsapp_confirmation(phone_number, patient_name, f"appointment booked for {preferred_date} at {preferred_time}", clinic_id=clinic_id)
    apply_automation_rules(
        clinic_id,
        trigger_type="appointment_created",
        payload={
            "appointment_id": appointment.id,
            "patient_name": patient_name,
            "phone_number": phone_number,
            "source": source,
            "status": status,
        },
    )
    log_audit("create", "appointment", appointment.id, f"Created appointment for {patient_name} on {preferred_date} at {preferred_time}.", clinic_id=clinic_id)
    return appointment


def create_call_record(
    *,
    caller_number: str,
    patient_name: str | None,
    intent: str,
    summary: str,
    urgent: bool = False,
    appointment_request: AppointmentRequest | None = None,
    clinic_id: int = 1,
) -> CallRecord:
    record = CallRecord(
        caller_number=caller_number,
        patient_name=patient_name,
        intent=intent,
        summary=summary,
        urgent=urgent,
        lead_score=infer_lead_score(intent, urgent),
        appointment_request=appointment_request,
    )
    with get_db() as db:
        db.execute(
            """
            INSERT INTO call_records (
                id, caller_number, patient_name, intent, summary, urgent, lead_score, internal_notes, appointment_id, created_at, clinic_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.caller_number,
                record.patient_name,
                record.intent,
                record.summary,
                int(record.urgent),
                record.lead_score,
                record.internal_notes,
                appointment_request.id if appointment_request else None,
                record.created_at,
                clinic_id,
            ),
        )
        db.commit()
    if fetch_clinic_settings(clinic_id).get("auto_callback_enabled", 1):
        if appointment_request is None and (urgent or intent in {"appointment_booking", "reschedule", "pricing"}):
            create_auto_callback_task(record, clinic_id=clinic_id)
    apply_automation_rules(
        clinic_id,
        trigger_type="call_logged",
        payload={
            "patient_name": patient_name or "",
            "phone_number": caller_number,
            "intent": intent,
            "lead_score": record.lead_score,
            "urgent": "true" if urgent else "false",
        },
    )
    log_audit("create", "call_record", record.id, f"Logged {intent} call from {caller_number}.", clinic_id=clinic_id)
    return record


def get_or_create_session(call_sid: str, caller_number: str) -> CallSession:
    if call_sid not in call_sessions:
        call_sessions[call_sid] = CallSession(call_sid=call_sid, caller_number=caller_number)
    return call_sessions[call_sid]


def slot_prompt(clinic_id: int = 1) -> str:
    available_slots = fetch_slots(clinic_id)
    prompts = []
    for slot in available_slots[:3]:
        prompts.append(f"Press or say {slot['option']} for {slot['date']} at {slot['time'].replace(':', ' ')}")
    return "Please choose a slot. " + ". ".join(prompts) + "."


def build_notifications(clinic_id: int = 1) -> list[dict[str, str]]:
    notifications: list[dict[str, str]] = []
    for item in fetch_call_records(limit=3, clinic_id=clinic_id):
        if item.urgent:
            notifications.append({"kind": "Urgent call", "message": f"{item.patient_name or item.caller_number} needs immediate attention.", "href": "/calls"})
    for item in fetch_receptionist_tasks(limit=3, clinic_id=clinic_id):
        if item["priority"] == "high" and item["status"] != "done":
            notifications.append({"kind": "High priority task", "message": f"{item['patient_name']} has a {item['status'].replace('_', ' ')} task due {item['due_date']}.", "href": "/inbox"})
    for item in fetch_reminders(limit=3, status="pending", clinic_id=clinic_id):
        notifications.append({"kind": "Pending reminder", "message": f"{item['patient_name']} has a pending {item['reminder_type'].replace('_', ' ')}.", "href": "/reminders"})
    for item in fetch_notifications(clinic_id=clinic_id, unread_only=True, limit=3):
        notifications.append({"kind": item["title"], "message": item["message"], "href": item["href"] or "/notifications"})
    return notifications[:8]


def build_setup_progress(clinic_id: int = 1) -> dict[str, object]:
    settings = fetch_clinic_settings(clinic_id)
    slots = fetch_slots(clinic_id)
    faqs = fetch_faq_entries(limit=50, clinic_id=clinic_id)
    appointments = fetch_appointments(limit=50, clinic_id=clinic_id)
    reminders = fetch_reminders(limit=50, clinic_id=clinic_id)
    contacts = [item for item in fetch_contact_requests(limit=200) if item["clinic_name"].lower() == settings["clinic_name"].lower()]
    onboarding_state = fetch_onboarding_state(clinic_id)

    steps = [
        {
            "key": "template",
            "label": "Apply an industry template",
            "done": bool(settings.get("business_type")),
            "hint": "Choose the closest vertical so the copy, hours, FAQs, and benchmark values feel specific.",
            "href": "/setup#template-library",
        },
        {
            "key": "branding",
            "label": "Brand the clinic page",
            "done": bool(settings.get("brand_tagline") and settings.get("logo_text") and settings.get("accent_color")),
            "hint": "Set the clinic name, tagline, logo text, and accent color so the landing page looks client-ready.",
            "href": "#settings-form",
        },
        {
            "key": "hours",
            "label": "Define working hours",
            "done": bool(settings.get("working_days") and settings.get("working_hours")),
            "hint": "Working days and hours control slot generation and make the demo feel more real.",
            "href": "#settings-form",
        },
        {
            "key": "slots",
            "label": "Review booking slots",
            "done": len(slots) >= 3,
            "hint": "Keep at least 3 live booking slots so the call workflow can always book a patient.",
            "href": "#slot-form",
        },
        {
            "key": "faqs",
            "label": "Customize FAQs",
            "done": len(faqs) >= 4,
            "hint": "Edit the receptionist answers so the workflow sounds specific to the clinic or business type.",
            "href": "/dashboard#faq-manager",
        },
        {
            "key": "appointments",
            "label": "Capture first appointments",
            "done": len(appointments) >= 1,
            "hint": "Seed demo data or create the first booking so analytics and reminders become visible.",
            "href": "/dashboard",
        },
        {
            "key": "reminders",
            "label": "Queue reminders",
            "done": len(reminders) >= 1,
            "hint": "Set up reminder workflows so the clinic can see confirmations and follow-up operations.",
            "href": "/reminders",
        },
        {
            "key": "lead_capture",
            "label": "Collect demand",
            "done": len(contacts) >= 1,
            "hint": "Use the public landing page to capture demo requests and prove the lead funnel works.",
            "href": "/leads",
        },
        {
            "key": "go_live",
            "label": "Mark workspace launch-ready",
            "done": "go_live" in onboarding_state,
            "hint": "Once the checklist feels solid, mark the workspace ready for real demos and founder outreach.",
            "href": "/setup#go-live-card",
        },
    ]
    for item in steps:
        item["done"] = bool(item["done"] or item["key"] in onboarding_state)
    completed = sum(1 for item in steps if item["done"])
    percent = round((completed / len(steps)) * 100) if steps else 0
    current_step = next((item for item in steps if not item["done"]), steps[-1] if steps else None)
    return {
        "steps": steps,
        "completed": completed,
        "total": len(steps),
        "percent": percent,
        "launch_ready": percent >= 80 and "go_live" in onboarding_state,
        "current_step": current_step,
    }


def build_company_growth_metrics() -> dict[str, object]:
    contacts = fetch_contact_requests(limit=5000)
    paid_accounts = sum(1 for item in contacts if item["status"] == "paid")
    active_trials = sum(1 for item in contacts if item["status"] in {"demo_booked", "trial_active"})
    self_serve_signups = sum(1 for item in contacts if "Self-serve workspace signup" in item["message"])
    qualified_leads = sum(1 for item in contacts if item["status"] in {"qualified", "demo_booked", "trial_active", "paid"})
    demo_to_paid_rate = round((paid_accounts / self_serve_signups) * 100, 1) if self_serve_signups else 0.0
    lead_to_trial_rate = round((active_trials / qualified_leads) * 100, 1) if qualified_leads else 0.0
    estimated_mrr = paid_accounts * 4999
    return {
        "self_serve_signups": self_serve_signups,
        "qualified_leads": qualified_leads,
        "active_trials": active_trials,
        "paid_accounts": paid_accounts,
        "demo_to_paid_rate": demo_to_paid_rate,
        "lead_to_trial_rate": lead_to_trial_rate,
        "estimated_mrr": estimated_mrr,
    }


def build_dashboard_context(request: Request | None = None, clinic_id: int | None = None) -> dict[str, object]:
    active_clinic_id = clinic_id or get_active_clinic_id(request)
    analytics = fetch_analytics(active_clinic_id)
    settings = fetch_clinic_settings(active_clinic_id)
    branding = normalize_branding(settings)
    company_growth = build_company_growth_metrics()
    staff_performance = build_staff_performance(active_clinic_id)
    sla_dashboard = build_sla_dashboard(active_clinic_id)
    announcements = fetch_announcements(active_clinic_id, limit=10)
    automation_rules = fetch_automation_rules(active_clinic_id, limit=50)
    current_role = get_current_role(request)
    return {
        "stats": analytics["totals"],
        "appointments": fetch_appointments(limit=10, clinic_id=active_clinic_id),
        "call_records": fetch_call_records(limit=10, clinic_id=active_clinic_id),
        "messages": fetch_messages(limit=10, clinic_id=active_clinic_id),
        "contact_requests": fetch_contact_requests(limit=5),
        "missed_leads": fetch_missed_leads(limit=5, clinic_id=active_clinic_id),
        "audit_logs": fetch_audit_logs(limit=8, clinic_id=active_clinic_id),
        "receptionist_tasks": fetch_receptionist_tasks(limit=8, clinic_id=active_clinic_id),
        "reminders": fetch_reminders(limit=8, clinic_id=active_clinic_id),
        "reminder_candidates": fetch_upcoming_reminder_candidates(limit=6, clinic_id=active_clinic_id),
        "calendar_entries": fetch_calendar_entries(active_clinic_id)[:5],
        "calendar_views": fetch_calendar_views(active_clinic_id),
        "patient_profiles": fetch_patient_profiles(limit=5, clinic_id=active_clinic_id),
        "faqs": fetch_faq_entries(limit=8, clinic_id=active_clinic_id),
        "slots": fetch_slots(active_clinic_id),
        "settings": settings,
        "clinics": fetch_clinics(),
        "current_clinic_id": active_clinic_id,
        "branding": branding,
        "notifications": build_notifications(active_clinic_id),
        "setup_progress": build_setup_progress(active_clinic_id),
        "company_growth": company_growth,
        "industry_templates": INDUSTRY_TEMPLATES,
        "business_types": BUSINESS_TYPES,
        "analytics": analytics,
        "team_users": fetch_clinic_users(active_clinic_id),
        "notification_center": fetch_notifications(active_clinic_id, limit=100),
        "unread_notification_count": len(fetch_notifications(active_clinic_id, unread_only=True, limit=100)),
        "referrals": fetch_referrals(active_clinic_id),
        "onboarding_emails": fetch_onboarding_emails(active_clinic_id),
        "benchmark_report": build_benchmark_report(),
        "report_summary": build_report_summary(active_clinic_id),
        "staff_performance": staff_performance,
        "sla_dashboard": sla_dashboard,
        "announcements": announcements,
        "automation_rules": automation_rules,
        "access_logs": fetch_access_logs(active_clinic_id, limit=100),
        "chartjs_data": json.dumps(build_chartjs_datasets(analytics)),
        "analytics_charts": {
            "appointments_by_status": build_chart(analytics["appointments_by_status"], APPOINTMENT_STATUSES),
            "appointments_by_source": build_chart(analytics["appointments_by_source"], APPOINTMENT_SOURCES),
            "calls_by_intent": build_chart(analytics["calls_by_intent"], CALL_INTENTS),
            "calls_by_lead_score": build_chart(analytics["calls_by_lead_score"], LEAD_SCORES),
            "tasks_by_status": build_chart(analytics["tasks_by_status"], TASK_STATUSES),
            "tasks_by_priority": build_chart(analytics["tasks_by_priority"], TASK_PRIORITIES),
            "contacts_by_status": build_chart(analytics["contacts_by_status"], CONTACT_STATUSES),
            "reminders_by_status": build_chart(analytics["reminders_by_status"], ["pending", "ready", "sent", "cancelled"]),
            "recent_days": build_trend_chart(analytics["recent_days"]),
        },
        "statuses": APPOINTMENT_STATUSES,
        "lead_scores": LEAD_SCORES,
        "task_statuses": TASK_STATUSES,
        "task_priorities": TASK_PRIORITIES,
        "contact_statuses": CONTACT_STATUSES,
        "current_role": current_role,
        "dashboard_variant": "admin" if current_role == "admin" else "receptionist",
        "session_display_name": request.session.get("dentvoice_display_name") if request else "",
        "session_username": request.session.get("dentvoice_username") if request else "",
        "asset_version": ASSET_VERSION,
    }


def csv_response(filename: str, fieldnames: list[str], rows: list[dict[str, object]]) -> StreamingResponse:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


init_db()


@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request) -> HTMLResponse:
    context = build_dashboard_context(request, clinic_id=1)
    context.update({"is_authenticated": is_authenticated(request)})
    return templates.TemplateResponse(request, "landing.html", context)


@app.get("/clinic/{slug}", response_class=HTMLResponse)
async def clinic_landing_page(request: Request, slug: str) -> HTMLResponse:
    clinic = fetch_clinic_by_slug(slug)
    if clinic is None:
        raise HTTPException(status_code=404, detail="Clinic not found")
    context = build_dashboard_context(request, clinic_id=int(clinic["id"]))
    context.update({"is_authenticated": is_authenticated(request)})
    return templates.TemplateResponse(request, "landing.html", context)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = Query(default="/dashboard"), error: str = Query(default="")) -> HTMLResponse:
    if is_authenticated(request):
        return RedirectResponse(url=next or "/dashboard", status_code=303)
    context = build_dashboard_context(request)
    context.update({"next_url": next, "login_error": error})
    return templates.TemplateResponse(request, "login.html", context)


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    if get_current_role(request) != "admin":
        return RedirectResponse(url="/dashboard", status_code=303)
    context = build_dashboard_context(request)
    context.update({"page_title": "Setup Workspace", "is_authenticated": True})
    return templates.TemplateResponse(request, "setup.html", context)


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next_url: str = Form(default="/dashboard"),
) -> Response:
    with get_db() as db:
        user = db.execute(
            """
            SELECT clinic_id, role, display_name, username
            FROM clinic_users
            WHERE username = ? AND password = ? AND is_active = 1
            """,
            (username, password),
        ).fetchone()
    if user:
        request.session["dentvoice_authenticated"] = True
        request.session["dentvoice_clinic_id"] = int(user["clinic_id"])
        request.session["dentvoice_role"] = str(user["role"])
        request.session["dentvoice_display_name"] = str(user["display_name"])
        request.session["dentvoice_username"] = str(user["username"])
        log_access_event(request, int(user["clinic_id"]), str(user["username"]), str(user["role"]), "login", "User signed in")
        return RedirectResponse(url=next_url or "/dashboard", status_code=303)
    return RedirectResponse(url=f"/login?next={next_url or '/dashboard'}&error=Invalid credentials", status_code=303)


@app.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    if is_authenticated(request):
        log_access_event(
            request,
            get_active_clinic_id(request),
            str(request.session.get("dentvoice_username") or ""),
            str(request.session.get("dentvoice_role") or ""),
            "logout",
            "User signed out",
        )
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context(request)
    context.update({"is_authenticated": True})
    return templates.TemplateResponse(request, "dashboard.html", context)


@app.get("/appointments", response_class=HTMLResponse)
async def appointments_page(
    request: Request,
    q: str = Query(default=""),
    status: str = Query(default=""),
    source: str = Query(default=""),
    preferred_date: str = Query(default=""),
    sort: str = Query(default="created_desc"),
) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context(request)
    context.update(
        {
            "page_title": "Appointments",
            "appointments": fetch_appointments(limit=200, search=q, status=status, source=source, preferred_date=preferred_date, sort=sort, clinic_id=get_active_clinic_id(request)),
            "filters": {"q": q, "status": status, "source": source, "preferred_date": preferred_date, "sort": sort},
            "is_authenticated": True,
        }
    )
    return templates.TemplateResponse(request, "appointments.html", context)


@app.get("/calls", response_class=HTMLResponse)
async def calls_page(
    request: Request,
    q: str = Query(default=""),
    intent: str = Query(default=""),
    urgent_only: bool = Query(default=False),
    lead_score: str = Query(default=""),
    sort: str = Query(default="created_desc"),
) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context(request)
    context.update(
        {
            "page_title": "Calls",
            "call_records": fetch_call_records(limit=200, search=q, intent=intent, urgent_only=urgent_only, lead_score=lead_score, sort=sort, clinic_id=get_active_clinic_id(request)),
            "filters": {"q": q, "intent": intent, "urgent_only": urgent_only, "lead_score": lead_score, "sort": sort},
            "call_intents": CALL_INTENTS,
            "is_authenticated": True,
        }
    )
    return templates.TemplateResponse(request, "calls.html", context)


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context(request)
    context.update({"page_title": "Analytics", "is_authenticated": True})
    return templates.TemplateResponse(request, "analytics.html", context)


@app.get("/inbox", response_class=HTMLResponse)
async def inbox_page(
    request: Request,
    q: str = Query(default=""),
    status: str = Query(default=""),
    priority: str = Query(default=""),
) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context(request)
    context.update(
        {
            "page_title": "Receptionist Inbox",
            "receptionist_tasks": fetch_receptionist_tasks(limit=200, status=status, search=q, priority=priority, clinic_id=get_active_clinic_id(request)),
            "task_comments": {item["id"]: fetch_comments("task", item["id"], clinic_id=get_active_clinic_id(request), limit=3) for item in fetch_receptionist_tasks(limit=200, status=status, search=q, priority=priority, clinic_id=get_active_clinic_id(request))},
            "filters": {"q": q, "status": status, "priority": priority},
            "is_authenticated": True,
        }
    )
    return templates.TemplateResponse(request, "inbox.html", context)


@app.get("/reminders", response_class=HTMLResponse)
async def reminders_page(
    request: Request,
    status: str = Query(default=""),
) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context(request)
    context.update(
        {
            "page_title": "Reminder Queue",
            "reminders": fetch_reminders(limit=200, status=status, clinic_id=get_active_clinic_id(request)),
            "reminder_candidates": fetch_upcoming_reminder_candidates(limit=25, clinic_id=get_active_clinic_id(request)),
            "filters": {"status": status},
            "is_authenticated": True,
        }
    )
    return templates.TemplateResponse(request, "reminders.html", context)


@app.get("/search", response_class=HTMLResponse)
async def search_page(
    request: Request,
    q: str = Query(default=""),
    segment: str = Query(default=""),
) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context(request)
    search_results = fetch_global_search_results(q, clinic_id=get_active_clinic_id(request))
    if segment:
        search_results = {key: value for key, value in search_results.items() if key == segment}
    context.update(
        {
            "page_title": "Global Search",
            "search_query": q,
            "search_segment": segment,
            "search_segments": ["appointments", "calls", "patients", "leads", "tasks", "faqs", "reminders"],
            "search_results": search_results,
            "is_authenticated": True,
        }
    )
    return templates.TemplateResponse(request, "search.html", context)


@app.get("/docs", response_class=HTMLResponse)
async def docs_page(request: Request) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context(request)
    context.update({"page_title": "Docs", "is_authenticated": True})
    return templates.TemplateResponse(request, "docs.html", context)


@app.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context(request)
    context.update({"page_title": "Calendar", "calendar_entries": fetch_calendar_entries(get_active_clinic_id(request)), "calendar_views": fetch_calendar_views(get_active_clinic_id(request)), "is_authenticated": True})
    return templates.TemplateResponse(request, "calendar.html", context)


@app.get("/patients", response_class=HTMLResponse)
async def patients_page(request: Request, q: str = Query(default=""), sort: str = Query(default="latest_desc")) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context(request)
    context.update({"page_title": "Patients", "patient_profiles": fetch_patient_profiles(limit=300, search=q, clinic_id=get_active_clinic_id(request), sort=sort), "filters": {"q": q, "sort": sort}, "is_authenticated": True})
    return templates.TemplateResponse(request, "patients.html", context)


@app.get("/patients/detail", response_class=HTMLResponse)
async def patient_detail_page(request: Request, phone: str = Query(...)) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    detail = fetch_patient_detail(phone, clinic_id=get_active_clinic_id(request))
    if detail is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    context = build_dashboard_context(request)
    context.update({"page_title": "Patient Detail", "patient_detail": detail, "is_authenticated": True})
    return templates.TemplateResponse(request, "patient_detail.html", context)


@app.get("/leads", response_class=HTMLResponse)
async def leads_page(
    request: Request,
    q: str = Query(default=""),
    status: str = Query(default=""),
    sort: str = Query(default="newest"),
) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context(request)
    lead_records = fetch_contact_requests(limit=200, search=q, status=status, sort=sort)
    context.update(
        {
            "page_title": "Demo Request CRM",
            "contact_requests": lead_records,
            "lead_pipeline": {item: [lead for lead in lead_records if lead["status"] == item] for item in CONTACT_STATUSES},
            "lead_comments": {item["id"]: fetch_comments("lead", item["id"], clinic_id=get_active_clinic_id(request), limit=3) for item in lead_records},
            "filters": {"q": q, "status": status, "sort": sort},
            "is_authenticated": True,
        }
    )
    return templates.TemplateResponse(request, "leads.html", context)


@app.get("/missed-leads", response_class=HTMLResponse)
async def missed_leads_page(
    request: Request,
    q: str = Query(default=""),
    lead_score: str = Query(default=""),
) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context(request)
    context.update(
        {
            "page_title": "Missed Lead Recovery",
            "missed_leads": fetch_missed_leads(limit=200, search=q, lead_score=lead_score, clinic_id=get_active_clinic_id(request)),
            "filters": {"q": q, "lead_score": lead_score},
            "is_authenticated": True,
        }
    )
    return templates.TemplateResponse(request, "missed_leads.html", context)


@app.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context(request)
    context.update({"page_title": "Audit Log", "audit_logs": fetch_audit_logs(limit=200, clinic_id=get_active_clinic_id(request)), "is_authenticated": True})
    return templates.TemplateResponse(request, "audit.html", context)


@app.get("/exports", response_class=HTMLResponse)
async def exports_page(request: Request) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context(request)
    context.update({"page_title": "Export Center", "is_authenticated": True})
    return templates.TemplateResponse(request, "exports.html", context)


@app.get("/team", response_class=HTMLResponse)
async def team_page(request: Request) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    require_admin(request)
    context = build_dashboard_context(request)
    context.update({"page_title": "Team Management", "is_authenticated": True})
    return templates.TemplateResponse(request, "team.html", context)


@app.get("/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context(request)
    context.update({"page_title": "Notifications", "is_authenticated": True})
    return templates.TemplateResponse(request, "notifications.html", context)


@app.get("/hq", response_class=HTMLResponse)
async def hq_page(request: Request) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    require_admin(request)
    context = build_dashboard_context(request)
    context.update({"page_title": "HQ Dashboard", "is_authenticated": True})
    return templates.TemplateResponse(request, "hq.html", context)


@app.get("/benchmarks", response_class=HTMLResponse)
async def benchmarks_page(request: Request) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context(request)
    context.update({"page_title": "Benchmarks", "is_authenticated": True})
    return templates.TemplateResponse(request, "benchmarks.html", context)


@app.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context(request)
    context.update({"page_title": "Reports", "is_authenticated": True})
    return templates.TemplateResponse(request, "reports.html", context)


@app.get("/solutions/{business_type}", response_class=HTMLResponse)
async def solutions_page(request: Request, business_type: str) -> HTMLResponse:
    template = INDUSTRY_TEMPLATES.get(business_type)
    if template is None:
        raise HTTPException(status_code=404, detail="Solution page not found")
    context = build_dashboard_context(request, clinic_id=1)
    context.update({"page_title": f"{template['label']} Solution", "case_template": template, "case_key": business_type, "is_authenticated": is_authenticated(request)})
    return templates.TemplateResponse(request, "solution_case.html", context)


@app.get("/health")
async def healthcheck() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/api/dashboard")
async def dashboard_data(request: Request) -> JSONResponse:
    require_authenticated_api(request)
    context = build_dashboard_context(request)
    return JSONResponse(
        {
            "stats": context["stats"],
            "appointments": [item.model_dump() for item in context["appointments"]],
            "calls": [item.model_dump() for item in context["call_records"]],
            "messages": [item.model_dump() for item in context["messages"]],
            "slots": context["slots"],
            "settings": context["settings"],
            "analytics": context["analytics"],
        }
    )


@app.post("/api/clinic/switch")
async def switch_clinic(request: Request, clinic_id: int = Form(...)) -> JSONResponse:
    require_admin(request)
    with get_db() as db:
        clinic = db.execute("SELECT id FROM clinics WHERE id = ?", (clinic_id,)).fetchone()
    if clinic is None:
        raise HTTPException(status_code=404, detail="Clinic not found")
    request.session["dentvoice_clinic_id"] = clinic_id
    return JSONResponse({"message": "Active clinic changed"})


@app.post("/api/clinics")
async def create_clinic(
    request: Request,
    slug: str = Form(...),
    clinic_name: str = Form(...),
    clinic_timings: str = Form(...),
    clinic_address: str = Form(...),
    brand_tagline: str = Form(...),
    accent_color: str = Form(...),
    logo_text: str = Form(...),
    business_type: str = Form(default="dental"),
    avg_booking_value: int = Form(default=5000),
    working_days: str = Form(...),
    working_hours: str = Form(...),
) -> JSONResponse:
    require_admin(request)
    if not valid_hex_color(accent_color):
        raise HTTPException(status_code=400, detail="Accent color must be a valid hex value like #146c78.")
    create_clinic_workspace(
        slug=slug,
        clinic_name=clinic_name,
        clinic_timings=clinic_timings,
        clinic_address=clinic_address,
        brand_tagline=brand_tagline,
        accent_color=accent_color,
        logo_text=logo_text,
        business_type=business_type,
        avg_booking_value=avg_booking_value,
        working_days=working_days,
        working_hours=working_hours,
    )
    return JSONResponse({"message": "Clinic created"})


@app.post("/api/trial-signup")
async def create_trial_signup(
    request: Request,
    owner_name: str = Form(...),
    clinic_name: str = Form(...),
    phone_number: str = Form(...),
    business_type: str = Form(...),
    city: str = Form(...),
) -> JSONResponse:
    slug = ensure_unique_slug(clinic_name)
    template = INDUSTRY_TEMPLATES.get(business_type, INDUSTRY_TEMPLATES["dental"])
    clinic_address = f"{city} · Self-serve demo workspace"
    workspace = create_clinic_workspace(
        slug=slug,
        clinic_name=clinic_name,
        clinic_timings=str(template["timings_label"]),
        clinic_address=clinic_address,
        brand_tagline=build_default_tagline(clinic_name, business_type),
        accent_color=str(template["accent_color"]),
        logo_text="DV",
        business_type=business_type,
        avg_booking_value=int(template["avg_booking_value"]),
        working_days=str(template["working_days"]),
        working_hours=str(template["working_hours"]),
    )
    request_id = str(uuid4())
    created_at = datetime.now(UTC).isoformat()
    with get_db() as db:
        db.execute(
            """
            INSERT INTO contact_requests (id, name, clinic_name, phone_number, message, created_at, status, owner_notes, business_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                owner_name,
                clinic_name,
                phone_number,
                f"Self-serve workspace signup\nBusiness type: {business_type}\nCity: {city}\nAuto-generated admin: {workspace['admin_username']}",
                created_at,
                "trial_active",
                "Auto-created from public self-serve signup.",
                business_type,
            ),
        )
        db.commit()
    request.session["dentvoice_authenticated"] = True
    request.session["dentvoice_clinic_id"] = int(workspace["clinic_id"])
    request.session["dentvoice_role"] = "admin"
    request.session["dentvoice_display_name"] = f"{clinic_name} Admin"
    request.session["dentvoice_username"] = str(workspace["admin_username"])
    log_access_event(request, int(workspace["clinic_id"]), str(workspace["admin_username"]), "admin", "login", "Self-serve workspace signup")
    return JSONResponse(
        {
            "message": "Workspace created",
            "redirect_url": "/setup",
            "admin_username": workspace["admin_username"],
            "password": "dentvoice123",
        }
    )


@app.get("/api/available-slots")
async def available_slots(request: Request) -> JSONResponse:
    clinic_id = get_active_clinic_id(request) if is_authenticated(request) else 1
    return JSONResponse({"slots": fetch_slots(clinic_id)})


@app.post("/api/contact-request")
async def create_contact_request(
    request: Request,
    name: str = Form(...),
    clinic_name: str = Form(...),
    phone_number: str = Form(...),
    message: str = Form(...),
    business_type: str = Form(default=""),
) -> JSONResponse:
    request_id = str(uuid4())
    created_at = datetime.now(UTC).isoformat()
    with get_db() as db:
        db.execute(
            """
            INSERT INTO contact_requests (id, name, clinic_name, phone_number, message, created_at, business_type)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (request_id, name, clinic_name, phone_number, message, created_at, business_type),
        )
        db.commit()
    apply_automation_rules(
        1,
        trigger_type="lead_created",
        payload={
            "patient_name": name,
            "phone_number": phone_number,
            "business_type": business_type,
            "source": "landing_page",
        },
    )
    log_audit("create", "contact_request", request_id, f"New demo request from {name} at {clinic_name}.")
    return JSONResponse({"message": "Demo request submitted"})


@app.post("/api/contact-requests/{request_id}/update")
async def update_contact_request(
    request: Request,
    request_id: str,
    status: str = Form(...),
    owner_notes: str = Form(default=""),
    tags: str = Form(default=""),
) -> JSONResponse:
    require_authenticated_api(request)
    with get_db() as db:
        result = db.execute(
            """
            UPDATE contact_requests
            SET status = ?, owner_notes = ?, tags = ?
            WHERE id = ?
            """,
            (status, owner_notes, tags, request_id),
        )
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Contact request not found")
    clinic_id = get_active_clinic_id(request)
    create_notification(clinic_id, "Lead updated", f"Lead stage moved to {status.replace('_', ' ')}.", "/leads")
    log_audit("update", "contact_request", request_id, f"Updated demo request status to {status}.", clinic_id=clinic_id)
    log_access_event(request, clinic_id, str(request.session.get("dentvoice_username") or ""), str(request.session.get("dentvoice_role") or ""), "lead_updated", f"{request_id}:{status}")
    return JSONResponse({"message": "Demo request updated"})


@app.post("/api/contact-requests/{request_id}/stage")
async def update_contact_request_stage(request: Request, request_id: str, status: str = Form(...)) -> JSONResponse:
    require_authenticated_api(request)
    with get_db() as db:
        result = db.execute("UPDATE contact_requests SET status = ? WHERE id = ?", (status, request_id))
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Lead not found")
    clinic_id = get_active_clinic_id(request)
    create_notification(clinic_id, "Pipeline updated", f"Lead moved to {status.replace('_', ' ')}.", "/leads")
    log_audit("update", "contact_request", request_id, f"Moved lead to {status}.", clinic_id=clinic_id)
    log_access_event(request, clinic_id, str(request.session.get("dentvoice_username") or ""), str(request.session.get("dentvoice_role") or ""), "lead_updated", f"{request_id}:{status}")
    return JSONResponse({"message": "Lead stage updated"})


@app.post("/api/faqs")
async def create_faq(
    request: Request,
    question: str = Form(...),
    answer: str = Form(...),
) -> JSONResponse:
    require_admin(request)
    clinic_id = get_active_clinic_id(request)
    with get_db() as db:
        max_order = db.execute("SELECT COALESCE(MAX(sort_order), -1) AS max_order FROM faq_entries WHERE clinic_id = ?", (clinic_id,)).fetchone()["max_order"]
        cursor = db.execute(
            """
            INSERT INTO faq_entries (question, answer, sort_order, created_at, clinic_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (question, answer, int(max_order) + 1, datetime.now(UTC).isoformat(), clinic_id),
        )
        faq_id = str(cursor.lastrowid)
        db.commit()
    log_audit("create", "faq_entry", faq_id, f"Added FAQ: {question}", clinic_id=clinic_id)
    return JSONResponse({"message": "FAQ added"})


@app.post("/api/faqs/{faq_id}/update")
async def update_faq(
    request: Request,
    faq_id: int,
    question: str = Form(...),
    answer: str = Form(...),
) -> JSONResponse:
    require_admin(request)
    clinic_id = get_active_clinic_id(request)
    with get_db() as db:
        result = db.execute(
            """
            UPDATE faq_entries
            SET question = ?, answer = ?
            WHERE id = ? AND clinic_id = ?
            """,
            (question, answer, faq_id, clinic_id),
        )
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="FAQ not found")
    log_audit("update", "faq_entry", str(faq_id), f"Updated FAQ: {question}", clinic_id=clinic_id)
    return JSONResponse({"message": "FAQ updated"})


@app.post("/api/faqs/{faq_id}/delete")
async def delete_faq(request: Request, faq_id: int) -> JSONResponse:
    require_admin(request)
    clinic_id = get_active_clinic_id(request)
    with get_db() as db:
        result = db.execute("DELETE FROM faq_entries WHERE id = ? AND clinic_id = ?", (faq_id, clinic_id))
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="FAQ not found")
    log_audit("delete", "faq_entry", str(faq_id), "Deleted FAQ entry.", clinic_id=clinic_id)
    return JSONResponse({"message": "FAQ deleted"})


@app.post("/api/simulate-call")
async def simulate_call(request: Request, payload: SimulatedCallPayload) -> JSONResponse:
    intent = classify_intent(payload.transcript)
    appointment_request = None
    clinic_id = get_active_clinic_id(request) if is_authenticated(request) else 1
    available_slots = fetch_slots(clinic_id)

    if intent == "appointment_booking":
        appointment_request = create_appointment_record(
            patient_name=payload.patient_name or "Unknown patient",
            phone_number=payload.caller_number,
            preferred_date=payload.preferred_date or available_slots[0]["date"],
            preferred_time=payload.preferred_time or available_slots[0]["time"],
            reason_for_visit=payload.reason_for_visit or "General consultation",
            source="simulated_call",
            clinic_id=clinic_id,
        )

    if intent in {"faq", "directions"}:
        send_whatsapp_confirmation(payload.caller_number, payload.patient_name, "clinic information and follow-up details", clinic_id=clinic_id)

    if intent == "emergency":
        send_whatsapp_confirmation(payload.caller_number, payload.patient_name, "urgent dental callback request", clinic_id=clinic_id)

    record = create_call_record(
        caller_number=payload.caller_number,
        patient_name=payload.patient_name,
        intent=intent,
        urgent=intent == "emergency",
        summary=create_summary(intent, payload),
        appointment_request=appointment_request,
        clinic_id=clinic_id,
    )
    return JSONResponse({"message": "Call processed successfully", "intent": intent, "call_record": record.model_dump()})


@app.post("/api/appointments")
async def create_appointment(request: Request, appointment: AppointmentRequest) -> JSONResponse:
    stored = create_appointment_record(
        patient_name=appointment.patient_name,
        phone_number=appointment.phone_number,
        preferred_date=appointment.preferred_date,
        preferred_time=appointment.preferred_time,
        reason_for_visit=appointment.reason_for_visit,
        source="api",
        status=appointment.status,
        notes=appointment.notes,
        clinic_id=get_active_clinic_id(request) if is_authenticated(request) else 1,
    )
    return JSONResponse({"message": "Appointment captured", "appointment": stored.model_dump()})


@app.post("/api/admin/appointments")
async def create_admin_appointment(
    request: Request,
    patient_name: str = Form(...),
    phone_number: str = Form(...),
    preferred_date: str = Form(...),
    preferred_time: str = Form(...),
    reason_for_visit: str = Form(...),
    status: str = Form(default="confirmed"),
    notes: str = Form(default=""),
) -> JSONResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    stored = create_appointment_record(
        patient_name=patient_name,
        phone_number=phone_number,
        preferred_date=preferred_date,
        preferred_time=preferred_time,
        reason_for_visit=reason_for_visit,
        source="admin",
        status=status,  # type: ignore[arg-type]
        notes=notes,
        clinic_id=clinic_id,
    )
    return JSONResponse({"message": "Admin appointment saved", "appointment": stored.model_dump()})


@app.post("/api/appointments/{appointment_id}/status")
async def update_appointment_status(request: Request, appointment_id: str, status: str = Form(...)) -> JSONResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    with get_db() as db:
        result = db.execute("UPDATE appointments SET status = ? WHERE id = ? AND clinic_id = ?", (status, appointment_id, clinic_id))
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Appointment not found")
    log_audit("update", "appointment", appointment_id, f"Updated appointment status to {status}.", clinic_id=clinic_id)
    return JSONResponse({"message": "Appointment status updated"})


@app.post("/api/appointments/{appointment_id}/update")
async def update_appointment(
    request: Request,
    appointment_id: str,
    patient_name: str = Form(...),
    phone_number: str = Form(...),
    preferred_date: str = Form(...),
    preferred_time: str = Form(...),
    reason_for_visit: str = Form(...),
    status: str = Form(...),
    notes: str = Form(default=""),
) -> JSONResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    check_double_booking(preferred_date, preferred_time, exclude_appointment_id=appointment_id, clinic_id=clinic_id)
    with get_db() as db:
        result = db.execute(
            """
            UPDATE appointments
            SET patient_name = ?, phone_number = ?, preferred_date = ?, preferred_time = ?, reason_for_visit = ?, status = ?, notes = ?
            WHERE id = ? AND clinic_id = ?
            """,
            (patient_name, phone_number, preferred_date, preferred_time, reason_for_visit, status, notes, appointment_id, clinic_id),
        )
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Appointment not found")
    log_audit("update", "appointment", appointment_id, f"Updated appointment for {patient_name} on {preferred_date}.", clinic_id=clinic_id)
    return JSONResponse({"message": "Appointment updated"})


@app.post("/api/appointments/{appointment_id}/delete")
async def delete_appointment(request: Request, appointment_id: str) -> JSONResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    with get_db() as db:
        result = db.execute("DELETE FROM appointments WHERE id = ? AND clinic_id = ?", (appointment_id, clinic_id))
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Appointment not found")
    log_audit("delete", "appointment", appointment_id, "Deleted appointment.", clinic_id=clinic_id)
    return JSONResponse({"message": "Appointment deleted"})


@app.post("/api/calls/{call_id}/lead-score")
async def update_call_lead_score(request: Request, call_id: str, lead_score: str = Form(...)) -> JSONResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    with get_db() as db:
        result = db.execute("UPDATE call_records SET lead_score = ? WHERE id = ? AND clinic_id = ?", (lead_score, call_id, clinic_id))
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Call not found")
    log_audit("update", "call_record", call_id, f"Updated lead score to {lead_score}.", clinic_id=clinic_id)
    return JSONResponse({"message": "Call lead score updated"})


@app.post("/api/calls/{call_id}/update")
async def update_call_record(
    request: Request,
    call_id: str,
    lead_score: str = Form(...),
    internal_notes: str = Form(default=""),
) -> JSONResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    with get_db() as db:
        result = db.execute(
            "UPDATE call_records SET lead_score = ?, internal_notes = ? WHERE id = ? AND clinic_id = ?",
            (lead_score, internal_notes, call_id, clinic_id),
        )
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Call not found")
    log_audit("update", "call_record", call_id, "Updated call notes and lead score.", clinic_id=clinic_id)
    return JSONResponse({"message": "Call record updated"})


@app.post("/api/slots")
async def create_slot(request: Request, slot: SlotInput) -> JSONResponse:
    require_admin(request)
    clinic_id = get_active_clinic_id(request)
    with get_db() as db:
        db.execute("INSERT INTO slots (slot_date, slot_time, clinic_id) VALUES (?, ?, ?)", (slot.date, slot.time, clinic_id))
        db.commit()
    log_audit("create", "slot", None, f"Added slot {slot.date} {slot.time}.", clinic_id=clinic_id)
    return JSONResponse({"message": "Slot added", "slots": fetch_slots(clinic_id)})


@app.post("/api/slots/{slot_id}/delete")
async def delete_slot(request: Request, slot_id: int) -> JSONResponse:
    require_admin(request)
    clinic_id = get_active_clinic_id(request)
    with get_db() as db:
        result = db.execute("DELETE FROM slots WHERE id = ? AND clinic_id = ?", (slot_id, clinic_id))
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Slot not found")
    log_audit("delete", "slot", str(slot_id), "Deleted slot.", clinic_id=clinic_id)
    return JSONResponse({"message": "Slot removed", "slots": fetch_slots(clinic_id)})


@app.post("/api/settings")
async def update_settings(request: Request, payload: ClinicSettingsInput) -> JSONResponse:
    require_admin(request)
    clinic_id = get_active_clinic_id(request)
    if not valid_hex_color(payload.accent_color):
        raise HTTPException(status_code=400, detail="Accent color must be a valid hex value like #146c78.")
    with get_db() as db:
        db.execute(
            """
            UPDATE clinics
            SET clinic_name = ?, clinic_timings = ?, clinic_address = ?, brand_tagline = ?, accent_color = ?, logo_text = ?, business_type = ?, avg_booking_value = ?, white_label_enabled = ?, white_label_name = ?, reseller_code = ?, working_days = ?, working_hours = ?, auto_callback_enabled = ?
            WHERE id = ?
            """,
            (
                payload.clinic_name,
                payload.clinic_timings,
                payload.clinic_address,
                payload.brand_tagline,
                payload.accent_color,
                payload.logo_text,
                payload.business_type,
                payload.avg_booking_value,
                int(payload.white_label_enabled),
                payload.white_label_name,
                payload.reseller_code,
                payload.working_days,
                payload.working_hours,
                int(payload.auto_callback_enabled),
                clinic_id,
            ),
        )
        db.commit()
    log_audit("update", "clinic_settings", str(clinic_id), "Updated clinic settings.", clinic_id=clinic_id)
    return JSONResponse({"message": "Clinic settings updated", "settings": fetch_clinic_settings(clinic_id)})


@app.post("/api/templates/apply")
async def apply_industry_template(request: Request, business_type: str = Form(...)) -> JSONResponse:
    require_admin(request)
    clinic_id = get_active_clinic_id(request)
    template = INDUSTRY_TEMPLATES.get(business_type)
    if template is None:
        raise HTTPException(status_code=404, detail="Industry template not found")
    settings = fetch_clinic_settings(clinic_id)
    with get_db() as db:
        db.execute(
            """
            UPDATE clinics
            SET business_type = ?, brand_tagline = ?, accent_color = ?, working_days = ?, working_hours = ?, clinic_timings = ?, avg_booking_value = ?
            WHERE id = ?
            """,
            (
                business_type,
                str(template["tagline"]),
                str(template["accent_color"]),
                str(template["working_days"]),
                str(template["working_hours"]),
                str(template["timings_label"]),
                int(template["avg_booking_value"]),
                clinic_id,
            ),
        )
        db.execute("DELETE FROM faq_entries WHERE clinic_id = ?", (clinic_id,))
        created_at = datetime.now(UTC).isoformat()
        for index, item in enumerate(template["faqs"]):
            db.execute(
                "INSERT INTO faq_entries (question, answer, sort_order, created_at, clinic_id) VALUES (?, ?, ?, ?, ?)",
                (item.question, item.answer, index, created_at, clinic_id),
            )
        db.commit()
    log_audit("update", "industry_template", str(clinic_id), f"Applied {business_type} template to {settings['clinic_name']}.", clinic_id=clinic_id)
    return JSONResponse({"message": "Industry template applied"})


@app.post("/api/onboarding/steps/{step_key}")
async def complete_onboarding_step(request: Request, step_key: str) -> JSONResponse:
    require_admin(request)
    clinic_id = get_active_clinic_id(request)
    mark_onboarding_step(clinic_id, step_key)
    create_notification(clinic_id, "Onboarding progress updated", f"Completed setup step: {step_key.replace('_', ' ')}.", "/setup")
    log_access_event(request, clinic_id, str(request.session.get("dentvoice_username") or ""), str(request.session.get("dentvoice_role") or ""), "onboarding_step_completed", step_key)
    return JSONResponse({"message": "Onboarding step completed"})


@app.post("/api/onboarding/preset")
async def apply_onboarding_preset(request: Request, business_type: str = Form(...)) -> JSONResponse:
    require_admin(request)
    clinic_id = get_active_clinic_id(request)
    settings = fetch_clinic_settings(clinic_id)
    available_slots = fetch_slots(clinic_id)
    if not available_slots:
        raise HTTPException(status_code=400, detail="Add at least one slot before loading a preset.")
    patient_name = f"{INDUSTRY_TEMPLATES.get(business_type, INDUSTRY_TEMPLATES['dental'])['label'].split()[0]} Demo Lead"
    appointment = None
    for slot in available_slots:
        try:
            appointment = create_appointment_record(
                patient_name=patient_name,
                phone_number="+919999900001",
                preferred_date=slot["date"],
                preferred_time=slot["time"],
                reason_for_visit="Preset demo workflow",
                source="admin",
                status="confirmed",
                notes=f"Preset demo appointment for {business_type} workspace.",
                clinic_id=clinic_id,
            )
            break
        except HTTPException:
            continue
    if appointment is None:
        raise HTTPException(status_code=409, detail="All current slots are already booked. Add a new slot first.")
    create_call_record(
        caller_number="+919999900001",
        patient_name=patient_name,
        intent="appointment_booking",
        summary=f"Preset demo call for {settings['clinic_name']} in {business_type}.",
        urgent=False,
        appointment_request=appointment,
        clinic_id=clinic_id,
    )
    mark_onboarding_step(clinic_id, "appointments")
    create_notification(clinic_id, "Industry preset loaded", f"Preset demo data added for {business_type.replace('_', ' ')}.", "/setup")
    log_access_event(request, clinic_id, str(request.session.get("dentvoice_username") or ""), str(request.session.get("dentvoice_role") or ""), "industry_preset_loaded", business_type)
    return JSONResponse({"message": "Industry preset loaded"})


@app.post("/api/announcements")
async def create_announcement(
    request: Request,
    title: str = Form(...),
    body: str = Form(...),
) -> JSONResponse:
    require_admin(request)
    clinic_id = get_active_clinic_id(request)
    with get_db() as db:
        db.execute(
            """
            INSERT INTO team_announcements (id, clinic_id, title, body, is_active, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (str(uuid4()), clinic_id, title, body, datetime.now(UTC).isoformat()),
        )
        db.commit()
    create_notification(clinic_id, "Announcement posted", title, "/notifications")
    log_access_event(request, clinic_id, str(request.session.get("dentvoice_username") or ""), str(request.session.get("dentvoice_role") or ""), "announcement_created", title)
    return JSONResponse({"message": "Announcement posted"})


@app.post("/api/automation-rules")
async def create_automation_rule(
    request: Request,
    name: str = Form(...),
    trigger_type: str = Form(...),
    condition_key: str = Form(default=""),
    condition_value: str = Form(default=""),
    action_type: str = Form(...),
    action_value: str = Form(default=""),
) -> JSONResponse:
    require_admin(request)
    clinic_id = get_active_clinic_id(request)
    with get_db() as db:
        db.execute(
            """
            INSERT INTO automation_rules (id, clinic_id, name, trigger_type, condition_key, condition_value, action_type, action_value, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (str(uuid4()), clinic_id, name, trigger_type, condition_key, condition_value, action_type, action_value, datetime.now(UTC).isoformat()),
        )
        db.commit()
    create_notification(clinic_id, "Automation rule added", name, "/setup")
    log_access_event(request, clinic_id, str(request.session.get("dentvoice_username") or ""), str(request.session.get("dentvoice_role") or ""), "automation_rule_created", name)
    return JSONResponse({"message": "Automation rule created"})


@app.post("/api/receptionist-tasks")
async def create_receptionist_task(
    request: Request,
    patient_name: str = Form(...),
    phone_number: str = Form(...),
    note: str = Form(...),
    due_date: str = Form(...),
    status: str = Form(default="open"),
    priority: str = Form(default="medium"),
    tags: str = Form(default=""),
    related_appointment_id: str = Form(default=""),
) -> JSONResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    task_id = str(uuid4())
    with get_db() as db:
        db.execute(
            """
            INSERT INTO receptionist_tasks (id, patient_name, phone_number, note, due_date, status, priority, tags, related_appointment_id, created_at, clinic_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                patient_name,
                phone_number,
                note,
                due_date,
                status,
                priority,
                tags,
                related_appointment_id or None,
                datetime.now(UTC).isoformat(),
                clinic_id,
            ),
        )
        db.commit()
    create_notification(clinic_id, "Task created", f"New follow-up task for {patient_name}.", "/inbox")
    log_access_event(request, clinic_id, str(request.session.get("dentvoice_username") or ""), str(request.session.get("dentvoice_role") or ""), "task_created", patient_name)
    log_audit("create", "receptionist_task", task_id, f"Created follow-up task for {patient_name}.", clinic_id=clinic_id)
    return JSONResponse({"message": "Receptionist task created"})


@app.post("/api/missed-leads/{call_id}/task")
async def create_missed_lead_task(
    request: Request,
    call_id: str,
    patient_name: str = Form(...),
    phone_number: str = Form(...),
    note: str = Form(...),
    due_date: str = Form(...),
    priority: str = Form(default="high"),
    tags: str = Form(default="missed_lead"),
) -> JSONResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    task_id = str(uuid4())
    with get_db() as db:
        row = db.execute("SELECT id FROM call_records WHERE id = ?", (call_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Call not found")
        db.execute(
            """
            INSERT INTO receptionist_tasks (id, patient_name, phone_number, note, due_date, status, priority, tags, related_appointment_id, created_at, clinic_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, patient_name, phone_number, note, due_date, "open", priority, tags, None, datetime.now(UTC).isoformat(), clinic_id),
        )
        db.commit()
    log_access_event(request, clinic_id, str(request.session.get("dentvoice_username") or ""), str(request.session.get("dentvoice_role") or ""), "task_created", patient_name)
    log_audit("create", "receptionist_task", task_id, f"Created missed-lead recovery task for {patient_name}.", clinic_id=clinic_id)
    return JSONResponse({"message": "Recovery task created"})


@app.post("/api/receptionist-tasks/{task_id}/update")
async def update_receptionist_task(
    request: Request,
    task_id: str,
    note: str = Form(...),
    due_date: str = Form(...),
    status: str = Form(...),
    priority: str = Form(default="medium"),
    tags: str = Form(default=""),
) -> JSONResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    with get_db() as db:
        result = db.execute(
            """
            UPDATE receptionist_tasks
            SET note = ?, due_date = ?, status = ?, priority = ?, tags = ?
            WHERE id = ? AND clinic_id = ?
            """,
            (note, due_date, status, priority, tags, task_id, clinic_id),
        )
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Receptionist task not found")
    action_name = "task_completed" if status == "done" else "task_updated"
    log_access_event(request, clinic_id, str(request.session.get("dentvoice_username") or ""), str(request.session.get("dentvoice_role") or ""), action_name, task_id)
    log_audit("update", "receptionist_task", task_id, f"Updated receptionist task to {status}.", clinic_id=clinic_id)
    return JSONResponse({"message": "Receptionist task updated"})


@app.post("/api/reminders")
async def create_reminder(
    request: Request,
    appointment_id: str = Form(...),
    patient_name: str = Form(...),
    phone_number: str = Form(...),
    reminder_type: str = Form(...),
    scheduled_for: str = Form(...),
    note: str = Form(default=""),
) -> JSONResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    reminder_id = str(uuid4())
    with get_db() as db:
        db.execute(
            """
            INSERT INTO reminder_queue (id, appointment_id, patient_name, phone_number, reminder_type, scheduled_for, status, note, created_at, clinic_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (reminder_id, appointment_id, patient_name, phone_number, reminder_type, scheduled_for, "pending", note, datetime.now(UTC).isoformat(), clinic_id),
        )
        db.commit()
    log_access_event(request, clinic_id, str(request.session.get("dentvoice_username") or ""), str(request.session.get("dentvoice_role") or ""), "reminder_created", patient_name)
    log_audit("create", "reminder", reminder_id, f"Queued {reminder_type} reminder for {patient_name}.", clinic_id=clinic_id)
    return JSONResponse({"message": "Reminder queued"})


@app.post("/api/reminders/{reminder_id}/update")
async def update_reminder(
    request: Request,
    reminder_id: str,
    status: str = Form(...),
    note: str = Form(default=""),
) -> JSONResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    with get_db() as db:
        result = db.execute(
            """
            UPDATE reminder_queue
            SET status = ?, note = ?
            WHERE id = ? AND clinic_id = ?
            """,
            (status, note, reminder_id, clinic_id),
        )
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Reminder not found")
    log_access_event(request, clinic_id, str(request.session.get("dentvoice_username") or ""), str(request.session.get("dentvoice_role") or ""), "reminder_updated", f"{reminder_id}:{status}")
    log_audit("update", "reminder", reminder_id, f"Updated reminder to {status}.", clinic_id=clinic_id)
    return JSONResponse({"message": "Reminder updated"})


@app.post("/api/team/users")
async def create_team_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    display_name: str = Form(...),
) -> JSONResponse:
    require_admin(request)
    clinic_id = get_active_clinic_id(request)
    with get_db() as db:
        db.execute(
            "INSERT INTO clinic_users (clinic_id, username, password, role, display_name, is_active) VALUES (?, ?, ?, ?, ?, 1)",
            (clinic_id, username, password, role, display_name),
        )
        db.commit()
    create_notification(clinic_id, "Team user added", f"{display_name} was added as {role}.", "/team")
    log_access_event(request, clinic_id, str(request.session.get("dentvoice_username") or ""), str(request.session.get("dentvoice_role") or ""), "team_user_created", username)
    return JSONResponse({"message": "Team user created"})


@app.post("/api/team/users/{user_id}/update")
async def update_team_user(
    request: Request,
    user_id: int,
    role: str = Form(...),
    display_name: str = Form(...),
    password: str = Form(default=""),
) -> JSONResponse:
    require_admin(request)
    clinic_id = get_active_clinic_id(request)
    with get_db() as db:
        if password:
            result = db.execute(
                "UPDATE clinic_users SET role = ?, display_name = ?, password = ? WHERE id = ? AND clinic_id = ?",
                (role, display_name, password, user_id, clinic_id),
            )
        else:
            result = db.execute(
                "UPDATE clinic_users SET role = ?, display_name = ? WHERE id = ? AND clinic_id = ?",
                (role, display_name, user_id, clinic_id),
            )
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Team user not found")
    log_access_event(request, clinic_id, str(request.session.get("dentvoice_username") or ""), str(request.session.get("dentvoice_role") or ""), "team_user_updated", str(user_id))
    return JSONResponse({"message": "Team user updated"})


@app.post("/api/team/users/{user_id}/toggle")
async def toggle_team_user(
    request: Request,
    user_id: int,
    is_active: int = Form(...),
) -> JSONResponse:
    require_admin(request)
    clinic_id = get_active_clinic_id(request)
    with get_db() as db:
        result = db.execute(
            "UPDATE clinic_users SET is_active = ? WHERE id = ? AND clinic_id = ?",
            (is_active, user_id, clinic_id),
        )
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Team user not found")
    state_label = "activated" if is_active else "deactivated"
    create_notification(clinic_id, "Team access changed", f"User account was {state_label}.", "/team")
    log_access_event(request, clinic_id, str(request.session.get("dentvoice_username") or ""), str(request.session.get("dentvoice_role") or ""), "team_user_toggled", f"{user_id}:{state_label}")
    return JSONResponse({"message": f"User {state_label}"})


@app.post("/api/password/change")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
) -> JSONResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    session_username = str(request.session.get("dentvoice_username") or "")
    username = None
    with get_db() as db:
        row = db.execute(
            "SELECT id, username, password FROM clinic_users WHERE clinic_id = ? AND username = ? LIMIT 1",
            (clinic_id, session_username),
        ).fetchone()
        if row is None or row["password"] != current_password:
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        username = row["username"]
        db.execute("UPDATE clinic_users SET password = ? WHERE id = ?", (new_password, row["id"]))
        db.commit()
    create_notification(clinic_id, "Password updated", f"Credentials changed for {username}.", "/team")
    log_access_event(request, clinic_id, username or session_username, str(request.session.get("dentvoice_role") or ""), "password_changed", username or session_username)
    return JSONResponse({"message": "Password updated"})


@app.post("/api/notifications/{notification_id}/read")
async def mark_notification_read(request: Request, notification_id: str) -> JSONResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    with get_db() as db:
        result = db.execute("UPDATE app_notifications SET is_read = 1 WHERE id = ? AND clinic_id = ?", (notification_id, clinic_id))
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Notification not found")
    return JSONResponse({"message": "Notification marked as read"})


@app.post("/api/referrals")
async def create_referral(
    request: Request,
    referrer_name: str = Form(...),
    referrer_phone: str = Form(...),
    referred_business: str = Form(...),
    status: str = Form(default="new"),
    notes: str = Form(default=""),
) -> JSONResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    referral_id = str(uuid4())
    with get_db() as db:
        db.execute(
            """
            INSERT INTO referrals (id, clinic_id, referrer_name, referrer_phone, referred_business, status, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (referral_id, clinic_id, referrer_name, referrer_phone, referred_business, status, notes, datetime.now(UTC).isoformat()),
        )
        db.commit()
    create_notification(clinic_id, "Referral added", f"Referral captured for {referred_business}.", "/hq")
    log_access_event(request, clinic_id, str(request.session.get("dentvoice_username") or ""), str(request.session.get("dentvoice_role") or ""), "referral_added", referred_business)
    return JSONResponse({"message": "Referral created"})


@app.post("/api/onboarding-emails")
async def queue_onboarding_email(
    request: Request,
    subject: str = Form(...),
    body: str = Form(...),
    status: str = Form(default="queued"),
) -> JSONResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    email_id = str(uuid4())
    with get_db() as db:
        db.execute(
            """
            INSERT INTO onboarding_emails (id, clinic_id, subject, body, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (email_id, clinic_id, subject, body, status, datetime.now(UTC).isoformat()),
        )
        db.commit()
    log_access_event(request, clinic_id, str(request.session.get("dentvoice_username") or ""), str(request.session.get("dentvoice_role") or ""), "onboarding_email_queued", subject)
    return JSONResponse({"message": "Onboarding email queued"})


@app.post("/api/comments")
async def create_comment(
    request: Request,
    entity_type: str = Form(...),
    entity_id: str = Form(...),
    body: str = Form(...),
) -> JSONResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    author_name = str(request.session.get("dentvoice_display_name") or "DentVoice User")
    with get_db() as db:
        db.execute(
            """
            INSERT INTO comments (id, clinic_id, entity_type, entity_id, author_name, body, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid4()), clinic_id, entity_type, entity_id, author_name, body, datetime.now(UTC).isoformat()),
        )
        db.commit()
    log_access_event(request, clinic_id, str(request.session.get("dentvoice_username") or ""), str(request.session.get("dentvoice_role") or ""), "comment_added", entity_type)
    return JSONResponse({"message": "Comment added"})


@app.post("/api/calendar/appointments/{appointment_id}/move")
async def move_calendar_appointment(
    request: Request,
    appointment_id: str,
    preferred_date: str = Form(...),
) -> JSONResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    with get_db() as db:
        row = db.execute("SELECT preferred_time, patient_name FROM appointments WHERE id = ? AND clinic_id = ?", (appointment_id, clinic_id)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Appointment not found")
        check_double_booking(preferred_date, row["preferred_time"], exclude_appointment_id=appointment_id, clinic_id=clinic_id)
        db.execute("UPDATE appointments SET preferred_date = ? WHERE id = ? AND clinic_id = ?", (preferred_date, appointment_id, clinic_id))
        db.commit()
    return JSONResponse({"message": "Appointment moved"})


@app.get("/api/export/appointments.csv")
async def export_appointments_csv(request: Request) -> StreamingResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    appointments = fetch_appointments(limit=1000, clinic_id=clinic_id)
    rows = [item.model_dump() for item in appointments]
    return csv_response(
        "dentvoice-appointments.csv",
        ["id", "patient_name", "phone_number", "preferred_date", "preferred_time", "reason_for_visit", "status", "source", "notes", "created_at"],
        rows,
    )


@app.get("/api/export/calls.csv")
async def export_calls_csv(request: Request) -> StreamingResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    calls = fetch_call_records(limit=1000, clinic_id=clinic_id)
    rows = [
        {
            "id": item.id,
            "caller_number": item.caller_number,
            "patient_name": item.patient_name,
            "intent": item.intent,
            "summary": item.summary,
            "urgent": item.urgent,
            "lead_score": item.lead_score,
            "internal_notes": item.internal_notes,
            "created_at": item.created_at,
        }
        for item in calls
    ]
    return csv_response(
        "dentvoice-calls.csv",
        ["id", "caller_number", "patient_name", "intent", "summary", "urgent", "lead_score", "internal_notes", "created_at"],
        rows,
    )


@app.get("/api/export/leads.csv")
async def export_leads_csv(request: Request) -> StreamingResponse:
    require_authenticated_api(request)
    leads = fetch_contact_requests(limit=1000)
    return csv_response(
        "dentvoice-leads.csv",
        ["id", "name", "clinic_name", "phone_number", "business_type", "message", "status", "owner_notes", "created_at"],
        leads,
    )


@app.get("/api/export/tasks.csv")
async def export_tasks_csv(request: Request) -> StreamingResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    tasks = fetch_receptionist_tasks(limit=1000, clinic_id=clinic_id)
    return csv_response(
        "dentvoice-tasks.csv",
        ["id", "patient_name", "phone_number", "note", "due_date", "status", "priority", "related_appointment_id", "created_at"],
        tasks,
    )


@app.get("/api/export/reminders.csv")
async def export_reminders_csv(request: Request) -> StreamingResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    reminders = fetch_reminders(limit=1000, clinic_id=clinic_id)
    return csv_response(
        "dentvoice-reminders.csv",
        ["id", "appointment_id", "patient_name", "phone_number", "reminder_type", "scheduled_for", "status", "note", "created_at"],
        reminders,
    )


@app.get("/api/export/summary.csv")
async def export_summary_csv(request: Request) -> StreamingResponse:
    require_authenticated_api(request)
    clinic_id = get_active_clinic_id(request)
    rows = build_report_summary(clinic_id)
    return csv_response("dentvoice-summary.csv", ["metric", "value"], rows)


@app.get("/api/export/benchmarks.csv")
async def export_benchmarks_csv(request: Request) -> StreamingResponse:
    require_authenticated_api(request)
    rows = build_benchmark_report()
    return csv_response(
        "dentvoice-benchmarks.csv",
        ["clinic_name", "business_type", "appointments", "calls", "conversion_rate", "estimated_revenue_recovered", "open_tasks"],
        rows,
    )


@app.post("/voice/incoming")
async def incoming_voice(
    CallSid: str = Form(default="demo-call"),  # noqa: N803
    From: str = Form(default="Unknown"),  # noqa: N803
) -> Response:
    settings = fetch_clinic_settings()
    get_or_create_session(CallSid, From)
    message = (
        f"Hello, thank you for calling {settings['clinic_name']}. "
        "Press or say 1 to book an appointment. "
        "Press or say 2 for clinic timings. "
        "Press or say 3 for location. "
        "Press or say 4 if this is a dental emergency."
    )
    xml = twiml(gather("/voice/process-main-menu", message, num_digits=1), redirect("/voice/incoming"))
    return Response(content=xml, media_type="application/xml")


@app.post("/voice/process-main-menu")
async def process_main_menu(
    CallSid: str = Form(default="demo-call"),  # noqa: N803
    From: str = Form(default="Unknown"),  # noqa: N803
    Digits: str = Form(default=""),  # noqa: N803
    SpeechResult: str = Form(default=""),  # noqa: N803
) -> Response:
    session = get_or_create_session(CallSid, From)
    settings = fetch_clinic_settings()
    selection = Digits.strip()
    speech = SpeechResult.strip().lower()

    if selection == "1" or "book" in speech or "appointment" in speech:
        session.intent = "appointment_booking"
        xml = twiml(gather("/voice/process-booking-name", "Please say your full name after the tone."), redirect("/voice/incoming"))
        return Response(content=xml, media_type="application/xml")

    if selection == "2" or "timing" in speech or "hours" in speech:
        create_call_record(caller_number=From, patient_name=None, intent="faq", summary="Caller asked for clinic timings in the live voice flow.")
        xml = twiml(
            say(f"Our clinic is open {settings['clinic_timings']}."),
            say("We will also send these details on WhatsApp. Thank you for calling."),
        )
        send_whatsapp_confirmation(From, None, f"clinic timings: {settings['clinic_timings']}")
        return Response(content=xml, media_type="application/xml")

    if selection == "3" or "location" in speech or "address" in speech:
        create_call_record(caller_number=From, patient_name=None, intent="directions", summary="Caller asked for clinic location in the live voice flow.")
        xml = twiml(
            say(f"Our clinic is located at {settings['clinic_address']}."),
            say("We will send the address on WhatsApp. Thank you for calling."),
        )
        send_whatsapp_confirmation(From, None, f"clinic address: {settings['clinic_address']}")
        return Response(content=xml, media_type="application/xml")

    if selection == "4" or "emergency" in speech or "pain" in speech or "bleeding" in speech:
        create_call_record(
            caller_number=From,
            patient_name=None,
            intent="emergency",
            urgent=True,
            summary="Urgent dental case flagged in the live voice flow for immediate follow-up.",
        )
        send_whatsapp_confirmation(From, None, "urgent dental callback request")
        xml = twiml(
            say("Your concern sounds urgent. Our team has been alerted for immediate follow-up."),
            say("If you are in severe pain or heavy bleeding, please seek emergency care right away."),
        )
        return Response(content=xml, media_type="application/xml")

    xml = twiml(say("Sorry, I did not catch that."), redirect("/voice/incoming"))
    return Response(content=xml, media_type="application/xml")


@app.post("/voice/process-booking-name")
async def process_booking_name(
    CallSid: str = Form(default="demo-call"),  # noqa: N803
    From: str = Form(default="Unknown"),  # noqa: N803
    SpeechResult: str = Form(default=""),  # noqa: N803
) -> Response:
    session = get_or_create_session(CallSid, From)
    name = SpeechResult.strip()
    if not name:
        xml = twiml(
            say("Sorry, I did not hear your name."),
            gather("/voice/process-booking-name", "Please say your full name clearly after the tone."),
        )
        return Response(content=xml, media_type="application/xml")

    session.patient_name = name
    xml = twiml(gather("/voice/process-booking-slot", slot_prompt(), num_digits=1), redirect("/voice/incoming"))
    return Response(content=xml, media_type="application/xml")


@app.post("/voice/process-booking-slot")
async def process_booking_slot(
    CallSid: str = Form(default="demo-call"),  # noqa: N803
    From: str = Form(default="Unknown"),  # noqa: N803
    Digits: str = Form(default=""),  # noqa: N803
    SpeechResult: str = Form(default=""),  # noqa: N803
) -> Response:
    session = get_or_create_session(CallSid, From)
    slot = lookup_slot(Digits.strip(), SpeechResult.strip())
    if not slot:
        xml = twiml(say("Sorry, that slot selection was not clear."), gather("/voice/process-booking-slot", slot_prompt(), num_digits=1))
        return Response(content=xml, media_type="application/xml")

    session.preferred_date = slot["date"]
    session.preferred_time = slot["time"]
    xml = twiml(gather("/voice/process-booking-reason", "Please briefly tell us the reason for your visit."), redirect("/voice/incoming"))
    return Response(content=xml, media_type="application/xml")


@app.post("/voice/process-booking-reason")
async def process_booking_reason(
    CallSid: str = Form(default="demo-call"),  # noqa: N803
    From: str = Form(default="Unknown"),  # noqa: N803
    SpeechResult: str = Form(default=""),  # noqa: N803
) -> Response:
    session = get_or_create_session(CallSid, From)
    reason = SpeechResult.strip() or "General consultation"
    session.reason_for_visit = reason
    available_slots = fetch_slots()

    appointment = create_appointment_record(
        patient_name=session.patient_name or "Unknown patient",
        phone_number=From,
        preferred_date=session.preferred_date or available_slots[0]["date"],
        preferred_time=session.preferred_time or available_slots[0]["time"],
        reason_for_visit=reason,
        source="voice_call",
    )
    create_call_record(
        caller_number=From,
        patient_name=session.patient_name,
        intent="appointment_booking",
        summary=f"Live voice booking completed for {appointment.patient_name} on {appointment.preferred_date} at {appointment.preferred_time}.",
        appointment_request=appointment,
    )
    call_sessions.pop(CallSid, None)

    xml = twiml(
        say(f"Thank you {appointment.patient_name}. Your appointment is booked for {appointment.preferred_date} at {appointment.preferred_time}."),
        say("We have sent the details on WhatsApp. We look forward to seeing you."),
    )
    return Response(content=xml, media_type="application/xml")


@app.post("/api/demo/seed")
async def seed_demo_data() -> JSONResponse:
    if fetch_call_records(limit=1) or fetch_appointments(limit=1) or fetch_messages(limit=1):
        raise HTTPException(status_code=400, detail="Demo data already exists.")

    demo_calls = [
        SimulatedCallPayload(
            caller_number="+919900000001",
            transcript="Hi, I want to book an appointment for tooth pain tomorrow morning",
            patient_name="Aarav Shah",
            preferred_date=fetch_slots()[0]["date"],
            preferred_time=fetch_slots()[0]["time"],
            reason_for_visit="Tooth pain",
        ),
        SimulatedCallPayload(
            caller_number="+919900000002",
            transcript="What time is the clinic open and where are you located?",
            patient_name="Priya Nair",
        ),
        SimulatedCallPayload(
            caller_number="+919900000003",
            transcript="This is an emergency, I have bleeding after extraction",
            patient_name="Rohan Mehta",
        ),
    ]

    for payload in demo_calls:
        intent = classify_intent(payload.transcript)
        appointment_request = None
        available_slots = fetch_slots(1)
        if intent == "appointment_booking":
            appointment_request = create_appointment_record(
                patient_name=payload.patient_name or "Unknown patient",
                phone_number=payload.caller_number,
                preferred_date=payload.preferred_date or available_slots[0]["date"],
                preferred_time=payload.preferred_time or available_slots[0]["time"],
                reason_for_visit=payload.reason_for_visit or "General consultation",
                source="simulated_call",
                clinic_id=1,
            )
        if intent in {"faq", "directions"}:
            send_whatsapp_confirmation(payload.caller_number, payload.patient_name, "clinic information and follow-up details", clinic_id=1)
        if intent == "emergency":
            send_whatsapp_confirmation(payload.caller_number, payload.patient_name, "urgent dental callback request", clinic_id=1)
        create_call_record(
            caller_number=payload.caller_number,
            patient_name=payload.patient_name,
            intent=intent,
            urgent=intent == "emergency",
            summary=create_summary(intent, payload),
            appointment_request=appointment_request,
            clinic_id=1,
        )

    return JSONResponse({"message": "Demo data loaded"})
