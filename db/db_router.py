from .base import SessionManager
from .repositories import (
    DoctorRepository,
    AppointmentRepository,
    KnowledgeRepository,
    PatientBehaviorRepository,
)
from typing import Optional


class DatabaseRouter:
    """
    数据库路由器

    职责：
    1. 管理数据库连接和会话
    2. 提供统一的数据访问入口
    3. 协调各个Repository的操作
    """

    def __init__(self, db_path: str = 'sqlite:///data/doctor_appointment.db'):
        """
        初始化数据库路由器

        Args:
            db_path: 数据库连接路径
        """
        self.session_manager = SessionManager(db_path)

        # 初始化各个Repository
        self.doctor_repo = DoctorRepository(self.session_manager)
        self.appointment_repo = AppointmentRepository(self.session_manager)
        self.knowledge_repo = KnowledgeRepository(self.session_manager)
        self.patient_behavior_repo = PatientBehaviorRepository(self.session_manager)

    @property
    def doctors(self) -> DoctorRepository:
        """获取医生数据仓库（含日程方法）"""
        return self.doctor_repo

    @property
    def appointments(self) -> AppointmentRepository:
        """获取预约记录数据仓库"""
        return self.appointment_repo

    @property
    def knowledge(self) -> KnowledgeRepository:
        """获取知识库数据仓库"""
        return self.knowledge_repo

    @property
    def patient_behavior(self) -> PatientBehaviorRepository:
        """获取患者行为数据仓库"""
        return self.patient_behavior_repo

    def close(self):
        """关闭数据库连接"""
        self.session_manager.close()


class DoctorDBRouter:
    """
    医生数据库路由器

    对 DatabaseRouter 的医生/日程能力做一层薄封装，方便不关心会话细节的调用方。
    """

    def __init__(self, db_type='local', **kwargs):
        self.db_router = DatabaseRouter(**kwargs)
        self.doctor_repo = self.db_router.doctors

    # 医生相关方法
    def add_doctor(self, name, department=None, bio=None) -> int:
        return self.doctor_repo.add_doctor(name, department, bio)

    def get_doctor_by_name(self, name: str):
        return self.doctor_repo.get_doctor_by_name(name)

    def get_doctor_by_id(self, doctor_id: int):
        return self.doctor_repo.get_doctor_by_id(doctor_id)

    def get_all_doctors(self):
        return self.doctor_repo.get_all_doctors()

    def get_doctors_by_department(self, department: str):
        return self.doctor_repo.get_doctors_by_department(department)

    def get_all_departments(self):
        return self.doctor_repo.get_all_departments()

    # 日程相关方法
    def add_schedule(self, doctor_id: int, start_time, end_time, status, appointment_id=None) -> int:
        return self.doctor_repo.add_schedule(doctor_id, start_time, end_time, status, appointment_id)

    def get_doctor_schedules(self, doctor_id: int, date):
        return self.doctor_repo.get_doctor_schedules(doctor_id, date)

    def get_schedules_by_date(self, date):
        return self.doctor_repo.get_schedules_by_date(date)

    def is_doctor_available(self, doctor_id: int, start_time, end_time) -> bool:
        return self.doctor_repo.is_doctor_available(doctor_id, start_time, end_time)

    def get_available_doctors(self, start_time, end_time, department=None):
        return self.doctor_repo.get_available_doctors(start_time, end_time, department)
