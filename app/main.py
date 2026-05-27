from __future__ import annotations

import csv
import io
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field


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


BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / "dentvoice.db"

FAQS = [
    FAQAnswer(question="What are your clinic timings?", answer="We are open Monday to Saturday from 9 AM to 8 PM."),
    FAQAnswer(question="Do you offer braces and aligners?", answer="Yes, the clinic offers orthodontic consultations for braces and clear aligners."),
    FAQAnswer(question="Where is the clinic located?", answer="We are located near the main market with parking available for patients."),
    FAQAnswer(question="Is a consultation available today?", answer="Same-day consultation depends on doctor availability, and we can help request a slot."),
]

call_sessions: dict[str, CallSession] = {}

app = FastAPI(title="DentVoice AI MVP")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def get_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


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
            """
        )
        existing_settings = db.execute("SELECT COUNT(*) AS count FROM clinic_settings").fetchone()["count"]
        if existing_settings == 0:
            db.execute(
                """
                INSERT INTO clinic_settings (id, clinic_name, clinic_timings, clinic_address)
                VALUES (1, ?, ?, ?)
                """,
                ("Smile Dental Clinic", "Monday to Saturday, 9 AM to 8 PM", "Near the main market with parking available"),
            )
        existing_slots = db.execute("SELECT COUNT(*) AS count FROM slots").fetchone()["count"]
        if existing_slots == 0:
            for slot in default_slots():
                db.execute("INSERT INTO slots (slot_date, slot_time) VALUES (?, ?)", (slot["date"], slot["time"]))
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
        row = db.execute("SELECT clinic_name, clinic_timings, clinic_address FROM clinic_settings WHERE id = 1").fetchone()
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
        return [
            {
                "id": row["id"],
                "option": str(index + 1),
                "date": row["slot_date"],
                "time": row["slot_time"],
            }
            for index, row in enumerate(rows)
        ]


def fetch_appointments(limit: int = 20) -> list[AppointmentRequest]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, patient_name, phone_number, preferred_date, preferred_time, reason_for_visit, status, source, created_at
            FROM appointments
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [AppointmentRequest(**dict(row)) for row in rows]


def fetch_call_records(limit: int = 20) -> list[CallRecord]:
    appointments = {item.id: item for item in fetch_appointments(limit=100)}
    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, caller_number, patient_name, intent, summary, urgent, appointment_id, created_at
            FROM call_records
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    records: list[CallRecord] = []
    for row in rows:
        data = dict(row)
        appointment_id = data.pop("appointment_id")
        data["urgent"] = bool(data["urgent"])
        records.append(
            CallRecord(
                **data,
                appointment_request=appointments.get(appointment_id),
            )
        )
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
) -> AppointmentRequest:
    appointment = AppointmentRequest(
        patient_name=patient_name,
        phone_number=phone_number,
        preferred_date=preferred_date,
        preferred_time=preferred_time,
        reason_for_visit=reason_for_visit,
        source=source,
        status=status,
    )
    with get_db() as db:
        db.execute(
            """
            INSERT INTO appointments (
                id, patient_name, phone_number, preferred_date, preferred_time, reason_for_visit, status, source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                appointment.created_at,
            ),
        )
        db.commit()
    send_whatsapp_confirmation(
        phone_number,
        patient_name,
        f"appointment booked for {preferred_date} at {preferred_time}",
    )
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
        appointment_request=appointment_request,
    )
    with get_db() as db:
        db.execute(
            """
            INSERT INTO call_records (
                id, caller_number, patient_name, intent, summary, urgent, appointment_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.caller_number,
                record.patient_name,
                record.intent,
                record.summary,
                int(record.urgent),
                appointment_request.id if appointment_request else None,
                record.created_at,
            ),
        )
        db.commit()
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
    appointments = fetch_appointments(limit=10)
    call_records = fetch_call_records(limit=10)
    messages = fetch_messages(limit=10)
    slots = fetch_slots()
    settings = fetch_clinic_settings()
    return {
        "stats": {
            "appointments": len(fetch_appointments(limit=500)),
            "calls": len(fetch_call_records(limit=500)),
            "emergencies": sum(1 for call in fetch_call_records(limit=500) if call.urgent),
            "messages": len(fetch_messages(limit=500)),
        },
        "appointments": appointments,
        "call_records": call_records,
        "messages": messages,
        "faqs": FAQS,
        "slots": slots,
        "settings": settings,
        "statuses": ["new", "confirmed", "completed", "cancelled", "needs_follow_up"],
    }


init_db()


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    context = build_dashboard_context()
    return templates.TemplateResponse(request, "dashboard.html", context)


@app.get("/health")
async def healthcheck() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/api/dashboard")
async def dashboard_data() -> JSONResponse:
    context = build_dashboard_context()
    return JSONResponse(
        {
            "stats": context["stats"],
            "appointments": [item.model_dump() for item in context["appointments"]],
            "calls": [item.model_dump() for item in context["call_records"]],
            "messages": [item.model_dump() for item in context["messages"]],
            "slots": context["slots"],
            "settings": context["settings"],
        }
    )


@app.get("/api/available-slots")
async def available_slots() -> JSONResponse:
    return JSONResponse({"slots": fetch_slots()})


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
    )
    return JSONResponse({"message": "Appointment captured", "appointment": stored.model_dump()})


@app.post("/api/admin/appointments")
async def create_admin_appointment(
    patient_name: str = Form(...),
    phone_number: str = Form(...),
    preferred_date: str = Form(...),
    preferred_time: str = Form(...),
    reason_for_visit: str = Form(...),
    status: str = Form(default="confirmed"),
) -> JSONResponse:
    stored = create_appointment_record(
        patient_name=patient_name,
        phone_number=phone_number,
        preferred_date=preferred_date,
        preferred_time=preferred_time,
        reason_for_visit=reason_for_visit,
        source="admin",
        status=status,  # type: ignore[arg-type]
    )
    return JSONResponse({"message": "Admin appointment saved", "appointment": stored.model_dump()})


@app.post("/api/appointments/{appointment_id}/status")
async def update_appointment_status(appointment_id: str, status: str = Form(...)) -> JSONResponse:
    with get_db() as db:
        result = db.execute("UPDATE appointments SET status = ? WHERE id = ?", (status, appointment_id))
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Appointment not found")
    return JSONResponse({"message": "Appointment status updated"})


@app.post("/api/slots")
async def create_slot(slot: SlotInput) -> JSONResponse:
    with get_db() as db:
        db.execute("INSERT INTO slots (slot_date, slot_time) VALUES (?, ?)", (slot.date, slot.time))
        db.commit()
    return JSONResponse({"message": "Slot added", "slots": fetch_slots()})


@app.post("/api/slots/{slot_id}/delete")
async def delete_slot(slot_id: int) -> JSONResponse:
    with get_db() as db:
        result = db.execute("DELETE FROM slots WHERE id = ?", (slot_id,))
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Slot not found")
    return JSONResponse({"message": "Slot removed", "slots": fetch_slots()})


@app.post("/api/settings")
async def update_settings(payload: ClinicSettingsInput) -> JSONResponse:
    with get_db() as db:
        db.execute(
            """
            UPDATE clinic_settings
            SET clinic_name = ?, clinic_timings = ?, clinic_address = ?
            WHERE id = 1
            """,
            (payload.clinic_name, payload.clinic_timings, payload.clinic_address),
        )
        db.commit()
    return JSONResponse({"message": "Clinic settings updated", "settings": fetch_clinic_settings()})


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


@app.get("/api/export/appointments.csv")
async def export_appointments_csv() -> StreamingResponse:
    appointments = fetch_appointments(limit=1000)
    rows = [item.model_dump() for item in appointments]
    return csv_response(
        "dentvoice-appointments.csv",
        ["id", "patient_name", "phone_number", "preferred_date", "preferred_time", "reason_for_visit", "status", "source", "created_at"],
        rows,
    )


@app.get("/api/export/calls.csv")
async def export_calls_csv() -> StreamingResponse:
    calls = fetch_call_records(limit=1000)
    rows = [
        {
            "id": item.id,
            "caller_number": item.caller_number,
            "patient_name": item.patient_name,
            "intent": item.intent,
            "summary": item.summary,
            "urgent": item.urgent,
            "created_at": item.created_at,
        }
        for item in calls
    ]
    return csv_response(
        "dentvoice-calls.csv",
        ["id", "caller_number", "patient_name", "intent", "summary", "urgent", "created_at"],
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
        create_call_record(
            caller_number=From,
            patient_name=None,
            intent="faq",
            summary="Caller asked for clinic timings in the live voice flow.",
        )
        xml = twiml(
            say(f"Our clinic is open {settings['clinic_timings']}."),
            say("We will also send these details on WhatsApp. Thank you for calling."),
        )
        send_whatsapp_confirmation(From, None, f"clinic timings: {settings['clinic_timings']}")
        return Response(content=xml, media_type="application/xml")

    if selection == "3" or "location" in speech or "address" in speech:
        create_call_record(
            caller_number=From,
            patient_name=None,
            intent="directions",
            summary="Caller asked for clinic location in the live voice flow.",
        )
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
        summary=(
            f"Live voice booking completed for {appointment.patient_name} on "
            f"{appointment.preferred_date} at {appointment.preferred_time}."
        ),
        appointment_request=appointment,
    )
    call_sessions.pop(CallSid, None)

    xml = twiml(
        say(
            f"Thank you {appointment.patient_name}. Your appointment is booked for "
            f"{appointment.preferred_date} at {appointment.preferred_time}."
        ),
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
