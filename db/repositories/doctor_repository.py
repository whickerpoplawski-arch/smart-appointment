from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy.exc import IntegrityError
from ..base.interfaces import BaseDoctorRepository, BaseScheduleRepository
from ..base.session_manager import SessionManager
from ..models import Doctor, DoctorSchedule


class DoctorRepository(BaseDoctorRepository, BaseScheduleRepository):
    """
    医生数据访问对象

    职责：
    1. 医生信息的CRUD操作
    2. 医生日程的管理
    3. 医生可用性与空闲筛选
    """

    def __init__(self, session_manager: SessionManager):
        """
        初始化医生数据仓库

        Args:
            session_manager: 会话管理器
        """
        self.session_manager = session_manager

    def add_doctor(self, name: str, department: Optional[str] = None, bio: Optional[str] = None) -> int:
        """
        添加新医生

        Args:
            name: 医生姓名（全局唯一）
            department: 科室
            bio: 简介，用于相似度推荐

        Returns:
            新创建的医生ID
        """
        with self.session_manager.session_scope() as session:
            doctor = Doctor(name=name, department=department, bio=bio)
            session.add(doctor)
            session.flush()
            return doctor.id

    def get_doctor_by_id(self, doctor_id: int) -> Optional[Dict[str, Any]]:
        """
        根据ID获取医生信息

        Args:
            doctor_id: 医生ID

        Returns:
            医生信息字典，如果不存在返回None
        """
        with self.session_manager.session_scope() as session:
            doctor = session.query(Doctor).filter(
                Doctor.id == doctor_id
            ).first()

            if not doctor:
                return None

            return self._doctor_to_dict(doctor)

    def get_doctor_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """
        根据姓名获取医生信息

        Args:
            name: 医生姓名

        Returns:
            医生信息字典，如果不存在返回None
        """
        with self.session_manager.session_scope() as session:
            doctor = session.query(Doctor).filter(
                Doctor.name == name
            ).first()

            if not doctor:
                return None

            return self._doctor_to_dict(doctor)

    def get_all_doctors(self) -> List[Dict[str, Any]]:
        """
        获取所有医生信息

        Returns:
            医生信息列表
        """
        with self.session_manager.session_scope() as session:
            doctors = session.query(Doctor).order_by(Doctor.id).all()
            return [self._doctor_to_dict(item) for item in doctors]

    def get_doctors_by_department(self, department: str) -> List[Dict[str, Any]]:
        """
        根据科室获取医生信息

        Args:
            department: 科室，如「后端」

        Returns:
            医生信息列表
        """
        with self.session_manager.session_scope() as session:
            doctors = session.query(Doctor).filter(
                Doctor.department == department
            ).order_by(Doctor.id).all()
            return [self._doctor_to_dict(item) for item in doctors]

    def get_all_departments(self) -> List[str]:
        """
        获取所有科室

        Returns:
            科室列表（去重后）
        """
        with self.session_manager.session_scope() as session:
            departments = session.query(Doctor.department).distinct().all()
            return [item[0] for item in departments if item[0] is not None]

    def update_doctor(self, doctor_id: int, **updates) -> bool:
        """
        更新医生信息

        Args:
            doctor_id: 医生ID
            **updates: 要更新的字段

        Returns:
            更新是否成功
        """
        with self.session_manager.session_scope() as session:
            doctor = session.query(Doctor).filter(
                Doctor.id == doctor_id
            ).first()

            if not doctor:
                return False

            for key, value in updates.items():
                if hasattr(doctor, key):
                    setattr(doctor, key, value)

            return True

    def delete_doctor(self, doctor_id: int) -> bool:
        """
        删除医生

        已有预约记录引用时会因外键约束失败并返回 False，避免产生孤儿记录。

        Args:
            doctor_id: 医生ID

        Returns:
            删除是否成功
        """
        try:
            with self.session_manager.session_scope() as session:
                doctor = session.query(Doctor).filter(
                    Doctor.id == doctor_id
                ).first()

                if not doctor:
                    return False

                session.delete(doctor)
                session.flush()
                return True
        except IntegrityError:
            return False

    # 日程相关方法
    def add_schedule(self, doctor_id: int, start_time: datetime, end_time: datetime,
                    status: str, appointment_id: Optional[int] = None) -> int:
        """
        添加医生日程

        Args:
            doctor_id: 医生ID
            start_time: 开始时间
            end_time: 结束时间
            status: 状态 ('busy' 或 'free')
            appointment_id: 关联就诊ID（忙碌时填写）

        Returns:
            新创建的日程ID
        """
        if end_time <= start_time:
            raise ValueError("日程结束时间必须晚于开始时间")

        with self.session_manager.session_scope() as session:
            schedule = DoctorSchedule(
                doctor_id=doctor_id,
                start_time=start_time,
                end_time=end_time,
                status=status,
                appointment_id=appointment_id
            )
            session.add(schedule)
            session.flush()
            return schedule.id

    def get_doctor_schedules(self, doctor_id: int, date: datetime) -> List[Dict[str, Any]]:
        """
        获取医生指定日期的日程

        采用区间重叠判断，跨天日程也能正确落在第一天。

        Args:
            doctor_id: 医生ID
            date: 查询日期

        Returns:
            日程信息列表
        """
        start = datetime(date.year, date.month, date.day)
        end = start + timedelta(days=1)

        with self.session_manager.session_scope() as session:
            schedules = session.query(DoctorSchedule).filter(
                DoctorSchedule.doctor_id == doctor_id,
                DoctorSchedule.start_time < end,
                DoctorSchedule.end_time > start
            ).order_by(DoctorSchedule.start_time).all()

            return [self._schedule_to_dict(schedule) for schedule in schedules]

    def get_schedules_by_date(self, date: datetime) -> List[Dict[str, Any]]:
        """
        获取所有医生指定日期的日程

        Args:
            date: 查询日期

        Returns:
            日程信息列表（含医生姓名）
        """
        start = datetime(date.year, date.month, date.day)
        end = start + timedelta(days=1)

        with self.session_manager.session_scope() as session:
            schedules = session.query(DoctorSchedule).filter(
                DoctorSchedule.start_time < end,
                DoctorSchedule.end_time > start
            ).order_by(DoctorSchedule.start_time).all()

            return [self._schedule_to_dict(schedule) for schedule in schedules]

    def is_doctor_available(self, doctor_id: int, start_time: datetime, end_time: datetime) -> bool:
        """
        检查医生在指定时间段是否可用

        使用半开区间判断重叠：已有.start < 新.end 且 已有.end > 新.start。

        Args:
            doctor_id: 医生ID
            start_time: 开始时间
            end_time: 结束时间

        Returns:
            是否可用
        """
        if end_time <= start_time:
            return False

        with self.session_manager.session_scope() as session:
            conflict = session.query(DoctorSchedule).filter(
                DoctorSchedule.doctor_id == doctor_id,
                DoctorSchedule.status == "busy",
                DoctorSchedule.start_time < end_time,
                DoctorSchedule.end_time > start_time
            ).first()

            return conflict is None

    def get_available_doctors(self, start_time: datetime, end_time: datetime,
                                   department: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        获取指定时段内空闲的医生

        Args:
            start_time: 开始时间
            end_time: 结束时间
            department: 科室过滤，为空则不限方向

        Returns:
            空闲医生列表
        """
        if end_time <= start_time:
            return []

        with self.session_manager.session_scope() as session:
            busy_ids = [
                row[0] for row in session.query(DoctorSchedule.doctor_id).filter(
                    DoctorSchedule.status == "busy",
                    DoctorSchedule.start_time < end_time,
                    DoctorSchedule.end_time > start_time
                ).all()
            ]

            query = session.query(Doctor)
            if department:
                query = query.filter(Doctor.department == department)
            if busy_ids:
                query = query.filter(~Doctor.id.in_(busy_ids))

            doctors = query.order_by(Doctor.id).all()
            return [self._doctor_to_dict(item) for item in doctors]

    def update_schedule_status(self, schedule_id: int, status: str,
                               appointment_id: Optional[int] = None) -> bool:
        """
        更新日程状态

        Args:
            schedule_id: 日程ID
            status: 新状态
            appointment_id: 关联就诊ID

        Returns:
            更新是否成功
        """
        with self.session_manager.session_scope() as session:
            schedule = session.query(DoctorSchedule).filter(
                DoctorSchedule.id == schedule_id
            ).first()

            if not schedule:
                return False

            schedule.status = status
            if appointment_id is not None:
                schedule.appointment_id = appointment_id

            return True

    def delete_schedule(self, schedule_id: int) -> bool:
        """
        删除日程

        Args:
            schedule_id: 日程ID

        Returns:
            删除是否成功
        """
        with self.session_manager.session_scope() as session:
            schedule = session.query(DoctorSchedule).filter(
                DoctorSchedule.id == schedule_id
            ).first()

            if not schedule:
                return False

            session.delete(schedule)
            return True

    def _doctor_to_dict(self, doctor: Doctor) -> Dict[str, Any]:
        """将医生对象转换为字典"""
        return {
            'id': doctor.id,
            'name': doctor.name,
            'department': doctor.department,
            'bio': doctor.bio
        }

    def _schedule_to_dict(self, schedule: DoctorSchedule) -> Dict[str, Any]:
        """将日程对象转换为字典"""
        return {
            'id': schedule.id,
            'doctor_id': schedule.doctor_id,
            'doctor_name': schedule.doctor.name if schedule.doctor else None,
            'start_time': schedule.start_time,
            'end_time': schedule.end_time,
            'status': schedule.status,
            'appointment_id': schedule.appointment_id
        }
