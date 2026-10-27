"""就诊预约 Agent 的解析、状态与主流程测试"""

import json

import pytest
from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agents.appointment.input_parser import InputParser
from agents.appointment.appointment_processor import AppointmentProcessor
from agents.appointment.message_builder import MessageBuilder


def make_parser():
    """构造只用于解析 JSON 的 InputParser，不初始化 LLM 链"""
    return object.__new__(InputParser)


def make_processor():
    return AppointmentProcessor(None, None, MessageBuilder(), None, None)


class TestInputParsing:
    def test_should_parse_valid_json(self):
        parser = make_parser()
        payload = json.dumps({
            "patient_name": "张三",
            "specialty": "内科",
            "visit_type": "初诊",
            "channel": "线下",
            "start_time": "2026-10-27 15:00",
            "duration": "45分钟",
            "doctor_name": "未知",
            "preference": "未知",
            "confirmation": "未知",
            "unrelated": False,
        }, ensure_ascii=False)

        result = parser.parse_data(payload)

        assert result["patient_name"] == "张三"
        assert result["specialty"] == "内科"
        assert result["start_time"] == "2026-10-27 15:00"
        assert result["unrelated"] is False

    def test_should_return_safe_defaults_for_invalid_json(self):
        parser = make_parser()

        result = parser.parse_data("这不是 JSON")

        assert result["patient_name"] == "未知"
        assert result["specialty"] == "未知"
        assert result["visit_type"] == "未知"
        assert result["start_time"] == "未知"
        assert result["unrelated"] is False

    def test_should_stream_parse_with_fake_llm(self):
        fake_llm = FakeListChatModel(responses=[
            '{"patient_name": "李四", "specialty": "外科", "visit_type": "复诊", '
            '"channel": "互联网医院", "start_time": "2026-10-28 10:00", "duration": "未知", '
            '"doctor_name": "未知", "preference": "未知", "confirmation": "未知", '
            '"unrelated": false}'
        ])
        parser = InputParser(fake_llm)

        content = "".join(parser.parse_stream("约明天上午十点二面前端", InMemoryChatMessageHistory()))
        result = parser.parse_data(content)

        assert result["patient_name"] == "李四"
        assert result["visit_type"] == "复诊"


class TestHistoryManagement:
    def test_should_accumulate_info_across_turns(self):
        processor = make_processor()
        history = {}

        assert processor.update_history_from_data(history, {"patient_name": "张三"}) is False
        assert history["patient_name"] == "张三"
        assert history.get("specialty") is None

        assert processor.update_history_from_data(history, {"specialty": "内科"}) is False
        assert processor.update_history_from_data(
            history, {"visit_type": "首诊", "start_time": "2026-10-27 15:00"}
        ) is True
        assert history["visit_type"] == "初诊"
        assert history["patient_name"] == "张三"

    def test_should_not_let_unknown_overwrite_known_values(self):
        processor = make_processor()
        history = {"patient_name": "张三"}

        processor.update_history_from_data(history, {"patient_name": "未知"})

        assert history["patient_name"] == "张三"

    def test_missing_info_question_asks_specialty_before_name(self):
        builder = MessageBuilder()

        text = builder.create_missing_info_questions([
            "patient_name", "specialty", "visit_type", "start_time",
        ])

        assert "请问要挂哪个科室？比如内科、外科或儿科。" in text
        assert "请问患者的姓名是" not in text
        assert "初诊、复诊还是咨询" not in text

    def test_should_require_patient_name_position_round_and_time(self):
        processor = make_processor()
        history = {}

        assert processor.update_history_from_data(history, {
            "patient_name": "张三",
            "specialty": "内科",
            "visit_type": "初诊",
        }) is False
        assert processor.update_history_from_data(history, {
            "start_time": "2026-10-27 15:00"
        }) is True

    @pytest.mark.parametrize("visit_name,expected", [
        ("初诊", 30),
        ("复诊", 15),
        ("咨询", 20),
        ("首诊", 30),
        ("未知", 30),
    ])
    def test_should_resolve_duration_by_visit_type(self, visit_name, expected):
        assert AppointmentProcessor.resolve_duration(visit_name, "未知") == expected

    @pytest.mark.parametrize("duration,expected", [
        ("60分钟", 60),
        ("1小时", 60),
        ("半小时", 30),
        ("90", 90),
    ])
    def test_should_parse_explicit_duration(self, duration, expected):
        assert AppointmentProcessor.resolve_duration("初诊", duration) == expected

    def test_should_default_mode_to_online(self):
        processor = make_processor()
        history = {"channel": None, "visit_type": "初诊", "duration": None}

        processor._prepare_defaults(history)

        assert history["channel"] == "门诊"
        assert history["duration"] == "30分钟"

    @pytest.mark.parametrize("raw,expected", [
        ("视频", "互联网医院"),
        ("到院", "门诊"),
        ("现场", "门诊"),
    ])
    def test_should_normalize_channel(self, raw, expected):
        assert AppointmentProcessor.normalize_channel(raw) == expected


class TestRecommendationConfirmation:
    def test_should_confirm_recommended_doctor(self):
        processor = make_processor()
        recommended = {"id": 2, "name": "王强", "department": "内科", "bio": "演示人设"}
        history = {
            "recommended_doctor": recommended,
            "awaiting_confirmation": True,
        }

        finished = processor.update_history_from_data(history, {"confirmation": "可以"})

        assert finished is True
        assert history["confirmed_doctor"] == recommended
        assert history["awaiting_confirmation"] is False

    def test_should_mark_declined(self):
        processor = make_processor()
        history = {
            "recommended_doctor": {"id": 2, "name": "王强"},
            "awaiting_confirmation": True,
        }

        finished = processor.update_history_from_data(history, {"confirmation": "不要，换一个"})

        assert finished is True
        assert history["recommendation_declined"] is True
        assert "confirmed_doctor" not in history

    def test_should_wait_for_unclear_reply(self):
        processor = make_processor()
        history = {
            "recommended_doctor": {"id": 2, "name": "王强"},
            "awaiting_confirmation": True,
        }

        assert processor.update_history_from_data(history, {"confirmation": "未知"}) is False
        assert history["awaiting_confirmation"] is True
