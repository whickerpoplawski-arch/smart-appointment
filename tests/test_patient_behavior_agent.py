"""患者偏好分析测试"""

import pytest

from agents.patient_behavior.pattern_analyzer import PatientPatternAnalyzer
from services.patient_behavior_service import PatientBehaviorService
from services.doctor_service import DoctorService

CANDIDATE = "default_patient"


@pytest.fixture
def behavior_service(temp_db_path):
    return PatientBehaviorService(db_path=temp_db_path)


@pytest.fixture
def doctor_id(temp_db_path):
    service = DoctorService(db_path=temp_db_path)
    return service.add_doctor("陈安", department="内科", bio="演示人设，非真实医师")


def record(behavior_service, doctor_id, **overrides):
    data = {
        "specialty": "内科",
        "visit_type": "初诊",
        "channel": "门诊",
        "duration": 45,
        "start_time": "2026-10-27 15:00:00",
    }
    data.update(overrides)
    return behavior_service.record_behavior(
        user_id=CANDIDATE,
        action_type="appointment",
        action_data=data,
        doctor_id=doctor_id,
        session_id="session-1",
    )


class TestPatientBehavior:
    def test_should_record_appointment_behavior(self, behavior_service, doctor_id):
        assert record(behavior_service, doctor_id) is True

        behaviors = behavior_service.get_patient_behaviors(CANDIDATE, action_type="appointment")

        assert len(behaviors) == 1
        assert behaviors[0]["action_type"] == "appointment"
        assert behaviors[0]["action_data"]["specialty"] == "内科"

    def test_should_analyze_favorite_position_round_and_mode(self, behavior_service, doctor_id):
        record(behavior_service, doctor_id)
        record(behavior_service, doctor_id)
        record(behavior_service, doctor_id, specialty="外科", visit_type="复诊", channel="互联网医院")

        analyzer = PatientPatternAnalyzer(behavior_service)
        analysis = analyzer.analyze_patient_preferences(CANDIDATE)

        assert analysis["favorite_position"] == "内科"
        assert analysis["favorite_round"] == "初诊"
        assert analysis["favorite_mode"] == "门诊"
        assert analysis["total_appointments"] == 3
        assert analysis["favorite_doctor_id"] == doctor_id

    def test_should_analyze_preferred_time_period(self, behavior_service, doctor_id):
        record(behavior_service, doctor_id, start_time="2026-10-27 09:30:00")
        record(behavior_service, doctor_id, start_time="2026-10-28 10:00:00")

        analyzer = PatientPatternAnalyzer(behavior_service)
        analysis = analyzer.analyze_patient_preferences(CANDIDATE)

        assert analysis["preferred_time_period"] == "上午"

    def test_should_return_none_without_history(self, behavior_service):
        analyzer = PatientPatternAnalyzer(behavior_service)

        assert analyzer.analyze_patient_preferences(CANDIDATE) is None

    def test_should_update_and_read_preferences(self, behavior_service):
        assert behavior_service.update_patient_preference(
            CANDIDATE, "specialty", "内科"
        ) is True
        assert behavior_service.update_patient_preference(
            CANDIDATE, "specialty", "内科"
        ) is True

        preferences = behavior_service.get_patient_preferences(CANDIDATE)

        assert any(
            item["preference_type"] == "specialty" and item["preference_value"] == "内科"
            for item in preferences
        )

    def test_should_build_statistics(self, behavior_service, doctor_id):
        record(behavior_service, doctor_id)

        stats = behavior_service.get_patient_statistics(CANDIDATE)

        assert stats["total_behaviors"] == 1
        assert stats["interview_count"] == 1
