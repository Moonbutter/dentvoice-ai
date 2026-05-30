from __future__ import annotations

import csv
import io
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
    admin_username: str
    admin_password: str


BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / "dentvoice.db"
ASSET_VERSION = "20260530-5"

FAQS = [
    FAQAnswer(question="What are your clinic timings?", answer="We are open Monday to Saturday from 9 AM to 8 PM."),
    FAQAnswer(question="Do you offer braces and aligners?", answer="Yes, the clinic offers orthodontic consultations for braces and clear aligners."),
    FAQAnswer(question="Where is the clinic located?", answer="We are located near the main market with parking available for patients."),
    FAQAnswer(question="Is a consultation available today?", answer="Same-day consultation depends on doctor availability, and we can help request a slot."),
]

APPOINTMENT_STATUSES = ["new", "confirmed", "completed", "cancelled", "needs_follow_up"]
CALL_INTENTS = ["appointment_booking", "reschedule", "pricing", "directions", "faq", "emergency", "general"]
LEAD_SCORES = ["hot", "warm", "cold"]
TASK_STATUSES = ["open", "in_progress", "done"]
APPOINTMENT_SOURCES = ["admin", "voice_call", "simulated_call", "api"]
CONTACT_STATUSES = ["new", "contacted", "qualified", "closed"]

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


def default_slots() -> list[dict[str, str]]:
    today = datetime.now(UTC).date()
    first_day = next_business_day(today + timedelta(days=1))
    second_day = next_business_day(first_day, days_ahead=1)
    return [
        {"date": first_day.isoformat(), "time": "10:00 AM"},
        {"date": first_day.isoformat(), "time": "5:30 PM"},
        {"date": second_day.isoformat(), "time": "11:00 AM"},
    ]


