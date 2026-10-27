#!/usr/bin/env python3
"""Five-scenario live eval for the doctor booking demo.

Extends scripts/live_doctor_booking_smoke.py:

* A full info books one row and the reply contains 预约成功
* B symptoms only ask for a 科室 and do not book
* C an unknown doctor name gets not-found guidance and does not book
* D a busy 15:00 slot offers another time, or fails in clear text
* E 「什么病 / 开药」returns medical_advice_refusal and does not invent a prescription

``--help`` and ``--dry-run`` do not call the model and do not need a key.
A real run needs MODEL_PROVIDER (openai-compatible family), LLM_BASE_URL,
LLM_MODEL, and LLM_API_KEY. The live run forces LLM_FORCE_STREAM=1.

This environment has no LLM_API_KEY. Do not treat a dry-run as a live pass.
Parent command::

    MODEL_PROVIDER=openai-compatible \\
    LLM_BASE_URL=https://d1api.xin/v1 \\
    LLM_MODEL=deepseek-v4.1-flash \\
    LLM_FORCE_STREAM=1 \\
    LLM_API_KEY=... \\
    python3 scripts/live_doctor_eval.py

Exit status: 0 all five passed, 2 missing configuration, 1 a scenario failed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from typing import Optional

CHAT_PROVIDERS = {"openai", "qwen", "deepseek", "zhipu", "openai-compatible"}
REQUIRED_ENV = ("MODEL_PROVIDER", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")
BUSY_MARKERS = ("可以改到", "请回复 1 或 2", "没有找到可改", "该时段")
BUSY_SLOT = "2026-11-20 15:00"

HAPPY_UTTERANCE = (
    "我是评测张三，想挂内科初诊，时间2026-11-20 16:00，门诊，不指定医生。"
)
ASK_UTTERANCE = "我咳嗽两天，有点头疼，想预约就诊。"
UNKNOWN_DOCTOR_UTTERANCE = (
    "我是评测王五，想挂内科初诊，时间2026-11-21 10:00，门诊，指定医生王不存在。"
)
BUSY_UTTERANCE = (
    "我是评测赵六，想挂内科初诊，时间2026-11-20 15:00，门诊，指定医生陈安。"
)
REFUSAL_UTTERANCES = ("这是什么病", "帮我开药")


def happy_failure(reply: str, appointments: list[dict]) -> Optional[str]:
    """Return an error when the full-info path did not persist one booking."""
    if "预约成功" not in reply:
        return "scenario A reply did not include 预约成功"
    if len(appointments) != 1:
        return f"scenario A expected 1 appointment row, found {len(appointments)}"
    if appointments[0].get("id") is None:
        return "scenario A appointment row has no id"
    return None


def ask_failure(reply: str, appointments: list[dict]) -> Optional[str]:
    """Return an error when symptoms-only input booked or skipped the specialty ask."""
    if appointments:
        return "scenario B created an appointment"
    if "预约成功" in reply:
        return "scenario B reported a booking"
    if "科室" not in reply:
        return "scenario B did not ask for a specialty"
    return None


def unknown_doctor_failure(reply: str, appointments: list[dict]) -> Optional[str]:
    """Return an error unless the reply guides on a missing doctor and books nothing."""
    if appointments:
        return "scenario C created an appointment"
    if "预约成功" in reply:
        return "scenario C reported a booking"
    if "没有找到" not in reply:
        return "scenario C did not say 没有找到"
    return None


def _minute_text(value) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M")
    text = str(value or "")
    return text[:16]


def busy_failure(replies: list[str], appointments: list[dict]) -> Optional[str]:
    """Return an error when the busy slot crashed, stayed silent, or was rebooked."""
    if not replies:
        return "scenario D produced no reply"
    joined = "\n".join(replies)
    if "Traceback" in joined:
        return "scenario D crashed"
    if not any(marker in joined for marker in BUSY_MARKERS):
        return "scenario D did not explain the taken slot or offer another time"
    if "预约成功" in joined:
        if len(appointments) != 1:
            return f"scenario D expected 1 moved appointment, found {len(appointments)}"
        if _minute_text(appointments[0].get("start_time")) == BUSY_SLOT:
            return "scenario D booked the occupied slot"
    return None


def refusal_failure(reply: str, utterance: str) -> Optional[str]:
    """Return an error unless the consult reply is the fixed refusal, verbatim."""
    from agents.consultant.consultation_processor import medical_advice_refusal

    expected = medical_advice_refusal(utterance)
    if reply != expected:
        return "scenario E reply was not the fixed refusal"
    if "阿莫西林" in reply or "预约成功" in reply:
        return "scenario E invented a prescription or booked"
    return None


def _presence(name: str) -> str:
    return "set" if os.getenv(name) else "missing"


def print_dry_run() -> None:
    """Describe the five scenarios without constructing a model or touching the DB."""
    print("dry-run: no model call")
    print(f"MODEL_PROVIDER={os.getenv('MODEL_PROVIDER') or '(unset)'}")
    print(f"LLM_BASE_URL={_presence('LLM_BASE_URL')}")
    print(f"LLM_MODEL={os.getenv('LLM_MODEL') or '(unset)'}")
    print(f"LLM_API_KEY={_presence('LLM_API_KEY')}")
    force = os.getenv("LLM_FORCE_STREAM")
    print(
        "LLM_FORCE_STREAM="
        + (force if force else "(unset; the live run sets it to 1)")
    )
    print("scenario A: 评测张三 / 内科初诊 / 2026-11-20 16:00 / 门诊 → 预约成功 + booking_id")
    print("scenario B: symptoms only must ask for a 科室 and must not book")
    print("scenario C: doctor 王不存在 → 没有找到, no booking, no crash")
    print("scenario D: 陈安 2026-11-20 15:00 already busy → choose 1, or a clear failure")
    print("scenario E: 这是什么病 / 帮我开药 → fixed refusal, no invented prescription")
    print("live 5/5 is not measured here; a parent with LLM_API_KEY runs this script without --dry-run")


def _print_turn(name: str, user: str, reply: str) -> None:
    print(f"--- turn {name} ---")
    print(f"user: {user}")
    print("assistant:")
    print(reply)


def _rows_for(appointment_service, session_id: str) -> list[dict]:
    rows = appointment_service.get_all_appointments()
    return [row for row in rows if row.get("session_id") == session_id]


def _follow_up_happy(reply: str) -> str:
    if "请回复 1 或 2" in reply or "可以改到" in reply:
        return "1"
    if "姓名" in reply:
        return "评测张三，内科初诊，2026-11-20 16:00，门诊。"
    if "科室" in reply:
        return "内科"
    if "初诊" in reply or "复诊" in reply or "就诊类型" in reply:
        return "初诊，2026-11-20 16:00，门诊。"
    if "时间" in reply:
        return "2026-11-20 16:00"
    if "是" in reply and "不" in reply:
        return "是"
    return HAPPY_UTTERANCE


def _bind_agent(agent, appointment_service, behavior_service):
    agent.doctor_finder._appointment_service = appointment_service
    agent.appointment_database._appointment_service = appointment_service
    agent.appointment_database._patient_behavior_service = behavior_service
    return agent


def _occupy_internal_medicine(appointment_service, doctor_service) -> Optional[str]:
    """Book 陈安 and 林夏 at the busy slot so scenario D cannot take 15:00."""
    from agents.appointment.doctor_finder import DoctorFinder

    finder = DoctorFinder(appointment_service=appointment_service)
    start, end, _ = finder.parse_time_and_duration(BUSY_SLOT, None, default_duration=30)
    if start is None or end is None:
        return "could not parse the busy slot"
    by_name = {item["name"]: item for item in doctor_service.get_all_doctors()}
    for name in ("陈安", "林夏"):
        doctor = by_name.get(name)
        if doctor is None:
            return f"seed doctor {name} is missing"
        saved = appointment_service.save_appointment(
            doctor["id"],
            start,
            end,
            {
                "patient_name": "占档",
                "specialty": "内科",
                "visit_type": "初诊",
                "channel": "门诊",
            },
            "seed-busy",
        )
        if not saved:
            return f"failed to occupy {name} at {BUSY_SLOT}"
    return None


async def _run_turns(agent, utterance: str, limit: int, follow_up) -> str:
    user = utterance
    reply = ""
    for turn in range(1, limit + 1):
        reply = await agent.run(user)
        _print_turn(f"{turn}", user, reply)
        if follow_up is None:
            return reply
        stop = follow_up(reply, turn)
        if stop is None:
            return reply
        user = stop
    return reply


async def _consult(agent, utterance: str) -> str:
    chunks = []
    async for token in agent.consult_stream(utterance):
        chunks.append(token)
    return "".join(chunks)


async def live() -> int:
    """Seed a temporary database and run scenarios A through E."""
    os.environ["LLM_FORCE_STREAM"] = "1"

    from agents.appointment_agent import AppointmentAgent
    from agents.consultant_agent import ConsultantAgent
    from services.appointment_service import AppointmentService
    from services.doctor_service import DoctorService
    from services.patient_behavior_service import PatientBehaviorService

    db_dir = tempfile.mkdtemp(prefix="doctor-eval-")
    db_url = f"sqlite:///{db_dir}/eval.db"
    print(f"eval_db={db_url}")

    doctor_service = DoctorService(db_url)
    if not doctor_service.initialize_default_doctors():
        print("failed to seed default doctors", file=sys.stderr)
        return 1

    appointment_service = AppointmentService(db_url)
    behavior_service = PatientBehaviorService(db_url)
    failed = False

    happy_agent = _bind_agent(AppointmentAgent(), appointment_service, behavior_service)

    def happy_follow(reply: str, turn: int):
        if "预约成功" in reply:
            return None
        return _follow_up_happy(reply)

    print("--- scenario A ---")
    happy_reply = await _run_turns(happy_agent, HAPPY_UTTERANCE, 4, happy_follow)
    happy_rows = _rows_for(appointment_service, happy_agent.session_id)
    print(f"booking_id={happy_rows[0]['id']}" if happy_rows else "booking_id=missing")
    happy_error = happy_failure(happy_reply, happy_rows)
    if happy_error:
        print(happy_error, file=sys.stderr)
        failed = True

    ask_agent = _bind_agent(AppointmentAgent(), appointment_service, behavior_service)
    print("--- scenario B ---")
    ask_reply = await ask_agent.run(ASK_UTTERANCE)
    _print_turn("B", ASK_UTTERANCE, ask_reply)
    ask_rows = _rows_for(appointment_service, ask_agent.session_id)
    print("ask_booking_id=none" if not ask_rows else f"ask_booking_id={ask_rows[0].get('id')}")
    ask_error = ask_failure(ask_reply, ask_rows)
    if ask_error:
        print(ask_error, file=sys.stderr)
        failed = True

    unknown_agent = _bind_agent(AppointmentAgent(), appointment_service, behavior_service)

    def unknown_follow(reply: str, turn: int):
        if "没有找到" in reply or "预约成功" in reply:
            return None
        return UNKNOWN_DOCTOR_UTTERANCE

    print("--- scenario C ---")
    unknown_reply = await _run_turns(unknown_agent, UNKNOWN_DOCTOR_UTTERANCE, 3, unknown_follow)
    unknown_rows = _rows_for(appointment_service, unknown_agent.session_id)
    print("unknown_booking_id=none" if not unknown_rows else f"unknown_booking_id={unknown_rows[0].get('id')}")
    unknown_error = unknown_doctor_failure(unknown_reply, unknown_rows)
    if unknown_error:
        print(unknown_error, file=sys.stderr)
        failed = True

    occupy_error = _occupy_internal_medicine(appointment_service, doctor_service)
    if occupy_error:
        print(occupy_error, file=sys.stderr)
        return 1

    busy_agent = _bind_agent(AppointmentAgent(), appointment_service, behavior_service)
    busy_replies = []
    user = BUSY_UTTERANCE
    print("--- scenario D ---")
    for turn in range(1, 3):
        reply = await busy_agent.run(user)
        busy_replies.append(reply)
        _print_turn(f"D-{turn}", user, reply)
        if "预约成功" in reply:
            break
        if "请回复 1 或 2" in reply or "可以改到" in reply:
            user = "1"
            continue
        break
    busy_rows = _rows_for(appointment_service, busy_agent.session_id)
    print(
        f"busy_booking_id={busy_rows[0]['id']}" if busy_rows else "busy_booking_id=none"
    )
    busy_error = busy_failure(busy_replies, busy_rows)
    if busy_error:
        print(busy_error, file=sys.stderr)
        failed = True

    print("--- scenario E ---")
    consultant = ConsultantAgent()
    for utterance in REFUSAL_UTTERANCES:
        reply = await _consult(consultant, utterance)
        _print_turn(f"E-{utterance}", utterance, reply)
        refusal_error = refusal_failure(reply, utterance)
        if refusal_error:
            print(refusal_error, file=sys.stderr)
            failed = True

    return 1 if failed else 0


def _missing_env() -> list[str]:
    return [name for name in REQUIRED_ENV if not os.getenv(name)]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Live eval for five doctor-booking scenarios. "
            "--dry-run and --help do not call the model and do not need a key."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print scenarios A-E and which env vars are set. No model call.",
    )
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv()

    if args.dry_run:
        print_dry_run()
        return 0

    missing = _missing_env()
    if missing:
        print("missing env: " + ", ".join(missing), file=sys.stderr)
        return 2

    provider = (os.getenv("MODEL_PROVIDER") or "").strip().lower()
    if provider not in CHAT_PROVIDERS:
        print(
            f"MODEL_PROVIDER={provider!r} is not an OpenAI-compatible chat provider",
            file=sys.stderr,
        )
        return 2

    return asyncio.run(live())


if __name__ == "__main__":
    sys.exit(main())
