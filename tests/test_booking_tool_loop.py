"""有界工具环：非法重填、超时回退、合法动作不回退。"""

import asyncio
import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agents.appointment.booking_tool_loop import (
    MAX_INVALID_ATTEMPTS,
    BookingToolLoop,
    policy_next_action,
    prepare_find_arguments,
    run_with_fallback,
)
from agents.appointment.message_builder import MessageBuilder
from services.appointment_service import DEPARTMENT_CHOICES
from agents.appointment.doctor_finder import DoctorFinder
from agents.appointment_agent import MAX_TURNS, AppointmentAgent
from agents.session_registry import SessionRegistry
from tests.conftest import FakeDoctorService


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
        assert "耳鼻喉科" in "".join(tokens)
        assert "比如内科、外科或儿科" not in "".join(tokens)
        assert "请问姓名" not in "".join(tokens)

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
        assert "我不能判断症状或开具用药" in tokens
        assert "耳鼻喉科" in tokens
        assert "我不会按症状推荐科室" in tokens
        assert "建议挂" not in tokens
        assert searched == []
        assert policy_next_action(loop.last_observation) == "ask_user"


REFUSAL_CLAUSE = "我不能判断症状或开具用药。请先选择科室，我可以帮您预约医生档期。"


class TestSpecialtyAsk:
    def test_question_reuses_the_refusal_clause_and_lists_every_department(self):
        from agents.consultant.consultation_processor import medical_advice_refusal

        ask = MessageBuilder.question_for("specialty")

        assert REFUSAL_CLAUSE in medical_advice_refusal("这是什么病")
        assert REFUSAL_CLAUSE in ask
        assert ask.index(REFUSAL_CLAUSE) < ask.index("可选科室")
        for name in DEPARTMENT_CHOICES:
            assert name in ask
        assert "比如内科、外科或儿科" not in ask
        assert "建议挂" not in ask
        assert "诊断" not in ask

    @pytest.mark.asyncio
    async def test_empty_ask_uses_the_full_menu_instead_of_three_examples(self):
        loop = BookingToolLoop(
            FakeListChatModel(responses=[action("ask_user", message="")]),
            StubFinder(),
        )

        tokens = "".join(await collect(loop.run("我咳嗽两天，有点头疼", {})))

        assert tokens == f"[REPLY][预约助手]{MessageBuilder.question_for('specialty')}"
        assert "耳鼻喉科" in tokens
        assert "比如内科、外科或儿科" not in tokens


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
        agent.appointment_history["visit_type"] = "初诊"
        agent.appointment_history["start_time"] = "2026-11-20 15:00"

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

        def fill_required(history):
            history["patient_name"] = "张三"
            history["specialty"] = "内科"
            history["visit_type"] = "初诊"
            history["start_time"] = "2026-11-20 15:00"

        agent.bind_session(registry.get("session-a"))
        fill_required(agent.appointment_history)
        for _ in range(MAX_TURNS - 1):
            await collect(agent.run_stream("a"))

        agent.bind_session(registry.get("session-b"))
        fill_required(agent.appointment_history)
        tokens = "".join(await collect(agent.run_stream("b")))

        assert "继续" in tokens
        assert registry.get("session-a").turn_count == MAX_TURNS - 1
        assert registry.get("session-b").turn_count == 1


def _real_finder():
    service = FakeDoctorService(doctors=[{
        "id": 1,
        "name": "陈安",
        "department": "内科",
        "bio": "演示人设，非真实医师。匹配用专长词：内科。",
    }])
    return DoctorFinder(appointment_service=service), service


