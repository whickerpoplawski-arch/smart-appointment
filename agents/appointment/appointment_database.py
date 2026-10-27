"""
就诊数据库操作器

负责处理就诊预约相关的数据库操作：
1. 通过 AppointmentService 在同一事务内写入预约记录与医生日程
2. 记录患者就诊行为，供偏好分析使用
3. 维护内存中的医生忙碌时段缓存
注意：通过Services层访问数据库，符合分层架构
"""

from typing import Dict, Any, Tuple
from datetime import datetime
from config.time_config import time_config
from config.constants import doctor_busy_periods


class AppointmentDatabase:
    """就诊数据库操作器"""

    def __init__(self, appointment_service=None, patient_behavior_service=None):
        """
        初始化就诊数据库操作器

        Args:
            appointment_service: 就诊服务实例，传入时便于测试替换
            patient_behavior_service: 患者行为服务实例
        """
        self._appointment_service = appointment_service
        self._patient_behavior_service = patient_behavior_service

    @property
    def appointment_service(self):
        """懒加载就诊服务"""
        if self._appointment_service is None:
            from services.appointment_service import AppointmentService
            self._appointment_service = AppointmentService()
        return self._appointment_service

    @property
    def patient_behavior_service(self):
        """懒加载患者行为服务"""
        if self._patient_behavior_service is None:
            from services.patient_behavior_service import PatientBehaviorService
            self._patient_behavior_service = PatientBehaviorService()
        return self._patient_behavior_service

    def save_appointment(self, doctor_id: int, start_time: datetime,
                       end_time: datetime, appointment_history: Dict[str, Any],
                       session_id: str) -> Tuple[bool, str]:
        """
        保存就诊信息到数据库

        Args:
            doctor_id: 医生ID
            start_time: 就诊开始时间
            end_time: 就诊结束时间
            appointment_history: 会话中的就诊信息
            session_id: 会话ID

        Returns:
            (是否成功, 失败原因)。成功时原因为空字符串
        """
        # 先做前置校验，让失败原因对患者可读，而不是一律「保存失败」
        try:
            reason = self._validate(doctor_id, start_time, end_time, appointment_history)
        except Exception as e:
            print(f"校验就诊预约条件失败：{e}")
            return False, "无法校验医生日程，请稍后重试"
        if reason:
            return False, reason

        appointment_info = {
            'patient_name': appointment_history.get('patient_name'),
            'specialty': appointment_history.get('specialty'),
            'visit_type': appointment_history.get('visit_type'),
            'channel': appointment_history.get('channel') or '门诊'
        }

        try:
            success = self.appointment_service.save_appointment(
                doctor_id, start_time, end_time, appointment_info, session_id
            )
        except Exception as e:
            print(f"保存就诊信息到数据库失败：{e}")
            return False, "数据库写入异常，请稍后重试"

        if not success:
            # 服务层已回滚事务，这里不再写日程与行为，避免留下半条记录
            return False, "数据库写入失败，可能该时段刚被占用"

        # 更新内存中的忙碌时段缓存
        self.update_memory_schedule(doctor_id, start_time, end_time)
        # 记录患者行为（失败不影响就诊本身）
        self._record_patient_behavior(start_time, end_time, doctor_id,
                                        appointment_history, session_id)
        return True, ""

    def _validate(self, doctor_id: int, start_time: datetime, end_time: datetime,
                  appointment_history: Dict[str, Any]) -> str:
        """校验预约前置条件，返回失败原因；通过时返回空字符串"""
        for field, label in (
            ('patient_name', '患者姓名'),
            ('specialty', '就诊科室'),
            ('visit_type', '就诊类型')
        ):
            value = appointment_history.get(field)
            if not value or value == "未知":
                return f"缺少{label}"

        if not start_time or not end_time:
            return "就诊时间无法识别"
        if end_time <= start_time:
            return "就诊结束时间必须晚于开始时间"

        doctor = self.appointment_service.get_doctor_by_id(doctor_id)
        if not doctor:
            return "医生不存在或已离职"

        if not self.appointment_service.is_doctor_available(doctor_id, start_time, end_time):
            return f"{doctor.get('name', '该医生')}在所选时段已有安排"

        return ""

    def update_memory_schedule(self, doctor_id: int, start_time: datetime, end_time: datetime):
        """更新内存中的医生忙碌时间段"""
        busy_period = {
            "start": time_config.format_datetime(start_time, "%H:%M"),
            "end": time_config.format_datetime(end_time, "%H:%M")
        }
        doctor_busy_periods.setdefault(doctor_id, []).append(busy_period)

    def _record_patient_behavior(self, start_time: datetime, end_time: datetime,
                                   doctor_id: int, appointment_history: Dict[str, Any],
                                   session_id: str):
        """记录患者预约就诊的行为"""
        try:
            action_data = {
                'start_time': time_config.format_datetime(start_time, "%Y-%m-%d %H:%M:%S"),
                'end_time': time_config.format_datetime(end_time, "%Y-%m-%d %H:%M:%S"),
                'duration': int((end_time - start_time).total_seconds() / 60),
                'specialty': appointment_history.get('specialty', ''),
                'visit_type': appointment_history.get('visit_type', ''),
                'channel': appointment_history.get('channel', '门诊'),
                'preference': appointment_history.get('preference', ''),
                'doctor_id': doctor_id
            }

            self.patient_behavior_service.record_behavior(
                user_id="default_patient",  # 单患者场景统一使用默认患者ID
                action_type='appointment',
                action_data=action_data,
                doctor_id=doctor_id,
                session_id=session_id
            )

        except Exception as behavior_error:
            print(f"记录患者行为失败（但就诊预约仍然成功）：{behavior_error}")
