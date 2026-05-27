from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
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
    status: Literal["new", "confirmed", "needs_follow_up"] = "confirmed"
    source: Literal["api", "simulated_call", "voice_call"] = "api"
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


BASE_DIR = Path(__file__).resolve().parent.parent


FAQS = [
    FAQAnswer(question="What are your clinic timings?", answer="We are open Monday to Saturday from 9 AM to 8 PM."),
    FAQAnswer(question="Do you offer braces and aligners?", answer="Yes, the clinic offers orthodontic consultations for braces and clear aligners."),
    FAQAnswer(question="Where is the clinic located?", answer="We are located near the main market with parking available for patients."),
    FAQAnswer(question="Is a consultation available today?", answer="Same-day consultation depends on doctor availability, and we can help request a slot."),
]

appointments: list[AppointmentRequest] = []
call_records: list[CallRecord] = []
whatsapp_messages: list[WhatsAppMessage] = []
call_sessions: dict[str, CallSession] = {}

app = FastAPI(title="DentVoice AI MVP")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def next_business_day(start: date, *, days_ahead: int = 0) -> date:
    current = start
    if days_ahead:
        current += timedelta(days=days_ahead)
    while current.weekday() == 6:
        current += timedelta(days=1)
    return current


def build_available_slots() -> list[dict[str, str]]:
    today = datetime.now(UTC).date()
    first_day = next_business_day(today + timedelta(days=1))
    second_day = next_business_day(first_day, days_ahead=1)
    return [
        {"option": "1", "date": first_day.isoformat(), "time": "10:00 AM"},
        {"option": "2", "date": first_day.isoformat(), "time": "5:30 PM"},
        {"option": "3", "date": second_day.isoformat(), "time": "11:00 AM"},
    ]


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
    available_slots = build_available_slots()
    if option:
        for slot in available_slots:
            if slot["option"] == option:
                return slot
    if speech_result:
        text = speech_result.lower()
        if "first" in text or "one" in text or "1" in text:
            return available_slots[0]
        if "second" in text or "two" in text or "2" in text:
            return available_slots[1]
        if "third" in text or "three" in text or "3" in text:
            return available_slots[2]
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
    message = (
        f"Hello {patient_name or 'there'}, thanks for contacting Smile Dental Clinic. "
        f"We have received your request: {details}. Our team will confirm shortly."
    )
    item = WhatsAppMessage(phone_number=phone_number, message=message)
    whatsapp_messages.insert(0, item)
    return item


def create_appointment_record(
    *,
    patient_name: str,
    phone_number: str,
    preferred_date: str,
    preferred_time: str,
    reason_for_visit: str,
    source: Literal["api", "simulated_call", "voice_call"],
) -> AppointmentRequest:
    appointment = AppointmentRequest(
        patient_name=patient_name,
        phone_number=phone_number,
        preferred_date=preferred_date,
        preferred_time=preferred_time,
        reason_for_visit=reason_for_visit,
        source=source,
    )
    appointments.insert(0, appointment)
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
    call_records.insert(0, record)
    return record


def get_or_create_session(call_sid: str, caller_number: str) -> CallSession:
    if call_sid not in call_sessions:
        call_sessions[call_sid] = CallSession(call_sid=call_sid, caller_number=caller_number)
    return call_sessions[call_sid]


def slot_prompt() -> str:
    available_slots = build_available_slots()
    first_day = available_slots[0]["date"]
    second_day = available_slots[2]["date"]
    return (
        f"Please choose a slot. Press or say 1 for {first_day} at 10 A M. "
        f"Press or say 2 for {first_day} at 5 30 P M. "
        f"Press or say 3 for {second_day} at 11 A M."
    )


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    available_slots = build_available_slots()
    stats = {
        "appointments": len(appointments),
        "calls": len(call_records),
        "emergencies": sum(1 for call in call_records if call.urgent),
        "messages": len(whatsapp_messages),
    }
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "stats": stats,
            "appointments": appointments[:10],
            "call_records": call_records[:10],
            "messages": whatsapp_messages[:10],
            "faqs": FAQS,
            "slots": available_slots,
        },
    )


@app.get("/api/dashboard")
async def dashboard_data() -> JSONResponse:
    available_slots = build_available_slots()
    return JSONResponse(
        {
            "stats": {
                "appointments": len(appointments),
                "calls": len(call_records),
                "emergencies": sum(1 for call in call_records if call.urgent),
                "messages": len(whatsapp_messages),
            },
            "appointments": [item.model_dump() for item in appointments[:20]],
            "calls": [item.model_dump() for item in call_records[:20]],
            "messages": [item.model_dump() for item in whatsapp_messages[:20]],
            "slots": available_slots,
        }
    )


