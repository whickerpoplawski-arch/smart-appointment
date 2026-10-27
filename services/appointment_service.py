"""
就诊服务层

职责：
1. 封装就诊预约相关的数据库操作
2. 保证预约记录与医生日程在同一事务内落库
3. 提供医生、预约记录与可用性查询
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from db.db_router import DatabaseRouter
import logging

logger = logging.getLogger(__name__)

# 只认用户说出的科室名。不做症状到科室的关键词表。
DEPARTMENT_CHOICES = (
    "内科",
    "外科",
    "骨科",
    "儿科",
    "皮肤科",
    "眼科",
    "耳鼻喉科",
    "口腔科",
    "妇产科",
    "中医科",
)
DEPARTMENTS = tuple(sorted(DEPARTMENT_CHOICES, key=len, reverse=True))


def match_department_name(specialty: Optional[str]) -> Optional[str]:
    """科室名出现在原话里才算数。最长名优先，避免短名误伤。"""
    if specialty is None:
        return None
    text = str(specialty).strip()
    if not text or text == "未知":
        return None
    for name in DEPARTMENTS:
        if name in text:
            return name
    return None


class AppointmentService:
    """就诊服务类"""

    def __init__(self, db_path: str = 'sqlite:///data/doctor_appointment.db'):
        self.db_router = DatabaseRouter(db_path)
        self.doctor_repo = self.db_router.doctors
        self.appointment_repo = self.db_router.appointments

    def save_appointment(self, doctor_id: int, start_time: datetime,
                       end_time: datetime, appointment_info: Dict[str, Any],
                       session_id: str) -> bool:
        """
        保存就诊信息到数据库

        在同一事务内写入 appointments 与 doctor_schedules(status=busy)：
        任一步失败整体回滚，不会留下半条记录。

        Args:
            doctor_id: 医生ID
            start_time: 就诊开始时间
            end_time: 就诊结束时间
            appointment_info: 就诊信息，需包含 patient_name/specialty/visit_type，
                            可选 channel（缺省「门诊」）
            session_id: 会话ID

        Returns:
            是否保存成功
        """
        try:
            doctor_id = int(doctor_id)

            patient_name = (appointment_info.get('patient_name') or '').strip()
            specialty = (appointment_info.get('specialty') or '').strip()
            round_name = (appointment_info.get('visit_type') or '').strip()
            channel = (appointment_info.get('channel') or '门诊').strip()

            if not patient_name or not specialty or not round_name:
                logger.warning(
                    f"就诊信息缺少必填字段：patient_name={patient_name!r}, "
                    f"specialty={specialty!r}, visit_type={round_name!r}"
                )
                return False

            if end_time <= start_time:
                logger.warning(f"就诊时间区间非法：{start_time} 到 {end_time}")
                return False

            appointment_id = self.appointment_repo.book_appointment(
                patient_name=patient_name,
                specialty=specialty,
                visit_type=round_name,
                channel=channel,
                doctor_id=doctor_id,
                start_time=start_time,
                end_time=end_time,
                session_id=session_id
            )

            logger.info(
                f"就诊信息已保存：就诊ID={appointment_id}, 医生ID={doctor_id}, "
                f"时间={start_time} 到 {end_time}"
            )
            return True

        except Exception as e:
            # book_appointment 内部事务已回滚，这里只负责向上返回失败
            logger.error(f"保存就诊信息到数据库失败：{e}")
            return False

    def cancel_appointment(self, appointment_id: int) -> bool:
        """取消就诊并释放医生日程"""
        try:
            return self.appointment_repo.cancel_appointment(int(appointment_id))
        except Exception as e:
            logger.error(f"取消就诊失败：{e}")
            return False

    def get_appointment_by_id(self, appointment_id: int) -> Optional[Dict[str, Any]]:
        """根据ID获取预约记录"""
        try:
            return self.appointment_repo.get_appointment_by_id(int(appointment_id))
        except Exception as e:
            logger.error(f"获取预约记录失败：{e}")
            return None

    def get_appointments_by_patient(self, patient_name: str) -> List[Dict[str, Any]]:
        """根据患者姓名获取预约记录"""
        try:
            return self.appointment_repo.get_appointments_by_patient(patient_name)
        except Exception as e:
            logger.error(f"获取患者预约记录失败：{e}")
            return []

    def get_appointments_by_date(self, date: datetime) -> List[Dict[str, Any]]:
        """获取指定日期的预约记录"""
        try:
            return self.appointment_repo.get_appointments_by_date(date)
        except Exception as e:
            logger.error(f"获取指定日期预约记录失败：{e}")
            return []

    def get_all_appointments(self) -> List[Dict[str, Any]]:
        """获取所有预约记录"""
        try:
            return self.appointment_repo.get_all_appointments()
        except Exception as e:
            logger.error(f"获取预约记录列表失败：{e}")
            return []

    def get_doctor_by_id(self, doctor_id: int) -> Optional[Dict[str, Any]]:
        """根据ID获取医生信息"""
        try:
            return self.doctor_repo.get_doctor_by_id(int(doctor_id))
        except Exception as e:
            logger.error(f"获取医生信息失败：{e}")
            return None

    def get_doctor_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """根据姓名获取医生信息"""
        try:
            return self.doctor_repo.get_doctor_by_name(name)
        except Exception as e:
            logger.error(f"获取医生信息失败：{e}")
            return None

    def get_all_doctors(self) -> List[Dict[str, Any]]:
        """获取所有医生信息"""
        try:
            return self.doctor_repo.get_all_doctors()
        except Exception as e:
            logger.error(f"获取医生列表失败：{e}")
            return []

    def get_doctors_by_department(self, department: str) -> List[Dict[str, Any]]:
        """根据科室获取医生信息"""
        try:
            return self.doctor_repo.get_doctors_by_department(department)
        except Exception as e:
            logger.error(f"根据科室获取医生信息失败：{e}")
            return []

    def get_all_departments(self) -> List[str]:
        """获取所有科室"""
        try:
            return self.doctor_repo.get_all_departments()
        except Exception as e:
            logger.error(f"获取科室列表失败：{e}")
            return []

    def get_doctors_by_specialty(self, specialty: str) -> List[Dict[str, Any]]:
        """只返回科室名对得上的医生。对不上时返回空列表，不拿全库凑数。"""
        department = self.match_department(specialty)
        if not department:
            return []
        return self.get_doctors_by_department(department)

    def match_department(self, specialty: str) -> Optional[str]:
        """specialty_only：原话里必须出现十个科室名之一。"""
        return match_department_name(specialty)

    def get_doctor_schedules(self, doctor_id: int, date) -> List[Dict[str, Any]]:
        """获取医生指定日期的日程"""
        try:
            return self.doctor_repo.get_doctor_schedules(int(doctor_id), date)
        except Exception as e:
            logger.error(f"获取医生日程失败：{e}")
            return []

    def get_schedules_by_date(self, date) -> List[Dict[str, Any]]:
        """获取所有医生指定日期的日程"""
        try:
            return self.doctor_repo.get_schedules_by_date(date)
        except Exception as e:
            logger.error(f"获取医生日程列表失败：{e}")
            return []

    def is_doctor_available(self, doctor_id: int, start_time: datetime, end_time: datetime) -> bool:
        """检查医生是否可用"""
        try:
            return self.doctor_repo.is_doctor_available(int(doctor_id), start_time, end_time)
        except Exception as e:
            logger.error(f"检查医生可用性失败：{e}")
            return False

    def get_available_doctors(self, start_time: datetime, end_time: datetime,
                                   department: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取指定时段空闲的医生，可按科室过滤"""
        try:
            return self.doctor_repo.get_available_doctors(start_time, end_time, department)
        except Exception as e:
            logger.error(f"获取空闲医生失败：{e}")
            return []

    def add_doctor(self, name: str, department: str = None, bio: str = None) -> Optional[int]:
        """添加新医生"""
        try:
            return self.doctor_repo.add_doctor(name, department, bio)
        except Exception as e:
            logger.error(f"添加医生失败：{e}")
            return None
