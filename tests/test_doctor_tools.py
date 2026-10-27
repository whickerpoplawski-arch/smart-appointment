"""find_doctor 工具层测试：参数校验、四态返回、替代者上限与排序"""

import pytest

from agents.appointment.doctor_finder import DoctorFinder, ALTERNATIVES_LIMIT
from agents.appointment.doctor_tools import find_doctor, FIND_DOCTOR_SCHEMA
from tests.conftest import FakeDoctorService

START = "2026-10-27 15:00"

DOCTORS = [
    {"id": 1, "name": "张伟", "department": "内科", "bio": "演示人设，非真实医师。匹配用专长词：内科。"},
    {"id": 2, "name": "王强", "department": "内科", "bio": "演示人设，非真实医师。匹配用专长词：内科。"},
    {"id": 3, "name": "李娜", "department": "内科", "bio": "演示人设，非真实医师。匹配用专长词：内科。"},
    {"id": 4, "name": "赵敏", "department": "内科", "bio": "演示人设，非真实医师。匹配用专长词：内科。"},
    {"id": 5, "name": "孙悦", "department": "内科", "bio": "演示人设，非真实医师。匹配用专长词：内科。"},
    {"id": 6, "name": "周洁", "department": "外科", "bio": "演示人设，非真实医师。匹配用专长词：外科。"},
]


def make_finder(available_ids=(1, 2, 3, 4, 5, 6)):
    service = FakeDoctorService(doctors=DOCTORS, available_ids=available_ids)
    return DoctorFinder(appointment_service=service)


def args(**overrides):
    base = {"specialty": "内科", "start_time": START,
            "duration_minutes": 30, "doctor_name": None}
    base.update(overrides)
    return base


class TestArgumentValidation:
    def test_should_reject_missing_keys(self):
        finder = make_finder()

        result = find_doctor({"specialty": "内科"}, finder)

        assert result["status"] == "invalid_arguments"
        assert "缺少" in result["reason"]

    def test_should_reject_extra_keys(self):
        finder = make_finder()

        result = find_doctor({**args(), "preference": "找个 senior"}, finder)

        assert result["status"] == "invalid_arguments"
        assert "多出" in result["reason"]

    def test_should_reject_out_of_range_duration(self):
        finder = make_finder()

        result = find_doctor(args(duration_minutes=600), finder)

        assert result["status"] == "invalid_arguments"
        assert "480" in result["reason"]

    def test_should_reject_duration_below_floor(self):
        finder = make_finder()

        result = find_doctor(args(duration_minutes=10), finder)

        assert result["status"] == "invalid_arguments"
        assert "30" in result["reason"]

    def test_should_reject_null_start_time(self):
        finder = make_finder()

        result = find_doctor(args(start_time=None), finder)

        assert result["status"] == "invalid_arguments"

    def test_should_use_default_duration_when_not_given(self):
        finder = make_finder(available_ids=(1,))

        result = find_doctor(args(duration_minutes=None), finder, default_duration=60)

        assert result["status"] == "available"

    def test_schema_keys_match_argument_contract(self):
        properties = FIND_DOCTOR_SCHEMA["function"]["parameters"]["properties"]

        assert set(properties) == {"specialty", "start_time", "duration_minutes", "doctor_name"}
        assert set(FIND_DOCTOR_SCHEMA["function"]["parameters"]["required"]) == set(properties)


class TestFourStates:
    def test_should_return_available_when_named_interviewer_is_free(self):
        finder = make_finder()

        result = find_doctor(args(doctor_name="张伟"), finder)

        assert result["status"] == "available"
        assert result["doctor"]["name"] == "张伟"

    def test_should_return_unavailable_with_alternatives_when_named_is_busy(self):
        finder = make_finder(available_ids=(2, 3))

        result = find_doctor(args(doctor_name="张伟"), finder)

        assert result["status"] == "unavailable"
        assert result["requested_doctor"]["name"] == "张伟"
        assert result["alternatives"]
        assert all(item["department"] == "内科" for item in result["alternatives"])
        assert all(item["id"] != 1 for item in result["alternatives"])

    def test_should_return_unavailable_with_empty_alternatives_when_no_same_department_free(self):
        finder = make_finder(available_ids=(6,))

        result = find_doctor(args(doctor_name="张伟"), finder)

        assert result["status"] == "unavailable"
        assert result["alternatives"] == []

    def test_should_return_not_found_for_unknown_name(self):
        finder = make_finder()

        result = find_doctor(args(doctor_name="小王"), finder)

        assert result["status"] == "not_found"
        assert result["requested_doctor_name"] == "小王"

    def test_should_return_no_match_when_nobody_free(self):
        finder = make_finder(available_ids=())

        result = find_doctor(args(), finder)

        assert result["status"] == "no_match"
        assert result["department"] == "内科"
        assert result["searched_specialty"] == "内科"
        assert not result.get("needs_specialty")

    def test_should_ask_when_specialty_is_only_a_symptom(self):
        finder = make_finder(available_ids=(6,))

        result = find_doctor(args(specialty="咳嗽两天"), finder)

        assert result["status"] == "no_match"
        assert result["needs_specialty"] is True
        assert result.get("doctor") is None

    def test_should_return_invalid_time_for_unparseable_start_time(self):
        finder = make_finder()

        result = find_doctor(args(start_time="明天下午"), finder)

        assert result["status"] == "invalid_time"


class TestAlternativesRules:
    def test_should_cap_alternatives_at_limit(self):
        finder = make_finder(available_ids=(2, 3, 4, 5))

        result = find_doctor(args(doctor_name="张伟"), finder)

        assert len(result["alternatives"]) == ALTERNATIVES_LIMIT
        assert ALTERNATIVES_LIMIT == 3

    def test_should_exclude_requested_doctor_from_alternatives(self):
        """即使底层空闲查询把本人带了回来，替代名单也必须排除本人"""
        finder = make_finder(available_ids=(2, 3))
        finder.appointment_service.get_available_doctors = \
            lambda start, end, department=None: list(DOCTORS[:3])

        result = find_doctor(args(doctor_name="张伟"), finder)

        assert result["status"] == "unavailable"
        assert 1 not in {item["id"] for item in result["alternatives"]}