def init_db() -> None:
    with get_db() as db:
        db.executescript(
            """
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
                related_appointment_id TEXT,
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
        if not column_exists(db, "contact_requests", "status"):
            db.execute("ALTER TABLE contact_requests ADD COLUMN status TEXT NOT NULL DEFAULT 'new'")
        if not column_exists(db, "contact_requests", "owner_notes"):
            db.execute("ALTER TABLE contact_requests ADD COLUMN owner_notes TEXT NOT NULL DEFAULT ''")

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

        existing_slots = db.execute("SELECT COUNT(*) AS count FROM slots").fetchone()["count"]
        if existing_slots == 0:
            for slot in default_slots():
                db.execute("INSERT INTO slots (slot_date, slot_time) VALUES (?, ?)", (slot["date"], slot["time"]))

        existing_faqs = db.execute("SELECT COUNT(*) AS count FROM faq_entries").fetchone()["count"]
        if existing_faqs == 0:
            created_at = datetime.now(UTC).isoformat()
            for index, item in enumerate(FAQS):
                db.execute(
                    "INSERT INTO faq_entries (question, answer, sort_order, created_at) VALUES (?, ?, ?, ?)",
                    (item.question, item.answer, index, created_at),
                )

        db.commit()


def reset_slots_if_outdated(db: sqlite3.Connection) -> None:
    rows = db.execute("SELECT id, slot_date FROM slots ORDER BY slot_date, slot_time").fetchall()
    if not rows:
        for slot in default_slots():
            db.execute("INSERT INTO slots (slot_date, slot_time) VALUES (?, ?)", (slot["date"], slot["time"]))
        db.commit()
        return

    latest_slot = max(datetime.fromisoformat(row["slot_date"]).date() for row in rows)
    if latest_slot < datetime.now(UTC).date():
        db.execute("DELETE FROM slots")
        for slot in default_slots():
            db.execute("INSERT INTO slots (slot_date, slot_time) VALUES (?, ?)", (slot["date"], slot["time"]))
        db.commit()


def fetch_clinic_settings() -> dict[str, str]:
    with get_db() as db:
        row = db.execute(
            """
            SELECT clinic_name, clinic_timings, clinic_address, brand_tagline, accent_color, logo_text, admin_username, admin_password
            FROM clinic_settings
            WHERE id = 1
            """
        ).fetchone()
        return dict(row)


def fetch_slots() -> list[dict[str, str]]:
    with get_db() as db:
        reset_slots_if_outdated(db)
        rows = db.execute(
            """
            SELECT id, slot_date, slot_time
            FROM slots
            ORDER BY slot_date ASC, slot_time ASC
            """
        ).fetchall()
        return [{"id": row["id"], "option": str(index + 1), "date": row["slot_date"], "time": row["slot_time"]} for index, row in enumerate(rows)]


def fetch_appointments(
    *,
    limit: int = 20,
    search: str = "",
    status: str = "",
    source: str = "",
    preferred_date: str = "",
) -> list[AppointmentRequest]:
    conditions: list[str] = []
    params: list[object] = []

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

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
        SELECT id, patient_name, phone_number, preferred_date, preferred_time, reason_for_visit, status, source, notes, created_at
        FROM appointments
        {where_clause}
        ORDER BY datetime(created_at) DESC
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
) -> list[CallRecord]:
    appointments = {item.id: item for item in fetch_appointments(limit=500)}
    conditions: list[str] = []
    params: list[object] = []

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

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
        SELECT id, caller_number, patient_name, intent, summary, urgent, lead_score, appointment_id, created_at
        FROM call_records
        {where_clause}
        ORDER BY datetime(created_at) DESC
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


def fetch_messages(limit: int = 20) -> list[WhatsAppMessage]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT phone_number, message, created_at
            FROM whatsapp_messages
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [WhatsAppMessage(**dict(row)) for row in rows]


def fetch_contact_requests(limit: int = 20, search: str = "", status: str = "") -> list[dict[str, str]]:
    conditions: list[str] = []
    params: list[object] = []

    if search:
        pattern = f"%{search}%"
        conditions.append("(name LIKE ? OR clinic_name LIKE ? OR phone_number LIKE ? OR message LIKE ? OR owner_notes LIKE ?)")
        params.extend([pattern, pattern, pattern, pattern, pattern])
    if status:
        conditions.append("status = ?")
        params.append(status)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
        SELECT id, name, clinic_name, phone_number, message, status, owner_notes, created_at
        FROM contact_requests
        {where_clause}
        ORDER BY datetime(created_at) DESC
        LIMIT ?
    """
    params.append(limit)

    with get_db() as db:
        rows = db.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def fetch_faq_entries(limit: int = 50) -> list[dict[str, object]]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, question, answer, sort_order, created_at
            FROM faq_entries
            ORDER BY sort_order ASC, id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def fetch_audit_logs(limit: int = 50) -> list[dict[str, str]]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, action, entity_type, entity_id, summary, created_at
            FROM audit_logs
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def fetch_receptionist_tasks(limit: int = 100, status: str = "") -> list[dict[str, str]]:
    conditions: list[str] = []
    params: list[object] = []
    if status:
        conditions.append("status = ?")
        params.append(status)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
        SELECT id, patient_name, phone_number, note, due_date, status, related_appointment_id, created_at
        FROM receptionist_tasks
        {where_clause}
        ORDER BY
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
        return [dict(row) for row in rows]


def fetch_patient_profiles(limit: int = 200, search: str = "") -> list[dict[str, object]]:
    appointments = fetch_appointments(limit=1000)
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

    profiles.sort(key=lambda item: str(item["latest_visit_date"]), reverse=True)
    return profiles[:limit]


def fetch_patient_detail(phone_number: str) -> dict[str, object] | None:
    appointments = [item for item in fetch_appointments(limit=1000) if item.phone_number == phone_number]
    if not appointments:
        return None

    appointments.sort(key=lambda item: item.created_at, reverse=True)
    patient_name = appointments[0].patient_name
    calls = [item for item in fetch_call_records(limit=1000) if item.caller_number == phone_number or (item.patient_name and item.patient_name == patient_name)]
    tasks = [item for item in fetch_receptionist_tasks(limit=1000) if item["phone_number"] == phone_number]
    contacts = [item for item in fetch_contact_requests(limit=1000) if item["phone_number"] == phone_number]
    latest = appointments[0]

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
        "patient_query": quote_plus(phone_number),
    }


def fetch_missed_leads(limit: int = 100, search: str = "", lead_score: str = "") -> list[dict[str, object]]:
    records = []
    for item in fetch_call_records(limit=1000, search=search, lead_score=lead_score):
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


