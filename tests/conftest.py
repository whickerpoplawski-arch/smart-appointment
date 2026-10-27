"""测试公共夹具与假实现"""

import pytest


@pytest.fixture
def temp_db_path(tmp_path):
    """每个测试用独立的临时 SQLite 数据库，避免污染真实数据"""
    return f"sqlite:///{tmp_path / 'appointment_test.db'}"


class FakeDoctorService:
    """就诊服务假实现，只覆盖 DoctorFinder 需要的方法"""

    def __init__(self, doctors=None, department_map=None, available_ids=None):
        self.doctors = doctors or []
        self.department_map = department_map or {}
        if available_ids is None:
            available_ids = [i["id"] for i in self.doctors]
        self.available_ids = set(available_ids)
        self.available_calls = []

    def get_doctor_by_name(self, name):
        for doctor in self.doctors:
            if doctor["name"] == name:
                return doctor
        return None

    def get_all_doctors(self):
        return list(self.doctors)

    def get_doctors_by_department(self, department):
        return [item for item in self.doctors if item.get("department") == department]

    def get_doctors_by_specialty(self, specialty):
        department = self.match_department(specialty)
        if not department:
            return []
        return self.get_doctors_by_department(department)

    def match_department(self, specialty):
        if not specialty:
            return None
        if specialty in self.department_map:
            return self.department_map[specialty]
        names = sorted(
            {item.get("department") for item in self.doctors if item.get("department")},
            key=len,
            reverse=True,
        )
        for name in names:
            if name in str(specialty):
                return name
        return None

    def is_doctor_available(self, doctor_id, start_time, end_time):
        self.available_calls.append((doctor_id, start_time, end_time))
        return doctor_id in self.available_ids

    def get_available_doctors(self, start_time, end_time, department=None):
        return [
            item for item in self.doctors
            if item["id"] in self.available_ids
            and (department is None or item.get("department") == department)
        ]