@app.get("/api/available-slots")
async def available_slots() -> JSONResponse:
    return JSONResponse({"slots": build_available_slots()})


@app.get("/health")
async def healthcheck() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.post("/api/simulate-call")
async def simulate_call(payload: SimulatedCallPayload) -> JSONResponse:
    intent = classify_intent(payload.transcript)
    appointment_request = None
    available_slots = build_available_slots()

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

    return JSONResponse(
        {
            "message": "Call processed successfully",
            "intent": intent,
            "call_record": record.model_dump(),
        }
    )


@app.post("/api/appointments")
async def create_appointment(appointment: AppointmentRequest) -> JSONResponse:
    stored = create_appointment_record(
        patient_name=appointment.patient_name,
        phone_number=appointment.phone_number,
        preferred_date=appointment.preferred_date,
        preferred_time=appointment.preferred_time,
        reason_for_visit=appointment.reason_for_visit,
        source="api",
    )
    return JSONResponse({"message": "Appointment captured", "appointment": stored.model_dump()})


@app.post("/voice/incoming")
async def incoming_voice(
    CallSid: str = Form(default="demo-call"),  # noqa: N803
    From: str = Form(default="Unknown"),  # noqa: N803
) -> Response:
    get_or_create_session(CallSid, From)
    message = (
        "Hello, thank you for calling Smile Dental Clinic. "
        "Press or say 1 to book an appointment. "
        "Press or say 2 for clinic timings. "
        "Press or say 3 for location. "
        "Press or say 4 if this is a dental emergency."
    )
    xml = twiml(
        gather("/voice/process-main-menu", message, num_digits=1),
        redirect("/voice/incoming"),
    )
    return Response(content=xml, media_type="application/xml")


@app.post("/voice/process-main-menu")
async def process_main_menu(
    CallSid: str = Form(default="demo-call"),  # noqa: N803
    From: str = Form(default="Unknown"),  # noqa: N803
    Digits: str = Form(default=""),  # noqa: N803
    SpeechResult: str = Form(default=""),  # noqa: N803
) -> Response:
    session = get_or_create_session(CallSid, From)
    selection = Digits.strip()
    speech = SpeechResult.strip().lower()

    if selection == "1" or "book" in speech or "appointment" in speech:
        session.intent = "appointment_booking"
        xml = twiml(
            gather("/voice/process-booking-name", "Please say your full name after the tone."),
            redirect("/voice/incoming"),
        )
        return Response(content=xml, media_type="application/xml")

    if selection == "2" or "timing" in speech or "hours" in speech:
        create_call_record(
            caller_number=From,
            patient_name=None,
            intent="faq",
            summary="Caller asked for clinic timings in the live voice flow.",
        )
        xml = twiml(
            say("Our clinic is open Monday to Saturday from 9 A M to 8 P M."),
            say("We will also send these details on WhatsApp. Thank you for calling."),
        )
        send_whatsapp_confirmation(From, None, "clinic timings: Monday to Saturday from 9 AM to 8 PM")
        return Response(content=xml, media_type="application/xml")

    if selection == "3" or "location" in speech or "address" in speech:
        create_call_record(
            caller_number=From,
            patient_name=None,
            intent="directions",
            summary="Caller asked for clinic location in the live voice flow.",
        )
        xml = twiml(
            say("Our clinic is near the main market with parking available for patients."),
            say("We will send the address on WhatsApp. Thank you for calling."),
        )
        send_whatsapp_confirmation(From, None, "clinic address near the main market with parking available")
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

    xml = twiml(
        say("Sorry, I did not catch that."),
        redirect("/voice/incoming"),
    )
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
    xml = twiml(
        gather("/voice/process-booking-slot", slot_prompt(), num_digits=1),
        redirect("/voice/incoming"),
    )
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
        xml = twiml(
            say("Sorry, that slot selection was not clear."),
            gather("/voice/process-booking-slot", slot_prompt(), num_digits=1),
        )
        return Response(content=xml, media_type="application/xml")

    session.preferred_date = slot["date"]
    session.preferred_time = slot["time"]
    xml = twiml(
        gather("/voice/process-booking-reason", "Please briefly tell us the reason for your visit."),
        redirect("/voice/incoming"),
    )
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
    available_slots = build_available_slots()

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
    if call_records or appointments or whatsapp_messages:
        raise HTTPException(status_code=400, detail="Demo data already exists.")

    demo_calls = [
        SimulatedCallPayload(
            caller_number="+919900000001",
            transcript="Hi, I want to book an appointment for tooth pain tomorrow morning",
            patient_name="Aarav Shah",
            preferred_date="2026-05-15",
            preferred_time="10:30 AM",
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
