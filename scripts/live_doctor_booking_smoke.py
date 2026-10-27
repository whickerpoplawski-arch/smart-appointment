#!/usr/bin/env python3
"""Live smoke for doctor booking against a stream-only gateway.

Drives AppointmentAgent, which calls BookingToolLoop and
``await self.llm.ainvoke(prompt)`` in booking_tool_loop.py. Non-Azure
models from create_chat_model collect that stream into one AIMessage.

Scenarios:

* happy: 内科初诊 with a name, visit type, and time. Up to four turns.
  Success is a reply containing 预约成功 and exactly one appointment row.
* ask: symptoms only, no department name. The reply must ask for a 科室
  (needs_specialty text or an ask_user question) and must not book.

``--help`` and ``--dry-run`` do not call the model and do not need a key.
A real run needs MODEL_PROVIDER (openai-compatible family), LLM_BASE_URL,
LLM_MODEL, and LLM_API_KEY. The live run forces LLM_FORCE_STREAM=1.
Transcripts and booking_id are printed. Secret values are not.

Exit status: 0 success, 2 missing configuration, 1 a scenario failed.
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
MAX_HAPPY_TURNS = 4

HAPPY_UTTERANCE = (
    "我是冒烟张三，想挂内科初诊，时间2026-11-20 15:00，门诊，不指定医生。"
)
ASK_UTTERANCE = "我咳嗽两天，有点头疼，想预约就诊。"


def happy_failure(reply: str, appointments: list[dict]) -> Optional[str]:
    """Return an error when the happy path did not persist one booking."""
    if "预约成功" not in reply:
        return "happy path reply did not include 预约成功"
    if len(appointments) != 1:
        return f"happy path expected 1 appointment row, found {len(appointments)}"
    if appointments[0].get("id") is None:
        return "happy path appointment row has no id"
    return None


def ask_failure(reply: str, appointments: list[dict]) -> Optional[str]:
    """Return an error when symptoms-only input booked or skipped the specialty ask."""
    if appointments:
        return "ask path created an appointment"
    if "预约成功" in reply:
        return "ask path reported a booking"
    if "科室" not in reply:
        return "ask path did not ask for a specialty"
    return None


def _presence(name: str) -> str:
    return "set" if os.getenv(name) else "missing"


def print_dry_run() -> None:
    """Describe the live run without constructing a model or touching the DB."""
    print("dry-run: no model call")
    print(f"MODEL_PROVIDER={os.getenv('MODEL_PROVIDER') or '(unset)'}")
    print(f"LLM_BASE_URL={_presence('LLM_BASE_URL')}")
    print(f"LLM_MODEL={os.getenv('LLM_MODEL') or '(unset)'}")
    print(f"LLM_API_KEY={_presence('LLM_API_KEY')}")
    force = os.getenv("LLM_FORCE_STREAM")
    print(
        "LLM_FORCE_STREAM="
        + (force if force else "(unset; non-Azure chat models still collect streams)")
    )
    print("scenario happy: 冒烟张三 / 内科初诊 / 2026-11-20 15:00 / 门诊 → booking_id")
    print(
        "scenario ask: symptoms only must ask for a 科室 "
        "(needs_specialty or ask_user) and must not book"
    )


def _print_turn(name: str, user: str, reply: str) -> None:
    print(f"--- turn {name} ---")
    print(f"user: {user}")
    print("assistant:")
    print(reply)


def _rows_for(appointment_service, session_id: str) -> list[dict]:
    rows = appointment_service.get_all_appointments()
    return [row for row in rows if row.get("session_id") == session_id]


def _follow_up(reply: str) -> str:
    """Answer the question the assistant just asked, staying inside the happy booking."""
    if "请回复 1 或 2" in reply or "可以改到" in reply:
        return "1"
    if "姓名" in reply:
        return "冒烟张三，内科初诊，2026-11-20 15:00，门诊。"
    if "科室" in reply:
        return "内科"
    if "初诊" in reply or "复诊" in reply or "就诊类型" in reply:
        return "初诊，2026-11-20 15:00，门诊。"
    if "时间" in reply:
        return "2026-11-20 15:00"
    if "是" in reply and "不" in reply:
        return "是"
    return HAPPY_UTTERANCE


def _bind_agent(agent, appointment_service, behavior_service):
    agent.doctor_finder._appointment_service = appointment_service
    agent.appointment_database._appointment_service = appointment_service
    agent.appointment_database._patient_behavior_service = behavior_service
    return agent


async def _run_happy(agent) -> str:
    user = HAPPY_UTTERANCE
    reply = ""
    for turn in range(1, MAX_HAPPY_TURNS + 1):
        reply = await agent.run(user)
        _print_turn(f"happy-{turn}", user, reply)
        if "预约成功" in reply:
            return reply
        user = _follow_up(reply)
    return reply


async def live() -> int:
    """Seed a temporary database and run the happy path and the ask path."""
    os.environ["LLM_FORCE_STREAM"] = "1"

    from agents.appointment_agent import AppointmentAgent
    from services.appointment_service import AppointmentService
    from services.doctor_service import DoctorService
    from services.patient_behavior_service import PatientBehaviorService

    db_dir = tempfile.mkdtemp(prefix="doctor-smoke-")
    db_url = f"sqlite:///{db_dir}/smoke.db"
    print(f"smoke_db={db_url}")

    if not DoctorService(db_url).initialize_default_doctors():
        print("failed to seed default doctors", file=sys.stderr)
        return 1

    appointment_service = AppointmentService(db_url)
    behavior_service = PatientBehaviorService(db_url)
    failed = False

    happy_agent = _bind_agent(
        AppointmentAgent(), appointment_service, behavior_service
    )
    happy_reply = await _run_happy(happy_agent)
    happy_rows = _rows_for(appointment_service, happy_agent.session_id)
    if happy_rows:
        print(f"booking_id={happy_rows[0]['id']}")
    else:
        print("booking_id=missing")
    happy_error = happy_failure(happy_reply, happy_rows)
    if happy_error:
        print(happy_error, file=sys.stderr)
        failed = True

    ask_agent = _bind_agent(
        AppointmentAgent(), appointment_service, behavior_service
    )
    ask_reply = await ask_agent.run(ASK_UTTERANCE)
    ask_rows = _rows_for(appointment_service, ask_agent.session_id)
    _print_turn("ask", ASK_UTTERANCE, ask_reply)
    print("ask_booking_id=none" if not ask_rows else f"ask_booking_id={ask_rows[0].get('id')}")
    ask_error = ask_failure(ask_reply, ask_rows)
    if ask_error:
        print(ask_error, file=sys.stderr)
        failed = True

    return 1 if failed else 0


def _missing_env() -> list[str]:
    return [name for name in REQUIRED_ENV if not os.getenv(name)]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Live smoke for the doctor booking agent. "
            "--dry-run and --help do not call the model and do not need a key."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the two scenarios and which env vars are set. No model call.",
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
