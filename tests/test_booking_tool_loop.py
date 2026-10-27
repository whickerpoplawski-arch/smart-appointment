"""有界工具环：非法重填、超时回退、合法动作不回退。"""

import asyncio
import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agents.appointment.booking_tool_loop import (
    MAX_INVALID_ATTEMPTS,
    BookingToolLoop,
    policy_next_action,
    run_with_fallback,
)
from agents.appointment_agent import MAX_TURNS, AppointmentAgent
from agents.session_registry import SessionRegistry


VALID_ARGS = {
    "specialty": "内科",
    "start_time": "2026-10-27 15:00",
    "duration_minutes": 45,
    "doctor_name": None,
}


class StubFinder:
    def __init__(self, result=None):
        self.result = result or {
            "status": "available",
            "doctor": {"id": 1, "name": "张伟", "department": "内科"},
        }
        self.calls = []

    def resolve_available(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class SlowModel:
    async def ainvoke(self, *args, **kwargs):
        await asyncio.sleep(2)
        return "太慢"


def action(name, **extra):
    payload = {"action": name}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


async def collect(generator):
    return [token async for token in generator]


class TestToolLoopFallback:
    @pytest.mark.asyncio
    async def test_should_fall_back_after_three_illegal_actions(self):
        finder = StubFinder()
        loop = BookingToolLoop(
            FakeListChatModel(responses=["不是动作"] * MAX_INVALID_ATTEMPTS),
            finder,
        )
        seen = []

        async def legacy(user_input):
            seen.append(user_input)
            yield "[REPLY][预约助手]回退成功"

        tokens = await collect(run_with_fallback(loop, legacy, "约一面", {}))

        assert seen == ["约一面"]
        assert "回退成功" in "".join(tokens)
        assert finder.calls == []

    @pytest.mark.asyncio
    async def test_should_fall_back_after_three_invalid_arguments(self):
        finder = StubFinder()
        illegal = action("find_doctor", arguments={"specialty": "后端"})
        loop = BookingToolLoop(FakeListChatModel(responses=[illegal] * 3), finder)
        seen = []

        async def legacy(user_input):
            seen.append(user_input)
            yield "[REPLY][预约助手]参数回退"

        tokens = await collect(run_with_fallback(loop, legacy, "约张伟", {}))

        assert seen == ["约张伟"]
        assert "参数回退" in "".join(tokens)
        assert finder.calls == []

    @pytest.mark.asyncio
    async def test_should_fall_back_when_model_times_out(self):
        loop = BookingToolLoop(SlowModel(), finder=StubFinder(), timeout_seconds=0.05)
        seen = []

        async def legacy(user_input):
            seen.append("timeout")
            yield "[REPLY][预约助手]超时回退"

        tokens = await collect(run_with_fallback(loop, legacy, "约就诊", {}))

        assert seen == ["timeout"]
        assert "超时回退" in "".join(tokens)

    @pytest.mark.asyncio
    async def test_should_not_fall_back_when_illegal_streak_is_broken(self):
        loop = BookingToolLoop(
            FakeListChatModel(responses=["坏的", action("ask_user", message="请问姓名？")]),
            StubFinder(),
        )
        seen = []

        async def legacy(user_input):
            seen.append(user_input)
            yield "[REPLY][预约助手]不该回退"

        tokens = await collect(run_with_fallback(loop, legacy, "我是张三", {}))

        assert seen == []
        assert "请问姓名" in "".join(tokens)

    @pytest.mark.asyncio
    async def test_should_finish_from_available_observation(self):
        finder = StubFinder()
        finished = {}

        async def on_finish(observation, history):
            finished["name"] = observation["doctor"]["name"]
            finished["specialty"] = history.get("specialty")
            return "[REPLY][预约助手]预约完成"

        loop = BookingToolLoop(
            FakeListChatModel(responses=[action("find_doctor", arguments=VALID_ARGS)]),
            finder,
            on_finish=on_finish,
        )
        seen = []

        async def legacy(user_input):
            seen.append(user_input)
            yield "不该回退"

        tokens = await collect(run_with_fallback(
            loop, legacy, "约张伟", {"patient_name": "张三", "visit_type": "初诊"}
        ))

        assert seen == []
        assert finished == {"name": "张伟", "specialty": "内科"}
        assert "预约完成" in "".join(tokens)
        assert policy_next_action(loop.last_observation) == "finish"
        assert finder.calls

    @pytest.mark.asyncio
    async def test_agent_run_stream_uses_fallback_parser(self):
        slot_json = json.dumps({
            "patient_name": "张三",
            "specialty": "未知",
            "visit_type": "未知",
            "channel": "未知",
            "start_time": "未知",
            "duration": "未知",
            "doctor_name": "未知",
            "preference": "未知",
            "confirmation": "未知",
            "unrelated": False,
        }, ensure_ascii=False)
        blank = json.dumps({
            "patient_name": "未知",
            "specialty": "未知",
            "visit_type": "未知",
            "channel": "未知",
            "start_time": "未知",
            "duration": "未知",
            "doctor_name": "未知",
            "preference": "未知",
            "confirmation": "未知",
            "unrelated": False,
        }, ensure_ascii=False)
        agent = AppointmentAgent(
            llm=FakeListChatModel(responses=[blank, "坏的", "坏的", "坏的", slot_json]),
            session_id="tool-loop-fallback",
            timeout_seconds=1,
        )

        tokens = await collect(agent.run_stream("我是张三"))

        assert agent.appointment_history["patient_name"] == "张三"
        assert any("[REPLY]" in token for token in tokens)

    @pytest.mark.asyncio
    async def test_should_ask_user_to_pick_a_department_for_a_symptom(self):
        finder = StubFinder(result={
            "status": "no_match",
            "department": None,
            "searched_specialty": "咳嗽两天",
            "needs_specialty": True,
        })
        payload = action("find_doctor", arguments={
            "specialty": "咳嗽两天",
            "start_time": "2026-10-27 15:00",
            "duration_minutes": 30,
            "doctor_name": None,
        })
        searched = []
        loop = BookingToolLoop(
            FakeListChatModel(responses=[payload]),
            finder,
            slot_search=lambda history: searched.append(history) or [{"start_time": "x"}],
        )

        tokens = "".join(await collect(loop.run("我咳嗽两天", {})))

        assert "请先选择科室" in tokens
        assert "内科" in tokens
        assert searched == []
        assert policy_next_action(loop.last_observation) == "ask_user"


class TestSlotPersistence:
    """Slots from the user turn land in history before finish, and finish waits for them."""

    UTTERANCE = "我是冒烟张三，想挂内科初诊，时间2026-11-20 15:00，门诊，不指定医生。"

    @pytest.mark.asyncio
    async def test_utterance_slots_are_in_history_before_finish(self):
        parsed = json.dumps({
            "patient_name": "冒烟张三",
            "specialty": "内科",
            "visit_type": "初诊",
            "channel": "门诊",
            "start_time": "2026-11-20 15:00",
            "duration": "未知",
            "doctor_name": "未知",
            "preference": "未知",
            "confirmation": "未知",
            "unrelated": False,
        }, ensure_ascii=False)
        find = action("find_doctor", arguments={
            "specialty": "内科",
            "start_time": "2026-11-20 15:00",
            "duration_minutes": 30,
            "doctor_name": None,
        })
        agent = AppointmentAgent(
            llm=FakeListChatModel(responses=[parsed, find]),
            doctor_finder=StubFinder(),
            session_id="slot-merge",
            timeout_seconds=1,
        )
        captured = {}

        async def on_finish(observation, history):
            captured["patient_name"] = history.get("patient_name")
            captured["visit_type"] = history.get("visit_type")
            captured["channel"] = history.get("channel")
            return "[REPLY][预约助手]预约成功"

        agent.tool_loop.on_finish = on_finish
        history = agent.appointment_history

        await collect(agent.tool_loop.run(self.UTTERANCE, history))

        assert captured["patient_name"] == "冒烟张三"
        assert captured["visit_type"] == "初诊"
        assert captured["channel"] == "门诊"
        assert history["patient_name"] == "冒烟张三"

    @pytest.mark.asyncio
    async def test_finish_asks_for_name_instead_of_booking(self):
        called = []

        async def on_finish(observation, history):
            called.append(history.get("patient_name"))
            return "[REPLY][预约助手]不该落库"

        loop = BookingToolLoop(
            FakeListChatModel(responses=[action("find_doctor", arguments=VALID_ARGS)]),
            StubFinder(),
            on_finish=on_finish,
        )

        tokens = "".join(await collect(loop.run("想挂内科", {})))

        assert called == []
        assert "姓名" in tokens
        assert "不该落库" not in tokens

    @pytest.mark.asyncio
    async def test_symptoms_only_reply_asks_for_specialty_and_does_not_book(self):
        called = []

        async def on_finish(observation, history):
            called.append(True)
            return "[REPLY][预约助手]不该落库"

        loop = BookingToolLoop(
            FakeListChatModel(responses=[action("ask_user", message="")]),
            StubFinder(),
            on_finish=on_finish,
        )

        tokens = "".join(await collect(loop.run("我咳嗽两天，有点头疼，想预约就诊。", {})))

        assert "科室" in tokens
        assert called == []
        assert "不该落库" not in tokens

    @pytest.mark.asyncio
    async def test_model_slot_fields_fill_history_before_finish(self):
        captured = {}

        async def on_finish(observation, history):
            captured["patient_name"] = history.get("patient_name")
            captured["visit_type"] = history.get("visit_type")
            return "[REPLY][预约助手]预约成功"

        payload = action(
            "find_doctor",
            arguments=VALID_ARGS,
            slots={"patient_name": "冒烟张三", "visit_type": "初诊", "channel": "门诊"},
        )
        loop = BookingToolLoop(
            FakeListChatModel(responses=[payload]),
            StubFinder(),
            on_finish=on_finish,
        )
        history = {}

        await collect(loop.run("我是冒烟张三，想挂内科初诊", history))

        assert captured["patient_name"] == "冒烟张三"
        assert captured["visit_type"] == "初诊"
        assert history["patient_name"] == "冒烟张三"

    @pytest.mark.asyncio
    async def test_null_duration_uses_first_visit_default_before_find(self):
        """初诊 + 有效开始时间 + duration_minutes=null 不得因缺时长变成 invalid_time。"""
        finder = StubFinder()
        payload = action("find_doctor", arguments={
            "specialty": "内科",
            "start_time": "2026-11-20 15:00",
            "duration_minutes": None,
            "doctor_name": None,
        })
        history = {
            "patient_name": "冒烟张三",
            "specialty": "内科",
            "visit_type": "初诊",
            "channel": "门诊",
            "start_time": "2026-11-20 15:00",
        }
        loop = BookingToolLoop(
            FakeListChatModel(responses=[payload]),
            finder,
            on_finish=lambda observation, hist: "[REPLY][预约助手]预约成功",
        )

        tokens = "".join(await collect(loop.run("挂内科初诊", history)))

        assert finder.calls, "find_doctor 应打到 finder"
        assert finder.calls[0]["default_duration"] == 30
        assert finder.calls[0]["start_time_str"] == "2026-11-20 15:00"
        assert loop.last_observation["status"] == "available"
        assert "就诊时间没能识别" not in tokens
        assert history["duration"] == "30分钟"


class TestTurnBudget:
    @pytest.mark.asyncio
    async def test_should_stop_at_max_turns_and_report_slots(self):
        ask = json.dumps({"action": "ask_user", "message": "继续"}, ensure_ascii=False)
        agent = AppointmentAgent(
            llm=FakeListChatModel(responses=[ask] * (MAX_TURNS + 2)),
            session_id="turns",
            timeout_seconds=1,
        )
        agent.appointment_history["patient_name"] = "张三"
        agent.appointment_history["specialty"] = "内科"

        for _ in range(MAX_TURNS - 1):
            tokens = await collect(agent.run_stream("继续说"))
            assert "继续" in "".join(tokens)
            assert "上限" not in "".join(tokens)

        limited = "".join(await collect(agent.run_stream("再来一次")))
        again = "".join(await collect(agent.run_stream("还来")))

        assert "上限" in limited
        assert "张三" in limited
        assert "内科" in limited
        assert "继续" not in limited
        assert "上限" in again

    @pytest.mark.asyncio
    async def test_turn_budget_is_per_session(self):
        ask = json.dumps({"action": "ask_user", "message": "继续"}, ensure_ascii=False)
        registry = SessionRegistry(enable_snapshots=False)
        agent = AppointmentAgent(
            llm=FakeListChatModel(responses=[ask] * 20),
            session_id="boot",
            timeout_seconds=1,
        )
        agent.bind_session(registry.get("session-a"))
        for _ in range(MAX_TURNS - 1):
            await collect(agent.run_stream("a"))

        agent.bind_session(registry.get("session-b"))
        tokens = "".join(await collect(agent.run_stream("b")))

        assert "继续" in tokens
        assert registry.get("session-a").turn_count == MAX_TURNS - 1
        assert registry.get("session-b").turn_count == 1
