from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from ..base.interfaces import BaseAppointmentRepository
from ..base.session_manager import SessionManager
from ..models import Appointment, Doctor, DoctorSchedule


class AppointmentRepository(BaseAppointmentRepository):
    """
    预约记录数据访问对象

    职责：
    1. 预约记录的创建与查询
    2. 预约落库：预约记录 + 医生忙碌日程在同一事务内完成
    3. 取消就诊并释放日程
    """

    def __init__(self, session_manager: SessionManager):
        """
        初始化就诊数据仓库

        Args:
            session_manager: 会话管理器
        """
        self.session_manager = session_manager

    def create_appointment(self, patient_name: str, specialty: str, visit_type: str, channel: str,
                         doctor_id: int, start_time: datetime, end_time: datetime,
                         session_id: Optional[str] = None) -> int:
        """
        创建预约记录（不写日程，仅用于数据修复等特殊场景）

        Args:
            patient_name: 患者姓名
            specialty: 就诊科室
            visit_type: 就诊类型
            channel: 就诊形式
            doctor_id: 医生ID
            start_time: 开始时间
            end_time: 结束时间
            session_id: 会话ID

        Returns:
            新创建的就诊ID
        """
        self._validate_appointment_fields(patient_name, specialty, visit_type, channel, start_time, end_time)

        with self.session_manager.session_scope() as session:
            appointment = Appointment(
                patient_name=patient_name,
                specialty=specialty,
                visit_type=visit_type,
                channel=channel,
                doctor_id=doctor_id,
                start_time=start_time,
                end_time=end_time,
                session_id=session_id,
                status='booked'
            )
            session.add(appointment)
            session.flush()
            return appointment.id

    def book_appointment(self, patient_name: str, specialty: str, visit_type: str, channel: str,
                       doctor_id: int, start_time: datetime, end_time: datetime,
                       session_id: Optional[str] = None) -> int:
        """
        在同一事务内创建预约记录并占用医生日程

        医生不存在或时段冲突时抛出 ValueError；数据库异常由 session_scope
        统一回滚，调用方不会看到半条记录。

        Args:
            patient_name: 患者姓名
            specialty: 就诊科室
            visit_type: 就诊类型
            channel: 就诊形式
            doctor_id: 医生ID
            start_time: 开始时间
            end_time: 结束时间
            session_id: 会话ID

        Returns:
            新创建的就诊ID
        """
        self._validate_appointment_fields(patient_name, specialty, visit_type, channel, start_time, end_time)

        with self.session_manager.session_scope() as session:
            doctor = session.query(Doctor).filter(
                Doctor.id == doctor_id
            ).first()
            if not doctor:
                raise ValueError(f"医生不存在：ID={doctor_id}")

            # 在同一事务、同一会话内复查冲突，避免“先查后写”之间被抢占
            conflict = session.query(DoctorSchedule).filter(
                DoctorSchedule.doctor_id == doctor_id,
                DoctorSchedule.status == "busy",
                DoctorSchedule.start_time < end_time,
                DoctorSchedule.end_time > start_time
            ).first()
            if conflict:
                raise ValueError(
                    f"医生 {doctor.name} 在 {start_time} 到 {end_time} 已有安排"
                )

            appointment = Appointment(
                patient_name=patient_name,
                specialty=specialty,
                visit_type=visit_type,
                channel=channel,
                doctor_id=doctor_id,
                start_time=start_time,
                end_time=end_time,
                session_id=session_id,
                status='booked'
            )
            session.add(appointment)
            session.flush()  # 先拿到就诊ID，供日程外键引用

            schedule = DoctorSchedule(
                doctor_id=doctor_id,
                start_time=start_time,
                end_time=end_time,
                status='busy',
                appointment_id=appointment.id
            )
            session.add(schedule)
            session.flush()

            return appointment.id

    def get_appointment_by_id(self, appointment_id: int) -> Optional[Dict[str, Any]]:
        """
        根据ID获取预约记录

        Args:
            appointment_id: 就诊ID

        Returns:
            就诊信息字典，如果不存在返回None
        """
        with self.session_manager.session_scope() as session:
            appointment = session.query(Appointment).filter(
                Appointment.id == appointment_id
            ).first()

            if not appointment:
                return None

            return self._appointment_to_dict(appointment)

    def get_appointments_by_patient(self, patient_name: str) -> List[Dict[str, Any]]:
        """
        根据患者姓名获取预约记录

        Args:
            patient_name: 患者姓名

        Returns:
            预约记录列表
        """
        with self.session_manager.session_scope() as session:
            appointments = session.query(Appointment).filter(
                Appointment.patient_name == patient_name
            ).order_by(Appointment.start_time.desc()).all()

            return [self._appointment_to_dict(appointment) for appointment in appointments]

    def get_appointments_by_date(self, date: datetime) -> List[Dict[str, Any]]:
        """
        获取指定日期的预约记录

        Args:
            date: 查询日期

        Returns:
            预约记录列表
        """
        start = datetime(date.year, date.month, date.day)
        end = start + timedelta(days=1)

        with self.session_manager.session_scope() as session:
            appointments = session.query(Appointment).filter(
                Appointment.start_time >= start,
                Appointment.start_time < end
            ).order_by(Appointment.start_time).all()

            return [self._appointment_to_dict(appointment) for appointment in appointments]

    def get_all_appointments(self) -> List[Dict[str, Any]]:
        """
        获取所有预约记录

        Returns:
            预约记录列表（按开始时间倒序）
        """
        with self.session_manager.session_scope() as session:
            appointments = session.query(Appointment).order_by(
                Appointment.start_time.desc()
            ).all()

            return [self._appointment_to_dict(appointment) for appointment in appointments]

    def cancel_appointment(self, appointment_id: int) -> bool:
        """
        取消就诊并释放对应日程

        幂等：已取消的就诊再次取消返回 True，不重复改状态。

        Args:
            appointment_id: 就诊ID

        Returns:
            取消是否成功
        """
        with self.session_manager.session_scope() as session:
            appointment = session.query(Appointment).filter(
                Appointment.id == appointment_id
            ).first()

            if not appointment:
                return False

            if appointment.status == 'cancelled':
                return True

            appointment.status = 'cancelled'

            # 释放该就诊占用的日程；保留 appointment_id 便于审计回溯
            session.query(DoctorSchedule).filter(
                DoctorSchedule.appointment_id == appointment_id,
                DoctorSchedule.status == 'busy'
            ).update({DoctorSchedule.status: 'free'})

            return True

    def _validate_appointment_fields(self, patient_name: str, specialty: str, visit_type: str, channel: str,
                                   start_time: datetime, end_time: datetime) -> None:
        """校验必填字段与时间区间，避免写入不可用的预约记录"""
        if not patient_name or not patient_name.strip():
            raise ValueError("患者姓名不能为空")
        if not specialty or not specialty.strip():
            raise ValueError("就诊科室不能为空")
        if not visit_type or not visit_type.strip():
            raise ValueError("就诊类型不能为空")
        if not channel or not channel.strip():
            raise ValueError("就诊形式不能为空")
        if end_time <= start_time:
            raise ValueError("就诊结束时间必须晚于开始时间")

    def _appointment_to_dict(self, appointment: Appointment) -> Dict[str, Any]:
        """将就诊对象转换为字典"""
        return {
            'id': appointment.id,
            'patient_name': appointment.patient_name,
            'specialty': appointment.specialty,
            'visit_type': appointment.visit_type,
            'channel': appointment.channel,
            'doctor_id': appointment.doctor_id,
            'doctor_name': appointment.doctor.name if appointment.doctor else None,
            'start_time': appointment.start_time,
            'end_time': appointment.end_time,
            'status': appointment.status,
            'session_id': appointment.session_id,
            'created_at': appointment.created_at
        }