class TestVisitDurationInjection:
    """复诊 15 / 咨询 20 在 find_doctor 前注入，合法开始时间不得变成 invalid_time。"""

    def test_prepare_find_arguments_nulls_visit_defaults_and_keeps_explicit(self):
        base = {
            "specialty": "内科",
            "start_time": "2026-11-20 15:00",
            "doctor_name": None,
        }

        follow_up = prepare_find_arguments(
            {"visit_type": "复诊"}, {**base, "duration_minutes": 15}
        )
        consult = prepare_find_arguments(
            {"visit_type": "咨询"}, {**base, "duration_minutes": 20}
        )
        first = prepare_find_arguments(
            {"visit_type": "初诊"}, {**base, "duration_minutes": None}
        )
        explicit = prepare_find_arguments(
            {"visit_type": "初诊"}, {**base, "duration_minutes": 45}
        )

        assert follow_up["duration_minutes"] is None
        assert consult["duration_minutes"] is None
        assert first["duration_minutes"] is None
        assert explicit["duration_minutes"] == 45

    @pytest.mark.asyncio
    async def test_follow_up_fifteen_minutes_is_not_invalid_time(self):
        finder, service = _real_finder()
        payload = action("find_doctor", arguments={
            "specialty": "内科",
            "start_time": "2026-11-20 15:00",
            "duration_minutes": 15,
            "doctor_name": None,
        })
        history = {"patient_name": "评测张三", "visit_type": "复诊", "specialty": "内科"}
        loop = BookingToolLoop(
            FakeListChatModel(responses=[payload]),
            finder,
            on_finish=lambda observation, hist: "[REPLY][预约助手]预约成功",
        )

        tokens = "".join(await collect(loop.run("挂内科复诊", history)))

        assert service.available_calls, "应查询医生可用性"
        start, end = service.available_calls[0][1], service.available_calls[0][2]
        assert (end - start).total_seconds() == 15 * 60
        assert loop.last_observation["status"] != "invalid_time"
        assert "就诊时间没能识别" not in tokens

    @pytest.mark.asyncio
    async def test_consult_null_duration_uses_twenty_minutes(self):
        finder, service = _real_finder()
        payload = action("find_doctor", arguments={
            "specialty": "内科",
            "start_time": "2026-11-20 15:00",
            "duration_minutes": None,
            "doctor_name": None,
        })
        history = {"patient_name": "评测张三", "visit_type": "咨询", "specialty": "内科"}
        loop = BookingToolLoop(
            FakeListChatModel(responses=[payload]),
            finder,
            on_finish=lambda observation, hist: "[REPLY][预约助手]预约成功",
        )

        tokens = "".join(await collect(loop.run("挂内科咨询", history)))

        start, end = service.available_calls[0][1], service.available_calls[0][2]
        assert (end - start).total_seconds() == 20 * 60
        assert loop.last_observation["status"] != "invalid_time"
        assert "就诊时间没能识别" not in tokens


class TestFinishGate:
    """finish 前要有姓名、就诊类型、科室、开始时间。缺了只问一句，不落库。"""

    @pytest.mark.asyncio
    async def test_direct_finish_without_slots_asks_specialty(self):
        called = []

        async def on_finish(observation, history):
            called.append(True)
            return "[REPLY][预约助手]缺少患者姓名"

        loop = BookingToolLoop(
            FakeListChatModel(responses=[action("finish")]),
            StubFinder(),
            on_finish=on_finish,
        )

        tokens = "".join(await collect(loop.run("帮我预约", {})))

        assert called == []
        assert "科室" in tokens
        assert "缺少患者姓名" not in tokens

    @pytest.mark.asyncio
    async def test_finish_without_start_time_asks_instead_of_saving(self):
        called = []

        async def on_finish(observation, history):
            called.append(True)
            return "[REPLY][预约助手]缺少患者姓名"

        loop = BookingToolLoop(
            FakeListChatModel(responses=[action("finish")]),
            StubFinder(),
            on_finish=on_finish,
        )
        history = {
            "patient_name": "评测张三",
            "visit_type": "初诊",
            "specialty": "内科",
        }

        tokens = "".join(await collect(loop.run("就这样约", history)))

        assert called == []
        assert "时间" in tokens
        assert "缺少患者姓名" not in tokens

    @pytest.mark.asyncio
    async def test_finish_without_a_successful_find_searches_then_offers_slots(self):
        finder = StubFinder(result={
            "status": "unavailable",
            "requested_doctor": {"id": 1, "name": "陈安"},
            "alternatives": [],
        })
        offered = [
            {"start_time": "2026-11-20 17:00", "duration_minutes": 30, "label": "2026-11-20 17:00"},
            {"start_time": "2026-11-23 09:00", "duration_minutes": 30, "label": "2026-11-23 09:00"},
        ]
        called = []

        async def on_finish(observation, history):
            called.append(observation)
            doctor = (observation or {}).get("doctor")
            if not doctor:
                return "[REPLY][预约助手]还不能结束，我还没有查到可约的医生。"
            return "[REPLY][预约助手]预约成功"

        loop = BookingToolLoop(
            FakeListChatModel(responses=[action("finish", arguments={
                "specialty": "内科",
                "start_time": "2026-11-20 15:00",
                "duration_minutes": 30,
                "doctor_name": "陈安",
            })]),
            finder,
            on_finish=on_finish,
            slot_search=lambda history: offered,
        )
        history = {
            "patient_name": "评测赵六",
            "specialty": "内科",
            "visit_type": "初诊",
            "start_time": "2026-11-20 15:00",
            "doctor_name": "陈安",
        }

        tokens = "".join(await collect(loop.run(
            "我是评测赵六，想挂内科初诊，时间2026-11-20 15:00，门诊，指定医生陈安。",
            history,
        )))

        assert finder.calls, "finish 没有查过医生时必须先 find"
        assert finder.calls[0]["doctor_name"] == "陈安"
        assert finder.calls[0]["start_time_str"] == "2026-11-20 15:00"
        assert called == []
        assert "还不能结束" not in tokens
        assert "可以改到" in tokens
        assert "2026-11-20 17:00" in tokens
        assert "2026-11-20 15:00" not in tokens.split("可以改到", 1)[-1]


