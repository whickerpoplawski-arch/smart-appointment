#!/usr/bin/env python3
"""Live smoke for the interview booking agent on a stream-only gateway.

This repository books interviews. It has no doctor, 内科初诊, or specialty
field, and no booking_tool_loop.py. The two scenarios are the flows that
exist here:

* happy: name + 后端开发工程师 + 一面 + a concrete time → one interview row
* ask: an interview request that omits 岗位 / 轮次 / 时间 → the assistant
  must ask for 岗位 and must not create an interview. 岗位 is the routing
  field (the analogue of "symptoms only → must ask specialty").

``--help`` and ``--dry-run`` do not call the model and do not need a key.
A real run needs MODEL_PROVIDER (openai-compatible family), LLM_BASE_URL,
LLM_MODEL, and LLM_API_KEY. The script forces LLM_FORCE_STREAM=1 for the
live run and prints transcripts plus booking_id. It never prints secret
values.

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

HAPPY_UTTERANCE = (
    "我是冒烟张三，应聘后端开发工程师，想预约2026-11-20 15:00的一面，"
    "线上，不指定面试官。"
)
ASK_UTTERANCE = "我想预约面试，但还没想好岗位、轮次和时间。"


def happy_failure(reply: str, interviews: list[dict]) -> Optional[str]:
    """Return an error when the happy path did not persist one booking."""
    if "预约成功" not in reply:
        return "happy path reply did not include 预约成功"
    if len(interviews) != 1:
        return f"happy path expected 1 interview row, found {len(interviews)}"
    if interviews[0].get("id") is None:
        return "happy path interview row has no id"
    return None


def ask_failure(reply: str, interviews: list[dict]) -> Optional[str]:
    """Return an error when the ask path booked or skipped the 岗位 question."""
    if interviews:
        return "ask path created an interview"
    if "预约成功" in reply:
        return "ask path reported a booking"
    if "岗位" not in reply:
        return "ask path did not ask for 岗位"
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
    print(
        "scenario happy: 冒烟张三 / 后端开发工程师 / 一面 / 2026-11-20 15:00 / 线上 "
        "→ booking_id"
    )
    print(
        "scenario ask: utterance without 岗位, 轮次, and 时间 must ask for 岗位 "
        "and must not book"
    )
    print(
        "domain: interview booking. This repo has no 内科初诊 and no medical specialty."
    )


def _print_turn(name: str, user: str, reply: str) -> None:
    print(f"--- turn {name} ---")
    print(f"user: {user}")
    print("assistant:")
    print(reply)


def _rows_for(interview_service, session_id: str) -> list[dict]:
    rows = interview_service.get_all_interviews()
    return [row for row in rows if row.get("session_id") == session_id]


def _bind_agent(agent, interview_service, behavior_service):
    agent.interviewer_finder._interview_service = interview_service
    agent.interview_database._interview_service = interview_service
    agent.interview_database._candidate_behavior_service = behavior_service
    return agent


async def live() -> int:
    """Seed a temporary database and run the happy path and the ask path."""
    os.environ["LLM_FORCE_STREAM"] = "1"

    from agents.interview_agent import InterviewAgent
    from services.candidate_behavior_service import CandidateBehaviorService
    from services.interview_service import InterviewService
    from services.interviewer_service import InterviewerService

    db_dir = tempfile.mkdtemp(prefix="interview-smoke-")
    db_url = f"sqlite:///{db_dir}/smoke.db"
    print(f"smoke_db={db_url}")

    if not InterviewerService(db_url).initialize_default_interviewers():
        print("failed to seed default interviewers", file=sys.stderr)
        return 1

    interview_service = InterviewService(db_url)
    behavior_service = CandidateBehaviorService(db_url)
    failed = False

    happy_agent = _bind_agent(InterviewAgent(), interview_service, behavior_service)
    happy_reply = await happy_agent.run(HAPPY_UTTERANCE)
    happy_rows = _rows_for(interview_service, happy_agent.session_id)
    _print_turn("happy", HAPPY_UTTERANCE, happy_reply)
    if happy_rows:
        print(f"booking_id={happy_rows[0]['id']}")
    else:
        print("booking_id=missing")
    happy_error = happy_failure(happy_reply, happy_rows)
    if happy_error:
        print(happy_error, file=sys.stderr)
        failed = True

    ask_agent = _bind_agent(InterviewAgent(), interview_service, behavior_service)
    ask_reply = await ask_agent.run(ASK_UTTERANCE)
    ask_rows = _rows_for(interview_service, ask_agent.session_id)
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
            "Live smoke for the interview booking agent. "
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
