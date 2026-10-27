"""医生匹配逻辑测试"""

from datetime import datetime

import pytest

from agents.appointment.doctor_finder import DoctorFinder
from tests.conftest import FakeDoctorService

START = "2026-10-27 15:00"
DURATION = "45"

DOCTORS = [
    {"id": 1, "name": "张伟", "department": "内科", "bio": "演示人设，非真实医师。匹配用专长词：内科。"},
    {"id": 2, "name": "王强", "department": "内科", "bio": "演示人设，非真实医师。匹配用专长词：内科。"},
    {"id": 3, "name": "李娜", "department": "外科", "bio": "演示人设，非真实医师。匹配用专长词：外科。"},
]


def make_finder(available_ids=(1, 2, 3), department_map=None):
    service = FakeDoctorService(
        doctors=DOCTORS,
        department_map=department_map or {},
        available_ids=available_ids,
    )
    return DoctorFinder(appointment_service=service), service


def history(**overrides):
    base = {
        "patient_name": "张三",
        "specialty": "内科",
        "visit_type": "初诊",
        "channel": "门诊",
        "start_time": START,
        "duration": DURATION,
        "doctor_name": "未知",
        "preference": "未知",
    }
    base.update(overrides)
    return base


class TestDoctorFinder:
    def test_should_match_available_interviewer_by_position(self):
        finder, _ = make_finder()

        result = finder.find_doctor_with_thought(history())

        assert result["status"] == "available"
        assert result["name"] in {"张伟", "王强"}
        assert result["requires_confirmation"] is False

    def test_should_book_named_interviewer_when_free(self):
        finder, _ = make_finder()

        result = finder.find_doctor_with_thought(history(doctor_name="李娜"))

        assert result["status"] == "available"
        assert result["name"] == "李娜"
        assert result["id"] == 3

    def test_should_recommend_same_department_interviewer_when_named_is_busy(self):
        finder, _ = make_finder(available_ids=(2, 3))

        result = finder.find_doctor_with_thought(history(doctor_name="张伟"))

        assert result["status"] == "recommendation"
        assert result["original_doctor"]["name"] == "张伟"
        assert result["recommended_doctor"]["department"] == "内科"
        assert result["recommended_doctor"]["id"] == 2
        assert result["requires_confirmation"] is True

    def test_should_report_conflict_when_no_same_department_interviewer(self):
        finder, _ = make_finder(available_ids=(3,))

        result = finder.find_doctor_with_thought(history(doctor_name="张伟"))

        assert result["status"] == "conflict"
        assert result["original_doctor"]["name"] == "张伟"

    def test_should_report_unknown_interviewer(self):
        finder, _ = make_finder()

        result = finder.find_doctor_with_thought(history(doctor_name="不存在"))

        assert result["status"] == "doctor_not_found"

    def test_should_report_no_available_doctor(self):
        finder, _ = make_finder(available_ids=())

        result = finder.find_doctor_with_thought(history())

        assert result["status"] == "no_available_doctor"
        assert result["department"] == "内科"

    def test_should_report_invalid_time(self):
        finder, _ = make_finder()

        result = finder.find_doctor_with_thought(history(start_time="未知"))

        assert result["status"] == "invalid_time"

    def test_should_not_assign_a_doctor_when_only_a_symptom_is_given(self):
        finder, _ = make_finder()

        result = finder.find_doctor_with_thought(history(specialty="咳嗽两天"))

        assert result["status"] == "needs_specialty"
        assert "name" not in result

    @pytest.mark.parametrize("raw,expected", [
        ("1小时", 60),
        ("一小时", 60),
        ("半小时", 30),
        ("45分钟", 45),
    ])
    def test_should_parse_time_and_duration(self, raw, expected):
        finder, _ = make_finder()

        start, end, minutes = finder.parse_time_and_duration(START, raw)

        assert minutes == expected
        assert start.strftime("%Y-%m-%d %H:%M") == START
        assert (end - start).total_seconds() == expected * 60
