"""就诊预约服务层测试：种子数据、事务落库、冲突与行为记录"""

from datetime import datetime, timedelta

import pytest

from agents.appointment.appointment_database import AppointmentDatabase
from services.patient_behavior_service import PatientBehaviorService
from services.appointment_service import AppointmentService
from services.doctor_service import DoctorService

START = datetime(2026, 10, 27, 15, 0)


@pytest.fixture
def services(temp_db_path):
    doctor_service = DoctorService(db_path=temp_db_path)
    doctor_service.initialize_default_doctors()
    appointment_service = AppointmentService(db_path=temp_db_path)
    behavior_service = PatientBehaviorService(db_path=temp_db_path)
    return doctor_service, appointment_service, behavior_service


def appointment_info(**overrides):
    info = {
        "patient_name": "张三",
        "specialty": "内科",
        "visit_type": "初诊",
        "channel": "门诊",
    }
    info.update(overrides)
    return info


class TestInterviewerSeeding:
    def test_should_seed_ten_interviewers(self, services):
        doctor_service, _, _ = services

        doctors = doctor_service.get_all_doctors()

        assert len(doctors) == 20
        departments = {item["department"] for item in doctors}
        assert departments == {
            "内科", "外科", "骨科", "儿科", "皮肤科", "眼科", "耳鼻喉科", "口腔科", "妇产科", "中医科"
        }
        assert all(sum(item["department"] == name for item in doctors) >= 2 for name in departments)

    def test_should_be_idempotent(self, services):
        doctor_service, _, _ = services

        assert doctor_service.initialize_default_doctors() is True
        assert len(doctor_service.get_all_doctors()) == 20

    def test_should_match_department_from_specialty_only(self, services):
        _, appointment_service, _ = services

        assert appointment_service.match_department("内科") == "内科"
        assert appointment_service.match_department("我想约内科") == "内科"
        assert appointment_service.match_department("咳嗽两天") is None
        assert appointment_service.match_department("神秘岗位") is None


class TestInterviewBooking:
    def test_should_save_appointment_and_busy_schedule(self, services):
        doctor_service, appointment_service, _ = services
        doctor = doctor_service.get_all_doctors()[0]
        end = START + timedelta(minutes=45)

        assert appointment_service.save_appointment(
            doctor["id"], START, end, appointment_info(), "session-1"
        ) is True

        appointments = appointment_service.get_all_appointments()
        assert len(appointments) == 1
        assert appointments[0]["patient_name"] == "张三"
        assert appointments[0]["visit_type"] == "初诊"
        assert appointments[0]["specialty"] == "内科"
        assert appointments[0]["channel"] == "门诊"
        assert appointments[0]["status"] == "booked"

        assert appointment_service.is_doctor_available(doctor["id"], START, end) is False

    def test_should_reject_conflicting_booking_without_partial_record(self, services):
        doctor_service, appointment_service, _ = services
        doctor = doctor_service.get_all_doctors()[0]
        end = START + timedelta(minutes=45)

        assert appointment_service.save_appointment(
            doctor["id"], START, end, appointment_info(), "session-1"
        ) is True
        assert appointment_service.save_appointment(
            doctor["id"], START, end, appointment_info(patient_name="李四"), "session-2"
        ) is False

        appointments = appointment_service.get_all_appointments()
        assert len(appointments) == 1
        assert appointments[0]["patient_name"] == "张三"

    def test_should_reject_missing_required_fields(self, services):
        doctor_service, appointment_service, _ = services
        doctor = doctor_service.get_all_doctors()[0]
        end = START + timedelta(minutes=45)

        assert appointment_service.save_appointment(
            doctor["id"], START, end, appointment_info(patient_name=""), "session-1"
        ) is False
        assert appointment_service.get_all_appointments() == []

    def test_should_reject_unknown_interviewer(self, services):
        _, appointment_service, _ = services
        end = START + timedelta(minutes=45)

        assert appointment_service.save_appointment(
            9999, START, end, appointment_info(), "session-1"
        ) is False
        assert appointment_service.get_all_appointments() == []

    def test_should_cancel_and_free_schedule(self, services):
        doctor_service, appointment_service, _ = services
        doctor = doctor_service.get_all_doctors()[0]
        end = START + timedelta(minutes=45)
        appointment_service.save_appointment(
            doctor["id"], START, end, appointment_info(), "session-1"
        )
        appointment_id = appointment_service.get_all_appointments()[0]["id"]

        assert appointment_service.cancel_appointment(appointment_id) is True
        assert appointment_service.is_doctor_available(doctor["id"], START, end) is True


class TestAppointmentDatabase:
    def test_should_record_patient_behavior(self, services):
        doctor_service, appointment_service, behavior_service = services
        doctor = doctor_service.get_all_doctors()[0]
        database = AppointmentDatabase(
            appointment_service=appointment_service,
            patient_behavior_service=behavior_service,
        )
        end = START + timedelta(minutes=45)

        success, reason = database.save_appointment(
            doctor["id"], START, end, appointment_info(), "session-1"
        )

        assert success is True, reason
        behaviors = behavior_service.get_patient_behaviors("default_patient")
        assert len(behaviors) == 1
        assert behaviors[0]["action_type"] == "appointment"
        assert behaviors[0]["action_data"]["specialty"] == "内科"
        assert behaviors[0]["action_data"]["channel"] == "门诊"

    def test_should_return_reason_when_saving_fails(self, services):
        doctor_service, appointment_service, behavior_service = services
        doctor = doctor_service.get_all_doctors()[0]
        database = AppointmentDatabase(
            appointment_service=appointment_service,
            patient_behavior_service=behavior_service,
        )
        end = START + timedelta(minutes=45)
        database.save_appointment(doctor["id"], START, end, appointment_info(), "session-1")

        success, reason = database.save_appointment(
            doctor["id"], START, end, appointment_info(patient_name="李四"), "session-2"
        )

        assert success is False
        assert reason
