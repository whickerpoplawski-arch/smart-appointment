"""失败后的域内改期：最多两个工作日选项，选定后再查一次面试官。"""

import json
from datetime import datetime, timedelta

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agents.interview.alternative_slots import search_alternative_slots, slots_for_history
from agents.interview.booking_tool_loop import BookingToolLoop, policy_next_action
from agents.interview.interview_processor import DEFAULT_DURATION_BY_ROUND


class QueueFinder:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def resolve_available(self, **kwargs):
        self.calls.append(kwargs)
        return self.results.pop(0)


class ScriptModel:
    def __init__(self, responses):
        self.responses = list(responses)

    async def ainvoke(self, *args, **kwargs):
        if not self.responses:
            raise AssertionError("选定改期后不应再问模型")
        return self.responses.pop(0)


def find_action():
    return json.dumps({
        "action": "find_interviewer",
        "arguments": {
            "position": "后端工程师",
            "start_time": "2026-10-27 15:00",
            "duration_minutes": 45,
            "interviewer_name": None,
        },
    }, ensure_ascii=False)


class TestSlotScan:
    def test_should_offer_two_slots_on_future_business_days(self):
        now = datetime(2026, 10, 24, 10, 0)  # 周六，下一个工作日是周一
        options = search_alternative_slots(now, DEFAULT_DURATION_BY_ROUND["二面"], lambda start, end: True)

        assert len(options) == 2
        assert options[0]["start_time"] == "2026-10-26 09:00"
        assert options[1]["start_time"] == "2026-10-26 10:00"
        assert options[0]["duration_minutes"] == DEFAULT_DURATION_BY_ROUND["二面"]

    def test_should_skip_hours_already_past(self):
        now = datetime(2026, 10, 27, 15, 30)
        options = search_alternative_slots(now, 45, lambda start, end: True)

        assert [item["start_time"] for item in options] == [
            "2026-10-27 16:00",
            "2026-10-27 17:00",
        ]

    def test_should_use_round_default_duration(self):
        spans = []

        class Service:
            def get_interviewer_by_name(self, name):
                return None

            def match_department(self, position):
                return "后端"

            def get_available_interviewers(self, start, end, department=None):
                spans.append((start, end, department))
                return [{"id": 1}] if len(spans) <= 2 else []

            def is_interviewer_available(self, interviewer_id, start, end):
                return False

        class Finder:
            interview_service = Service()

        options = slots_for_history(
            Finder(),
            {"round": "二面", "position": "后端工程师", "interviewer_name": None},
            now=datetime(2026, 10, 27, 8, 0),
        )

        assert len(options) == 2
        assert options[0]["duration_minutes"] == DEFAULT_DURATION_BY_ROUND["二面"]
        assert spans[0][1] - spans[0][0] == timedelta(minutes=60)
        assert spans[0][2] == "后端"


class TestReplanPolicy:
    def test_should_replan_when_nobody_is_free(self):
        assert policy_next_action({"status": "no_match"}) == "search_alternative_slots"
        assert policy_next_action({
            "status": "unavailable",
            "alternatives": [],
        }) == "search_alternative_slots"


class TestReplanLoop:
    @pytest.mark.asyncio
    async def test_should_search_then_find_interviewer_again_after_user_picks(self):
        finder = QueueFinder([
            {"status": "no_match", "department": "后端", "searched_position": "后端工程师"},
            {"status": "available", "interviewer": {"id": 1, "name": "张伟", "department": "后端"}},
        ])
        offered = [{
            "start_time": "2026-10-28 10:00",
            "duration_minutes": 45,
            "label": "2026-10-28 10:00",
        }, {
            "start_time": "2026-10-28 11:00",
            "duration_minutes": 45,
            "label": "2026-10-28 11:00",
        }]
        finished = {}

        async def on_finish(observation, history):
            finished["start"] = history.get("start_time")
            finished["name"] = observation["interviewer"]["name"]
            return "[REPLY][面试机器人]第二次查询后完成"

        loop = BookingToolLoop(
            ScriptModel([find_action()]),
            finder,
            on_finish=on_finish,
            slot_search=lambda history: offered,
        )
        history = {"round": "一面"}

        first = [token async for token in loop.run("约明天下午", history)]
        assert policy_next_action(finder.results[0] if False else {"status": "no_match"}) == "search_alternative_slots"
        assert loop.last_observation["alternative_slots"] == offered
        assert history["awaiting_slot_choice"] is True
        assert "2026-10-28 10:00" in "".join(first)
        assert len(finder.calls) == 1

        second = [token async for token in loop.run("1", history)]

        assert len(finder.calls) == 2
        assert finder.calls[1]["start_time_str"] == "2026-10-28 10:00"
        assert finished == {"start": "2026-10-28 10:00", "name": "张伟"}
        assert "第二次查询后完成" in "".join(second)
        assert history["awaiting_slot_choice"] is False

    @pytest.mark.asyncio
    async def test_should_not_replan_when_alternatives_exist(self):
        finder = QueueFinder([{
            "status": "unavailable",
            "requested_interviewer": {"id": 1, "name": "张伟"},
            "alternatives": [{"id": 2, "name": "王强"}],
        }])
        searched = []
        loop = BookingToolLoop(
            ScriptModel([find_action()]),
            finder,
            slot_search=lambda history: searched.append(history) or [],
        )

        tokens = [token async for token in loop.run("约张伟", {})]

        assert searched == []
        assert "王强" in "".join(tokens)
        assert policy_next_action(loop.last_observation) == "ask_user"
