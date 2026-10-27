"""
患者偏好管理器 - 专门负责管理患者的偏好数据

职责：
1. 从就诊预约数据中提取和更新患者偏好
2. 管理常选医生、常申岗位、常用轮次、常用时段等偏好
3. 提供偏好数据的查询和摘要
4. 处理偏好的变化和趋势分析
"""

from typing import Dict, Any, Optional
from datetime import datetime
import logging

# 单患者场景的默认患者ID
DEFAULT_PATIENT_ID = "default_patient"

# 偏好类型常量，避免各处硬编码字符串
PREFERENCE_INTERVIEWER = 'doctor'
PREFERENCE_POSITION = 'specialty'
PREFERENCE_ROUND = 'visit_type'
PREFERENCE_MODE = 'channel'
PREFERENCE_TIME_PERIOD = 'time_period'
PREFERENCE_DURATION = 'duration'


class PatientPreferenceManager:
    """患者偏好管理器 - 负责患者偏好的管理和分析"""

    def __init__(self, behavior_service=None, user_id: str = DEFAULT_PATIENT_ID):
        """
        初始化偏好管理器

        Args:
            behavior_service: 患者行为服务实例
            user_id: 患者ID
        """
        self.behavior_service = behavior_service
        self.user_id = user_id
        self.logger = logging.getLogger(__name__)

    @property
    def behavior_repo(self):
        """获取患者行为数据仓库；未注入服务时懒加载服务层（不直接访问 db 层）"""
        if self.behavior_service is None:
            from services.patient_behavior_service import PatientBehaviorService
            self.behavior_service = PatientBehaviorService()
        return self.behavior_service.patient_behavior_repo

    def _update_preference(self, preference_type: str, preference_value: str) -> bool:
        """写入一条患者偏好"""
        try:
            if self.behavior_service:
                return self.behavior_service.update_patient_preference(
                    self.user_id, preference_type, preference_value
                )
            return self.behavior_repo.update_patient_preference(
                self.user_id, preference_type, preference_value
            )
        except Exception as e:
            self.logger.error(f"更新偏好 {preference_type} 失败: {str(e)}")
            return False

    def update_preferences_from_appointment(self, action_data: Dict[str, Any],
                                          doctor_id: int = None) -> bool:
        """
        从就诊预约数据中更新患者偏好

        Args:
            action_data: 就诊行为数据
            doctor_id: 医生ID

        Returns:
            是否全部更新成功
        """
        results = []

        # 医生偏好
        if doctor_id:
            results.append(self.update_doctor_preference(doctor_id))

        # 岗位偏好
        if action_data.get('specialty'):
            results.append(self.update_position_preference(action_data['specialty']))

        # 轮次偏好
        if action_data.get('visit_type'):
            results.append(self.update_round_preference(action_data['visit_type']))

        # 就诊形式偏好
        if action_data.get('channel'):
            results.append(self.update_mode_preference(action_data['channel']))

        # 就诊时段偏好
        if action_data.get('start_time'):
            results.append(self.update_time_preference(action_data['start_time']))

        # 就诊时长偏好
        if action_data.get('duration'):
            results.append(self.update_duration_preference(action_data['duration']))

        return bool(results) and all(results)

    def update_doctor_preference(self, doctor_id: int) -> bool:
        """更新常选医生偏好"""
        return self._update_preference(PREFERENCE_INTERVIEWER, str(doctor_id))

    def update_position_preference(self, specialty: str) -> bool:
        """更新常申岗位偏好"""
        return self._update_preference(PREFERENCE_POSITION, specialty)

    def update_round_preference(self, round_name: str) -> bool:
        """更新常用就诊类型偏好"""
        return self._update_preference(PREFERENCE_ROUND, round_name)

    def update_mode_preference(self, channel: str) -> bool:
        """更新常用就诊形式偏好"""
        return self._update_preference(PREFERENCE_MODE, channel)

    def update_time_preference(self, start_time: str) -> bool:
        """更新常用就诊时段偏好"""
        try:
            start_datetime = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            hour = start_datetime.hour

            if hour < 12:
                time_period = '上午'
            elif hour < 18:
                time_period = '下午'
            else:
                time_period = '晚上'

            return self._update_preference(PREFERENCE_TIME_PERIOD, time_period)
        except Exception as e:
            self.logger.error(f"更新时间偏好失败: {str(e)}")
            return False

    def update_duration_preference(self, duration: Any) -> bool:
        """更新就诊时长偏好"""
        return self._update_preference(PREFERENCE_DURATION, str(duration))

    def get_patient_preferences(self) -> Dict[str, Any]:
        """
        获取患者所有偏好

        Returns:
            Dict: 偏好类型到偏好值的映射
        """
        try:
            if self.behavior_service:
                preferences = self.behavior_service.get_patient_preferences(self.user_id)
            else:
                preferences = self.behavior_repo.get_patient_preferences(self.user_id)

            # 数据仓库按置信度倒序返回，先出现的值优先
            result = {}
            for item in preferences or []:
                pref_type = item.get('preference_type')
                if pref_type and pref_type not in result:
                    result[pref_type] = item.get('preference_value')
            return result
        except Exception as e:
            self.logger.error(f"获取患者偏好失败: {str(e)}")
            return {}

    def get_preferred_doctor_id(self) -> Optional[int]:
        """获取偏好的医生ID"""
        try:
            doctor_id = self.get_patient_preferences().get(PREFERENCE_INTERVIEWER)
            return int(doctor_id) if doctor_id else None
        except (ValueError, TypeError):
            return None

    def get_preferred_position(self) -> Optional[str]:
        """获取偏好的就诊科室"""
        return self.get_patient_preferences().get(PREFERENCE_POSITION)

    def get_preferred_time_period(self) -> Optional[str]:
        """获取偏好的就诊时段"""
        return self.get_patient_preferences().get(PREFERENCE_TIME_PERIOD)

    def get_preference_summary(self) -> Dict[str, Any]:
        """
        获取偏好摘要信息

        Returns:
            Dict: 偏好摘要
        """
        preferences = self.get_patient_preferences()

        summary = {
            'has_interviewer_preference': bool(preferences.get(PREFERENCE_INTERVIEWER)),
            'has_position_preference': bool(preferences.get(PREFERENCE_POSITION)),
            'has_round_preference': bool(preferences.get(PREFERENCE_ROUND)),
            'has_time_preference': bool(preferences.get(PREFERENCE_TIME_PERIOD)),
            'has_duration_preference': bool(preferences.get(PREFERENCE_DURATION)),
            'preference_count': len([v for v in preferences.values() if v])
        }

        if preferences.get(PREFERENCE_INTERVIEWER):
            summary['preferred_doctor_id'] = int(preferences[PREFERENCE_INTERVIEWER])
        if preferences.get(PREFERENCE_POSITION):
            summary['preferred_position'] = preferences[PREFERENCE_POSITION]
        if preferences.get(PREFERENCE_ROUND):
            summary['preferred_round'] = preferences[PREFERENCE_ROUND]
        if preferences.get(PREFERENCE_MODE):
            summary['preferred_mode'] = preferences[PREFERENCE_MODE]
        if preferences.get(PREFERENCE_TIME_PERIOD):
            summary['preferred_time'] = preferences[PREFERENCE_TIME_PERIOD]
        if preferences.get(PREFERENCE_DURATION):
            summary['preferred_duration'] = preferences[PREFERENCE_DURATION]

        return summary
