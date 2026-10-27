"""
患者行为分析器

核心功能：
1. 分析患者常选的医生
2. 分析患者常申的岗位、轮次与常用时段
3. 判断是否需要跟进提醒
4. 生成个性化的就诊跟进消息
"""

from typing import Dict, Any, Optional
from datetime import datetime
import logging

# 单患者场景的默认患者ID
DEFAULT_PATIENT_ID = "default_patient"


class PatientPatternAnalyzer:
    """患者行为分析器"""

    def __init__(self, behavior_service=None):
        self.behavior_service = behavior_service
        self.logger = logging.getLogger(__name__)

    @property
    def behavior_repo(self):
        """获取患者行为数据仓库；未注入服务时懒加载服务层（不直接访问 db 层）"""
        if self.behavior_service is None:
            from services.patient_behavior_service import PatientBehaviorService
            self.behavior_service = PatientBehaviorService()
        return self.behavior_service.patient_behavior_repo

    def _get_appointment_behaviors(self, user_id: str):
        """获取患者的就诊行为记录"""
        if self.behavior_service:
            return self.behavior_service.get_patient_behaviors(
                user_id=user_id,
                action_type='appointment'
            )
        return self.behavior_repo.get_patient_behaviors(
            user_id=user_id,
            action_type='appointment'
        )

    def analyze_patient_preferences(self, user_id: str = DEFAULT_PATIENT_ID
                                      ) -> Optional[Dict[str, Any]]:
        """分析患者偏好：常选医生、常申岗位、常用轮次与时段"""
        try:
            appointments = self._get_appointment_behaviors(user_id)
            if not appointments:
                return None

            doctor_counts = {}
            position_counts = {}
            round_counts = {}
            mode_counts = {}
            duration_counts = {}
            time_period_counts = {}

            for appointment in appointments:
                data = appointment.get('action_data', {})
                if not isinstance(data, dict):
                    data = {}

                # 医生ID存储在行为记录的独立字段中
                doctor_id = appointment.get('doctor_id')
                if doctor_id:
                    doctor_counts[doctor_id] = doctor_counts.get(doctor_id, 0) + 1

                specialty = data.get('specialty')
                if specialty:
                    position_counts[specialty] = position_counts.get(specialty, 0) + 1

                round_name = data.get('visit_type')
                if round_name:
                    round_counts[round_name] = round_counts.get(round_name, 0) + 1

                channel = data.get('channel')
                if channel:
                    mode_counts[channel] = mode_counts.get(channel, 0) + 1

                duration = data.get('duration')
                if duration:
                    duration_counts[duration] = duration_counts.get(duration, 0) + 1

                time_period = self._time_period_of(data.get('start_time'))
                if time_period:
                    time_period_counts[time_period] = time_period_counts.get(time_period, 0) + 1

            favorite_doctor_id = self._most_common_key(doctor_counts)
            last_appointment_date = appointments[0].get('created_at') if appointments else None

            return {
                'favorite_doctor_id': favorite_doctor_id,
                'favorite_doctor_name': self._get_doctor_name(favorite_doctor_id),
                'favorite_position': self._most_common_key(position_counts),
                'favorite_round': self._most_common_key(round_counts),
                'favorite_mode': self._most_common_key(mode_counts),
                'favorite_duration': self._most_common_key(duration_counts),
                'preferred_time_period': self._most_common_key(time_period_counts),
                'total_appointments': len(appointments),
                'last_appointment_date': last_appointment_date
            }

        except Exception as e:
            self.logger.error(f"分析患者偏好失败: {str(e)}")
            return None

    @staticmethod
    def _most_common_key(counts: Dict[Any, int]) -> Optional[Any]:
        """返回出现次数最多的键，空字典时返回 None"""
        return max(counts, key=counts.get) if counts else None

    @staticmethod
    def _time_period_of(start_time: Any) -> Optional[str]:
        """把就诊开始时间归到上午/下午/晚上"""
        if not start_time:
            return None
        try:
            if isinstance(start_time, str):
                start_time = datetime.fromisoformat(start_time)
            hour = start_time.hour
        except (ValueError, TypeError, AttributeError):
            return None

        if hour < 12:
            return '上午'
        if hour < 18:
            return '下午'
        return '晚上'

    def _get_doctor_name(self, doctor_id: Optional[Any]) -> Optional[str]:
        """通过医生服务把ID转换为姓名"""
        if not doctor_id:
            return None
        try:
            from services.doctor_service import DoctorService
            doctor = DoctorService().get_doctor_by_id(doctor_id)
            return doctor.get('name') if doctor else None
        except Exception as e:
            self.logger.error(f"获取医生姓名失败: {str(e)}")
            return None

    def should_send_follow_up(self, user_id: str = DEFAULT_PATIENT_ID,
                              days_threshold: int = 30) -> bool:
        """判断是否需要发送就诊跟进提醒"""
        try:
            preferences = self.analyze_patient_preferences(user_id)
            if not preferences or preferences['total_appointments'] < 1:
                return False

            last_interview = preferences['last_appointment_date']
            if not last_interview:
                return False

            # 确保是datetime对象
            if isinstance(last_interview, str):
                last_interview = datetime.fromisoformat(last_interview.replace('Z', '+00:00'))
            if last_interview.tzinfo is not None:
                last_interview = last_interview.replace(tzinfo=None)

            days_since_last = (datetime.now() - last_interview).days
            return days_since_last >= days_threshold

        except Exception as e:
            self.logger.error(f"判断跟进提醒失败: {str(e)}")
            return False

    def generate_follow_up_message(self, user_id: str = DEFAULT_PATIENT_ID) -> Optional[str]:
        """生成个性化的就诊跟进消息"""
        try:
            preferences = self.analyze_patient_preferences(user_id)
            if not preferences:
                return "您好！目前还没有就诊预约记录，如需安排就诊，我可以按岗位方向为您预约医生。"

            specialty = preferences.get('favorite_position') or '目标岗位'
            round_name = preferences.get('favorite_round') or '就诊'
            time_period = preferences.get('preferred_time_period') or '合适的时间'
            doctor_name = preferences.get('favorite_doctor_name')

            if doctor_name:
                message = (
                    f"您好！您之前主要申请{specialty}，习惯安排在{time_period}进行{round_name}，"
                    f"常选{doctor_name}医生。"
                )
            else:
                message = f"您好！您之前主要申请{specialty}，习惯安排在{time_period}进行{round_name}。"

            message += "如果需要安排下一轮就诊，我可以按同样的偏好为您协调医生。"
            return message

        except Exception as e:
            self.logger.error(f"生成跟进消息失败: {str(e)}")
            return "您好！如需安排就诊，我可以按岗位方向为您预约合适的医生。"
