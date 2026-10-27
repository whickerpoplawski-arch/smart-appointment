"""页面模板与 API 路径：字段跟路由一致，不留旧 URL。"""

from api.appointment import AppointmentCreateRequest, AvailabilityResponse, router as appointment_router
from api.doctor import router as doctor_router
from config.constants import DEMO_DISCLAIMER
from db.models import Appointment
from fastapi.templating import Jinja2Templates
from web.routes import router as web_router


def _paths(router):
    return [route.path for route in router.routes]


def test_routes_use_doctor_paths_and_drop_interview_aliases():
    paths = _paths(appointment_router) + _paths(doctor_router) + _paths(web_router)

    assert any(path.startswith("/api/appointments") for path in paths)
    assert any(path.startswith("/api/doctors") for path in paths)
    assert "/doctor" in paths
    assert "/doctor_schedule" in paths
    assert "/patient_analysis" in paths
    assert not any("interview" in path for path in paths)


def test_create_request_defaults_to_clinic_channel():
    request = AppointmentCreateRequest(
        patient_name="张三",
        specialty="内科",
        visit_type="初诊",
        start_time="2026-10-28 10:00",
        end_time="2026-10-28 10:30",
        doctor_id=1,
    )

    assert request.channel == "门诊"
    assert "available_doctors" in AvailabilityResponse.model_fields
    assert "available_interviewers" not in AvailabilityResponse.model_fields
    assert Appointment.__table__.c.channel.default.arg == "门诊"


def test_pages_render_doctors_departments_and_disclaimer():
    templates = Jinja2Templates(directory="web/templates")
    doctor = {
        "id": 1,
        "name": "陈安",
        "department": "内科",
        "bio": "演示人设，非真实医师",
    }
    appointment = {
        "patient_name": "张三",
        "specialty": "内科",
        "visit_type": "初诊",
        "channel": "门诊",
        "doctor_name": "陈安",
        "start_time": "2026-10-28 10:00",
        "status": "booked",
    }
    stats = {
        "doctors": 20,
        "departments": 10,
        "appointments": 1,
        "appointments_today": 1,
        "cancelled_appointments": 0,
        "knowledge_documents": 8,
        "categories": 8,
    }
    schedule = [{
        "id": 1,
        "name": "陈安",
        "department": "内科",
        "utilization": 10,
        "busy_periods": [{
            "start": "10:00",
            "end": "10:30",
            "patient_name": "张三",
            "specialty": "内科",
            "visit_type": "初诊",
            "channel": "门诊",
        }],
    }]
    pages = {
        "index.html": {},
        "doctor.html": {"doctors": [doctor]},
        "doctor_schedule.html": {
            "schedule": schedule,
            "current_date": "2026-10-28",
            "work_start_hour": 9,
            "work_end_hour": 18,
        },
        "admin_dashboard.html": {
            "stats": stats,
            "doctors": [doctor],
            "recent_appointments": [appointment],
        },
        "database_admin.html": {"stats": stats},
        "patient_analysis.html": {},
        "knowledge_management.html": {"documents": [], "categories": []},
    }

    for name, context in pages.items():
        html = templates.get_template(name).render(**context)
        assert DEMO_DISCLAIMER in html, name
        assert "interviewers" not in html, name
        assert "/api/interviews" not in html, name
        assert "一面" not in html, name

    doctor_html = templates.get_template("doctor.html").render(doctors=[doctor])
    assert "陈安" in doctor_html
    assert ">内科<" in doctor_html or "内科" in doctor_html

    admin = templates.get_template("admin_dashboard.html").render(
        stats=stats, doctors=[doctor], recent_appointments=[appointment]
    )
    assert "20" in admin
    assert "初诊" in admin
    assert "门诊" in admin

    database = templates.get_template("database_admin.html").render(stats=stats)
    assert "<code>doctors</code>" in database
    assert "<code>appointments</code>" in database

    schedule_html = templates.get_template("doctor_schedule.html").render(
        schedule=schedule,
        current_date="2026-10-28",
        work_start_hour=9,
        work_end_hour=18,
    )
    assert "初诊" in schedule_html
    assert "门诊" in schedule_html