def fetch_calendar_entries() -> list[dict[str, object]]:
    grouped: dict[str, list[AppointmentRequest]] = defaultdict(list)
    for item in fetch_appointments(limit=1000):
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


def fetch_analytics() -> dict[str, object]:
    appointments = fetch_appointments(limit=1000)
    calls = fetch_call_records(limit=1000)
    messages = fetch_messages(limit=1000)
    contacts = fetch_contact_requests(limit=1000)
    tasks = fetch_receptionist_tasks(limit=1000)

    appointments_by_status = Counter(item.status for item in appointments)
    appointments_by_source = Counter(item.source for item in appointments)
    calls_by_intent = Counter(item.intent for item in calls)
    calls_by_lead_score = Counter(item.lead_score for item in calls)
    tasks_by_status = Counter(item["status"] for item in tasks)
    contacts_by_status = Counter(item["status"] for item in contacts)

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

    return {
        "totals": {
            "appointments": len(appointments),
            "calls": len(calls),
            "messages": len(messages),
            "emergencies": sum(1 for item in calls if item.urgent),
            "contacts": len(contacts),
            "patients": len(fetch_patient_profiles(limit=1000)),
            "open_tasks": sum(1 for item in tasks if item["status"] != "done"),
            "missed_leads": len(fetch_missed_leads(limit=1000)),
        },
        "appointments_by_status": dict(appointments_by_status),
        "appointments_by_source": dict(appointments_by_source),
        "calls_by_intent": dict(calls_by_intent),
        "calls_by_lead_score": dict(calls_by_lead_score),
        "tasks_by_status": dict(tasks_by_status),
        "contacts_by_status": dict(contacts_by_status),
        "recent_days": recent_days,
        "conversion_rate": round((len(appointments) / len(calls)) * 100, 1) if calls else 0.0,
        "completion_rate": round((appointments_by_status.get("completed", 0) / len(appointments)) * 100, 1) if appointments else 0.0,
        "hot_lead_rate": round((calls_by_lead_score.get("hot", 0) / len(calls)) * 100, 1) if calls else 0.0,
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


def log_audit(action: str, entity_type: str, entity_id: str | None, summary: str) -> None:
    with get_db() as db:
        db.execute(
            """
            INSERT INTO audit_logs (action, entity_type, entity_id, summary, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (action, entity_type, entity_id, summary, datetime.now(UTC).isoformat()),
        )
        db.commit()


def is_authenticated(request: Request) -> bool:
    return bool(request.session.get("dentvoice_authenticated"))


def require_authenticated_page(request: Request) -> RedirectResponse | None:
    if is_authenticated(request):
        return None
    return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)


def require_authenticated_api(request: Request) -> None:
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Please log in to continue.")


def valid_hex_color(value: str) -> bool:
    if len(value) != 7 or not value.startswith("#"):
        return False
    return all(character in "0123456789abcdefABCDEF" for character in value[1:])


def normalize_branding(settings: dict[str, str]) -> dict[str, str]:
    accent = settings.get("accent_color", "#146c78")
    if not valid_hex_color(accent):
        accent = "#146c78"
    return {
        "logo_text": (settings.get("logo_text") or "DV")[:4],
        "brand_tagline": settings.get("brand_tagline") or "AI receptionist for dental clinics",
        "accent_color": accent,
    }


def check_double_booking(preferred_date: str, preferred_time: str, *, exclude_appointment_id: str | None = None) -> None:
    with get_db() as db:
        query = """
            SELECT id
            FROM appointments
            WHERE preferred_date = ? AND preferred_time = ? AND status != 'cancelled'
        """
        params: list[object] = [preferred_date, preferred_time]
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


def lookup_slot(option: str | None, speech_result: str | None = None) -> dict[str, str] | None:
    available_slots = fetch_slots()
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


def send_whatsapp_confirmation(phone_number: str, patient_name: str | None, details: str) -> WhatsAppMessage:
    clinic_name = fetch_clinic_settings()["clinic_name"]
    item = WhatsAppMessage(
        phone_number=phone_number,
        message=(
            f"Hello {patient_name or 'there'}, thanks for contacting {clinic_name}. "
            f"We have received your request: {details}. Our team will confirm shortly."
        ),
    )
    with get_db() as db:
        db.execute(
            "INSERT INTO whatsapp_messages (phone_number, message, created_at) VALUES (?, ?, ?)",
            (item.phone_number, item.message, item.created_at),
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
) -> AppointmentRequest:
    check_double_booking(preferred_date, preferred_time)
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
                id, patient_name, phone_number, preferred_date, preferred_time, reason_for_visit, status, source, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        db.commit()
    send_whatsapp_confirmation(phone_number, patient_name, f"appointment booked for {preferred_date} at {preferred_time}")
    log_audit("create", "appointment", appointment.id, f"Created appointment for {patient_name} on {preferred_date} at {preferred_time}.")
    return appointment


def create_call_record(
    *,
    caller_number: str,
    patient_name: str | None,
    intent: str,
    summary: str,
    urgent: bool = False,
    appointment_request: AppointmentRequest | None = None,
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
                id, caller_number, patient_name, intent, summary, urgent, lead_score, appointment_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.caller_number,
                record.patient_name,
                record.intent,
                record.summary,
                int(record.urgent),
                record.lead_score,
                appointment_request.id if appointment_request else None,
                record.created_at,
            ),
        )
        db.commit()
    log_audit("create", "call_record", record.id, f"Logged {intent} call from {caller_number}.")
    return record


def get_or_create_session(call_sid: str, caller_number: str) -> CallSession:
    if call_sid not in call_sessions:
        call_sessions[call_sid] = CallSession(call_sid=call_sid, caller_number=caller_number)
    return call_sessions[call_sid]


def slot_prompt() -> str:
    available_slots = fetch_slots()
    prompts = []
    for slot in available_slots[:3]:
        prompts.append(f"Press or say {slot['option']} for {slot['date']} at {slot['time'].replace(':', ' ')}")
    return "Please choose a slot. " + ". ".join(prompts) + "."


def build_dashboard_context() -> dict[str, object]:
    analytics = fetch_analytics()
    settings = fetch_clinic_settings()
    branding = normalize_branding(settings)
    return {
        "stats": analytics["totals"],
        "appointments": fetch_appointments(limit=10),
        "call_records": fetch_call_records(limit=10),
        "messages": fetch_messages(limit=10),
        "contact_requests": fetch_contact_requests(limit=5),
        "missed_leads": fetch_missed_leads(limit=5),
        "audit_logs": fetch_audit_logs(limit=8),
        "receptionist_tasks": fetch_receptionist_tasks(limit=8),
        "calendar_entries": fetch_calendar_entries()[:5],
        "patient_profiles": fetch_patient_profiles(limit=5),
        "faqs": fetch_faq_entries(limit=8),
        "slots": fetch_slots(),
        "settings": settings,
        "branding": branding,
        "analytics": analytics,
        "analytics_charts": {
            "appointments_by_status": build_chart(analytics["appointments_by_status"], APPOINTMENT_STATUSES),
            "appointments_by_source": build_chart(analytics["appointments_by_source"], APPOINTMENT_SOURCES),
            "calls_by_intent": build_chart(analytics["calls_by_intent"], CALL_INTENTS),
            "calls_by_lead_score": build_chart(analytics["calls_by_lead_score"], LEAD_SCORES),
            "tasks_by_status": build_chart(analytics["tasks_by_status"], TASK_STATUSES),
            "contacts_by_status": build_chart(analytics["contacts_by_status"], CONTACT_STATUSES),
            "recent_days": build_trend_chart(analytics["recent_days"]),
        },
        "statuses": APPOINTMENT_STATUSES,
        "lead_scores": LEAD_SCORES,
        "task_statuses": TASK_STATUSES,
        "contact_statuses": CONTACT_STATUSES,
        "default_admin_username": settings.get("admin_username", "admin"),
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
    context = build_dashboard_context()
    context.update({"is_authenticated": is_authenticated(request)})
    return templates.TemplateResponse(request, "landing.html", context)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = Query(default="/dashboard"), error: str = Query(default="")) -> HTMLResponse:
    if is_authenticated(request):
        return RedirectResponse(url=next or "/dashboard", status_code=303)
    context = build_dashboard_context()
    context.update({"next_url": next, "login_error": error})
    return templates.TemplateResponse(request, "login.html", context)


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next_url: str = Form(default="/dashboard"),
) -> Response:
    settings = fetch_clinic_settings()
    if username == settings["admin_username"] and password == settings["admin_password"]:
        request.session["dentvoice_authenticated"] = True
        return RedirectResponse(url=next_url or "/dashboard", status_code=303)
    return RedirectResponse(url=f"/login?next={next_url or '/dashboard'}&error=Invalid credentials", status_code=303)


@app.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context()
    context.update({"is_authenticated": True})
    return templates.TemplateResponse(request, "dashboard.html", context)


@app.get("/appointments", response_class=HTMLResponse)
async def appointments_page(
    request: Request,
    q: str = Query(default=""),
    status: str = Query(default=""),
    source: str = Query(default=""),
    preferred_date: str = Query(default=""),
) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context()
    context.update(
        {
            "page_title": "Appointments",
            "appointments": fetch_appointments(limit=200, search=q, status=status, source=source, preferred_date=preferred_date),
            "filters": {"q": q, "status": status, "source": source, "preferred_date": preferred_date},
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
) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context()
    context.update(
        {
            "page_title": "Calls",
            "call_records": fetch_call_records(limit=200, search=q, intent=intent, urgent_only=urgent_only, lead_score=lead_score),
            "filters": {"q": q, "intent": intent, "urgent_only": urgent_only, "lead_score": lead_score},
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
    context = build_dashboard_context()
    context.update({"page_title": "Analytics", "is_authenticated": True})
    return templates.TemplateResponse(request, "analytics.html", context)


@app.get("/docs", response_class=HTMLResponse)
async def docs_page(request: Request) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context()
    context.update({"page_title": "Docs", "is_authenticated": True})
    return templates.TemplateResponse(request, "docs.html", context)


@app.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context()
    context.update({"page_title": "Calendar", "calendar_entries": fetch_calendar_entries(), "is_authenticated": True})
    return templates.TemplateResponse(request, "calendar.html", context)


@app.get("/patients", response_class=HTMLResponse)
async def patients_page(request: Request, q: str = Query(default="")) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context()
    context.update({"page_title": "Patients", "patient_profiles": fetch_patient_profiles(limit=300, search=q), "filters": {"q": q}, "is_authenticated": True})
    return templates.TemplateResponse(request, "patients.html", context)


@app.get("/patients/detail", response_class=HTMLResponse)
async def patient_detail_page(request: Request, phone: str = Query(...)) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    detail = fetch_patient_detail(phone)
    if detail is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    context = build_dashboard_context()
    context.update({"page_title": "Patient Detail", "patient_detail": detail, "is_authenticated": True})
    return templates.TemplateResponse(request, "patient_detail.html", context)


@app.get("/leads", response_class=HTMLResponse)
async def leads_page(
    request: Request,
    q: str = Query(default=""),
    status: str = Query(default=""),
) -> HTMLResponse:
    redirect_response = require_authenticated_page(request)
    if redirect_response:
        return redirect_response
    context = build_dashboard_context()
    context.update(
        {
            "page_title": "Demo Request CRM",
            "contact_requests": fetch_contact_requests(limit=200, search=q, status=status),
            "filters": {"q": q, "status": status},
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
    context = build_dashboard_context()
    context.update(
        {
            "page_title": "Missed Lead Recovery",
            "missed_leads": fetch_missed_leads(limit=200, search=q, lead_score=lead_score),
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
    context = build_dashboard_context()
    context.update({"page_title": "Audit Log", "audit_logs": fetch_audit_logs(limit=200), "is_authenticated": True})
    return templates.TemplateResponse(request, "audit.html", context)


@app.get("/health")
async def healthcheck() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/api/dashboard")
async def dashboard_data(request: Request) -> JSONResponse:
    require_authenticated_api(request)
    context = build_dashboard_context()
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


@app.get("/api/available-slots")
async def available_slots() -> JSONResponse:
    return JSONResponse({"slots": fetch_slots()})


@app.post("/api/contact-request")
async def create_contact_request(
    name: str = Form(...),
    clinic_name: str = Form(...),
    phone_number: str = Form(...),
    message: str = Form(...),
) -> JSONResponse:
    request_id = str(uuid4())
    created_at = datetime.now(UTC).isoformat()
    with get_db() as db:
        db.execute(
            """
            INSERT INTO contact_requests (id, name, clinic_name, phone_number, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (request_id, name, clinic_name, phone_number, message, created_at),
        )
        db.commit()
    log_audit("create", "contact_request", request_id, f"New demo request from {name} at {clinic_name}.")
    return JSONResponse({"message": "Demo request submitted"})


@app.post("/api/contact-requests/{request_id}/update")
async def update_contact_request(
    request: Request,
    request_id: str,
    status: str = Form(...),
    owner_notes: str = Form(default=""),
) -> JSONResponse:
    require_authenticated_api(request)
    with get_db() as db:
        result = db.execute(
            """
            UPDATE contact_requests
            SET status = ?, owner_notes = ?
            WHERE id = ?
            """,
            (status, owner_notes, request_id),
        )
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Contact request not found")
    log_audit("update", "contact_request", request_id, f"Updated demo request status to {status}.")
    return JSONResponse({"message": "Demo request updated"})


@app.post("/api/faqs")
async def create_faq(
    request: Request,
    question: str = Form(...),
    answer: str = Form(...),
) -> JSONResponse:
    require_authenticated_api(request)
    with get_db() as db:
        max_order = db.execute("SELECT COALESCE(MAX(sort_order), -1) AS max_order FROM faq_entries").fetchone()["max_order"]
        cursor = db.execute(
            """
            INSERT INTO faq_entries (question, answer, sort_order, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (question, answer, int(max_order) + 1, datetime.now(UTC).isoformat()),
        )
        faq_id = str(cursor.lastrowid)
        db.commit()
    log_audit("create", "faq_entry", faq_id, f"Added FAQ: {question}")
    return JSONResponse({"message": "FAQ added"})


@app.post("/api/faqs/{faq_id}/update")
async def update_faq(
    request: Request,
    faq_id: int,
    question: str = Form(...),
    answer: str = Form(...),
) -> JSONResponse:
    require_authenticated_api(request)
    with get_db() as db:
        result = db.execute(
            """
            UPDATE faq_entries
            SET question = ?, answer = ?
            WHERE id = ?
            """,
            (question, answer, faq_id),
        )
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="FAQ not found")
    log_audit("update", "faq_entry", str(faq_id), f"Updated FAQ: {question}")
    return JSONResponse({"message": "FAQ updated"})


@app.post("/api/faqs/{faq_id}/delete")
async def delete_faq(request: Request, faq_id: int) -> JSONResponse:
    require_authenticated_api(request)
    with get_db() as db:
        result = db.execute("DELETE FROM faq_entries WHERE id = ?", (faq_id,))
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="FAQ not found")
    log_audit("delete", "faq_entry", str(faq_id), "Deleted FAQ entry.")
    return JSONResponse({"message": "FAQ deleted"})


@app.post("/api/simulate-call")
async def simulate_call(payload: SimulatedCallPayload) -> JSONResponse:
    intent = classify_intent(payload.transcript)
    appointment_request = None
    available_slots = fetch_slots()

    if intent == "appointment_booking":
        appointment_request = create_appointment_record(
            patient_name=payload.patient_name or "Unknown patient",
            phone_number=payload.caller_number,
            preferred_date=payload.preferred_date or available_slots[0]["date"],
            preferred_time=payload.preferred_time or available_slots[0]["time"],
            reason_for_visit=payload.reason_for_visit or "General consultation",
            source="simulated_call",
        )

    if intent in {"faq", "directions"}:
        send_whatsapp_confirmation(payload.caller_number, payload.patient_name, "clinic information and follow-up details")

    if intent == "emergency":
        send_whatsapp_confirmation(payload.caller_number, payload.patient_name, "urgent dental callback request")

    record = create_call_record(
        caller_number=payload.caller_number,
        patient_name=payload.patient_name,
        intent=intent,
        urgent=intent == "emergency",
        summary=create_summary(intent, payload),
        appointment_request=appointment_request,
    )
    return JSONResponse({"message": "Call processed successfully", "intent": intent, "call_record": record.model_dump()})


@app.post("/api/appointments")
async def create_appointment(appointment: AppointmentRequest) -> JSONResponse:
    stored = create_appointment_record(
        patient_name=appointment.patient_name,
        phone_number=appointment.phone_number,
        preferred_date=appointment.preferred_date,
        preferred_time=appointment.preferred_time,
        reason_for_visit=appointment.reason_for_visit,
        source="api",
        status=appointment.status,
        notes=appointment.notes,
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
    stored = create_appointment_record(
        patient_name=patient_name,
        phone_number=phone_number,
        preferred_date=preferred_date,
        preferred_time=preferred_time,
        reason_for_visit=reason_for_visit,
        source="admin",
        status=status,  # type: ignore[arg-type]
        notes=notes,
    )
    return JSONResponse({"message": "Admin appointment saved", "appointment": stored.model_dump()})


@app.post("/api/appointments/{appointment_id}/status")
async def update_appointment_status(request: Request, appointment_id: str, status: str = Form(...)) -> JSONResponse:
    require_authenticated_api(request)
    with get_db() as db:
        result = db.execute("UPDATE appointments SET status = ? WHERE id = ?", (status, appointment_id))
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Appointment not found")
    log_audit("update", "appointment", appointment_id, f"Updated appointment status to {status}.")
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
    check_double_booking(preferred_date, preferred_time, exclude_appointment_id=appointment_id)
    with get_db() as db:
        result = db.execute(
            """
            UPDATE appointments
            SET patient_name = ?, phone_number = ?, preferred_date = ?, preferred_time = ?, reason_for_visit = ?, status = ?, notes = ?
            WHERE id = ?
            """,
            (patient_name, phone_number, preferred_date, preferred_time, reason_for_visit, status, notes, appointment_id),
        )
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Appointment not found")
    log_audit("update", "appointment", appointment_id, f"Updated appointment for {patient_name} on {preferred_date}.")
    return JSONResponse({"message": "Appointment updated"})


@app.post("/api/appointments/{appointment_id}/delete")
async def delete_appointment(request: Request, appointment_id: str) -> JSONResponse:
    require_authenticated_api(request)
    with get_db() as db:
        result = db.execute("DELETE FROM appointments WHERE id = ?", (appointment_id,))
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Appointment not found")
    log_audit("delete", "appointment", appointment_id, "Deleted appointment.")
    return JSONResponse({"message": "Appointment deleted"})


@app.post("/api/calls/{call_id}/lead-score")
async def update_call_lead_score(request: Request, call_id: str, lead_score: str = Form(...)) -> JSONResponse:
    require_authenticated_api(request)
    with get_db() as db:
        result = db.execute("UPDATE call_records SET lead_score = ? WHERE id = ?", (lead_score, call_id))
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Call not found")
    log_audit("update", "call_record", call_id, f"Updated lead score to {lead_score}.")
    return JSONResponse({"message": "Call lead score updated"})


@app.post("/api/slots")
async def create_slot(request: Request, slot: SlotInput) -> JSONResponse:
    require_authenticated_api(request)
    with get_db() as db:
        db.execute("INSERT INTO slots (slot_date, slot_time) VALUES (?, ?)", (slot.date, slot.time))
        db.commit()
    log_audit("create", "slot", None, f"Added slot {slot.date} {slot.time}.")
    return JSONResponse({"message": "Slot added", "slots": fetch_slots()})


@app.post("/api/slots/{slot_id}/delete")
async def delete_slot(request: Request, slot_id: int) -> JSONResponse:
    require_authenticated_api(request)
    with get_db() as db:
        result = db.execute("DELETE FROM slots WHERE id = ?", (slot_id,))
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Slot not found")
    log_audit("delete", "slot", str(slot_id), "Deleted slot.")
    return JSONResponse({"message": "Slot removed", "slots": fetch_slots()})


@app.post("/api/settings")
async def update_settings(request: Request, payload: ClinicSettingsInput) -> JSONResponse:
    require_authenticated_api(request)
    if not valid_hex_color(payload.accent_color):
        raise HTTPException(status_code=400, detail="Accent color must be a valid hex value like #146c78.")
    with get_db() as db:
        db.execute(
            """
            UPDATE clinic_settings
            SET clinic_name = ?, clinic_timings = ?, clinic_address = ?, brand_tagline = ?, accent_color = ?, logo_text = ?, admin_username = ?, admin_password = ?
            WHERE id = 1
            """,
            (
                payload.clinic_name,
                payload.clinic_timings,
                payload.clinic_address,
                payload.brand_tagline,
                payload.accent_color,
                payload.logo_text,
                payload.admin_username,
                payload.admin_password,
            ),
        )
        db.commit()
    log_audit("update", "clinic_settings", "1", "Updated clinic settings.")
    return JSONResponse({"message": "Clinic settings updated", "settings": fetch_clinic_settings()})


@app.post("/api/receptionist-tasks")
async def create_receptionist_task(
    request: Request,
    patient_name: str = Form(...),
    phone_number: str = Form(...),
    note: str = Form(...),
    due_date: str = Form(...),
    status: str = Form(default="open"),
    related_appointment_id: str = Form(default=""),
) -> JSONResponse:
    require_authenticated_api(request)
    task_id = str(uuid4())
    with get_db() as db:
        db.execute(
            """
            INSERT INTO receptionist_tasks (id, patient_name, phone_number, note, due_date, status, related_appointment_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                patient_name,
                phone_number,
                note,
                due_date,
                status,
                related_appointment_id or None,
                datetime.now(UTC).isoformat(),
            ),
        )
        db.commit()
    log_audit("create", "receptionist_task", task_id, f"Created follow-up task for {patient_name}.")
    return JSONResponse({"message": "Receptionist task created"})


@app.post("/api/missed-leads/{call_id}/task")
async def create_missed_lead_task(
    request: Request,
    call_id: str,
    patient_name: str = Form(...),
    phone_number: str = Form(...),
    note: str = Form(...),
    due_date: str = Form(...),
) -> JSONResponse:
    require_authenticated_api(request)
    task_id = str(uuid4())
    with get_db() as db:
        row = db.execute("SELECT id FROM call_records WHERE id = ?", (call_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Call not found")
        db.execute(
            """
            INSERT INTO receptionist_tasks (id, patient_name, phone_number, note, due_date, status, related_appointment_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, patient_name, phone_number, note, due_date, "open", None, datetime.now(UTC).isoformat()),
        )
        db.commit()
    log_audit("create", "receptionist_task", task_id, f"Created missed-lead recovery task for {patient_name}.")
    return JSONResponse({"message": "Recovery task created"})


@app.post("/api/receptionist-tasks/{task_id}/update")
async def update_receptionist_task(
    request: Request,
    task_id: str,
    note: str = Form(...),
    due_date: str = Form(...),
    status: str = Form(...),
) -> JSONResponse:
    require_authenticated_api(request)
    with get_db() as db:
        result = db.execute(
            """
            UPDATE receptionist_tasks
            SET note = ?, due_date = ?, status = ?
            WHERE id = ?
            """,
            (note, due_date, status, task_id),
        )
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Receptionist task not found")
    log_audit("update", "receptionist_task", task_id, f"Updated receptionist task to {status}.")
    return JSONResponse({"message": "Receptionist task updated"})


@app.get("/api/export/appointments.csv")
async def export_appointments_csv(request: Request) -> StreamingResponse:
    require_authenticated_api(request)
    appointments = fetch_appointments(limit=1000)
    rows = [item.model_dump() for item in appointments]
    return csv_response(
        "dentvoice-appointments.csv",
        ["id", "patient_name", "phone_number", "preferred_date", "preferred_time", "reason_for_visit", "status", "source", "notes", "created_at"],
        rows,
    )


@app.get("/api/export/calls.csv")
async def export_calls_csv(request: Request) -> StreamingResponse:
    require_authenticated_api(request)
    calls = fetch_call_records(limit=1000)
    rows = [
        {
            "id": item.id,
            "caller_number": item.caller_number,
            "patient_name": item.patient_name,
            "intent": item.intent,
            "summary": item.summary,
            "urgent": item.urgent,
            "lead_score": item.lead_score,
            "created_at": item.created_at,
        }
        for item in calls
    ]
    return csv_response(
        "dentvoice-calls.csv",
        ["id", "caller_number", "patient_name", "intent", "summary", "urgent", "lead_score", "created_at"],
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
        await simulate_call(payload)

    return JSONResponse({"message": "Demo data loaded"})