class TestSingleAskCopy:
    """空 ask_user 与多槽原文都只问最缺的一槽，且不使用禁句。"""

    @pytest.mark.asyncio
    async def test_empty_ask_uses_specialty_question_only(self):
        loop = BookingToolLoop(
            FakeListChatModel(responses=[action("ask_user", message="")]),
            StubFinder(),
        )

        tokens = "".join(await collect(loop.run("我想预约", {})))

        assert "耳鼻喉科" in tokens
        assert "我不能判断症状或开具用药" in tokens
        assert "比如内科、外科或儿科" not in tokens
        assert "请补充就诊预约需要的信息" not in tokens
        assert "姓名" not in tokens

    @pytest.mark.asyncio
    async def test_model_multi_ask_is_replaced_by_specialty_question(self):
        loop = BookingToolLoop(
            FakeListChatModel(responses=[action(
                "ask_user", message="请问姓名和是初诊还是复诊"
            )]),
            StubFinder(),
        )

        tokens = "".join(await collect(loop.run("我想预约", {})))

        assert "耳鼻喉科" in tokens
        assert "比如内科、外科或儿科" not in tokens
        assert "请问患者的姓名是" not in tokens

    @pytest.mark.asyncio
    async def test_empty_ask_with_specialty_asks_the_next_slot(self):
        loop = BookingToolLoop(
            FakeListChatModel(responses=[action("ask_user", message="")]),
            StubFinder(),
        )

        tokens = "".join(await collect(loop.run("挂内科", {"specialty": "内科"})))

        assert tokens == "[REPLY][预约助手]请问患者的姓名是？"
        assert "请补充就诊预约需要的信息" not in tokens

    @pytest.mark.asyncio
    async def test_hollow_specialty_ask_is_replaced_with_the_menu(self):
        loop = BookingToolLoop(
            FakeListChatModel(responses=[action(
                "ask_user", message="请问您想挂哪个科室？"
            )]),
            StubFinder(),
        )

        tokens = "".join(await collect(loop.run("我咳嗽两天，有点头疼", {})))

        assert "请问您想挂哪个科室" not in tokens
        assert tokens == f"[REPLY][预约助手]{MessageBuilder.specialty_menu_question()}"
        for name in DEPARTMENT_CHOICES:
            assert name in tokens

    @pytest.mark.asyncio
    async def test_specialty_mention_without_the_menu_is_rewritten(self):
        loop = BookingToolLoop(
            FakeListChatModel(responses=[action(
                "ask_user", message="请问您想挂哪个科室？"
            )]),
            StubFinder(),
        )
        history = {
            "patient_name": "评测李四",
            "specialty": "内科",
            "visit_type": "初诊",
            "start_time": "2026-11-22 11:00",
        }

        tokens = "".join(await collect(loop.run("就这个科室", history)))

        assert "请问您想挂哪个科室" not in tokens
        assert "耳鼻喉科" in tokens
        assert "我不能判断症状或开具用药" in tokens

    @pytest.mark.asyncio
    async def test_confirmation_that_mentions_specialty_is_kept(self):
        loop = BookingToolLoop(
            FakeListChatModel(responses=[action(
                "ask_user",
                message="请确认是否按已记下的姓名、科室、就诊类型和时间预约。",
            )]),
            StubFinder(),
        )
        history = {
            "patient_name": "评测李四",
            "specialty": "内科",
            "visit_type": "初诊",
            "start_time": "2026-11-22 11:00",
        }

        tokens = "".join(await collect(loop.run("是", history)))

        assert "请确认是否按已记下的姓名、科室、就诊类型和时间预约。" in tokens
        assert "耳鼻喉科" not in tokens
