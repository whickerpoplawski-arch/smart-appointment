"""
患者行为分析代理

核心功能：
1. 记录患者的就诊预约与咨询行为
2. 分析患者偏好（常申岗位、常选医生、常用时段）
3. 判断跟进时机并生成就诊安排提醒
"""

import logging
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from config.model_provider import create_chat_model
from .patient_behavior import (
    PatientPatternAnalyzer,
    PatientBehaviorRecorder,
    PatientPreferenceManager
)

load_dotenv()

# 单患者场景的默认患者ID
DEFAULT_PATIENT_ID = "default_patient"


class PatientBehaviorAgent:
    """患者行为分析代理"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

        # 初始化LLM
        self.llm = self._initialize_llm()

        # 初始化患者行为服务与组件
        try:
            from services.patient_behavior_service import PatientBehaviorService
            self.behavior_service = PatientBehaviorService()
        except ImportError:
            # 向后兼容：服务层不可用时直接使用数据仓库
            self.behavior_service = None

        self.pattern_analyzer = PatientPatternAnalyzer(self.behavior_service)
        self.behavior_recorder = PatientBehaviorRecorder(self.behavior_service)
        self.preference_manager = PatientPreferenceManager(self.behavior_service)

    def _initialize_llm(self):
        """初始化通用聊天模型"""
        return create_chat_model(temperature=0.7)

    def record_behavior(self, action_type: str, action_data: Dict[str, Any],
                        doctor_id: int = None,
                        session_id: str = "default_session") -> bool:
        """记录患者行为"""
        try:
            if self.behavior_service:
                return self.behavior_service.record_behavior(
                    user_id=DEFAULT_PATIENT_ID,
                    action_type=action_type,
                    action_data=action_data,
                    doctor_id=doctor_id,
                    session_id=session_id
                )

            behavior_id = self.behavior_recorder.record_behavior(
                action_type=action_type,
                action_data=action_data,
                doctor_id=doctor_id,
                session_id=session_id
            )
            return bool(behavior_id)
        except Exception as e:
            self.logger.error(f"记录患者行为失败: {str(e)}")
            return False

    def get_patient_analysis(self, user_id: str = DEFAULT_PATIENT_ID
                               ) -> Optional[Dict[str, Any]]:
        """获取患者偏好分析数据"""
        try:
            preferences = self.pattern_analyzer.analyze_patient_preferences(user_id)
            if not preferences:
                return None

            # 计算距离上次就诊的天数
            last_interview = preferences.get('last_appointment_date')
            days_since_last = None
            if last_interview:
                from datetime import datetime
                if isinstance(last_interview, str):
                    last_interview = datetime.fromisoformat(last_interview.replace('Z', '+00:00'))
                if last_interview.tzinfo is not None:
                    last_interview = last_interview.replace(tzinfo=None)
                days_since_last = (datetime.now() - last_interview).days

            return {
                'favorite_doctor_id': preferences.get('favorite_doctor_id'),
                'favorite_doctor_name': preferences.get('favorite_doctor_name'),
                'favorite_position': preferences.get('favorite_position'),
                'favorite_round': preferences.get('favorite_round'),
                'favorite_mode': preferences.get('favorite_mode'),
                'favorite_duration': preferences.get('favorite_duration'),
                'preferred_time_period': preferences.get('preferred_time_period'),
                'total_appointments': preferences.get('total_appointments'),
                'days_since_last_interview': days_since_last,
                'should_send_reminder': self.pattern_analyzer.should_send_follow_up(user_id)
            }
        except Exception as e:
            self.logger.error(f"获取患者分析失败: {str(e)}")
            return None

    def generate_reminder_message(self, user_id: str = DEFAULT_PATIENT_ID) -> Optional[str]:
        """生成就诊跟进提醒消息"""
        try:
            return self.pattern_analyzer.generate_follow_up_message(user_id)
        except Exception as e:
            self.logger.error(f"生成提醒消息失败: {str(e)}")
            return None

    async def generate_personalized_reminder(self, user_id: str = DEFAULT_PATIENT_ID,
                                             available_times: list = None) -> Optional[str]:
        """使用LLM生成个性化的就诊安排提醒消息"""
        try:
            self.logger.info(f"开始生成个性化提醒，患者ID: {user_id}")
            self.logger.info(f"可用时间: {available_times}")

            analysis = self.get_patient_analysis(user_id)
            if not analysis:
                self.logger.info("没有找到患者偏好数据，使用默认消息")
                return "您好！目前还没有就诊预约记录，如需安排就诊，我可以按岗位方向为您预约合适的医生。"

            # 格式化可用时间
            times = available_times or []
            times_text = "、".join([t.get("formatted", "") for t in times[:3]]) if times else "暂无可用时段"

            # 构建LLM提示
            prompt = f"""请为就诊预约助手生成一条给患者的就诊安排提醒。

患者偏好：
- 常申岗位：{analysis.get('favorite_position') or '暂无记录'}
- 常选医生：{analysis.get('favorite_doctor_name') or '暂无记录'}
- 常用轮次：{analysis.get('favorite_round') or '暂无记录'}
- 常用时段：{analysis.get('preferred_time_period') or '暂无记录'}
- 常用时长：{analysis.get('favorite_duration') or '暂无记录'}
- 医生可用时段：{times_text}

要求：
1. 语气专业、礼貌
2. 结合患者原有的就诊偏好
3. 如果有可用时段，自然地提及具体时间
4. 不要虚构未提供的患者信息
5. 控制在80字以内
6. 直接输出消息内容，不要任何标记"""

            self.logger.info(f"准备调用LLM，提示内容长度: {len(prompt)}")

            response = await self.llm.ainvoke([{"role": "user", "content": prompt}])
            generated_message = response.content.strip()
            if isinstance(generated_message, list):
                generated_message = "".join(str(part) for part in generated_message)

            self.logger.info(f"LLM生成的消息: {generated_message}")
            return generated_message

        except Exception as e:
            self.logger.error(f"LLM生成个性化提醒失败: {type(e).__name__}: {str(e)}")
            # 回退到模板方法
            return self.generate_reminder_message(user_id)

    async def get_reminder_with_schedule(self, user_id: str = DEFAULT_PATIENT_ID
                                         ) -> Dict[str, Any]:
        """获取包含医生可用时段的完整提醒信息"""
        try:
            analysis = self.get_patient_analysis(user_id)
            if not analysis or not analysis.get('favorite_doctor_id'):
                message = await self.generate_personalized_reminder(user_id, [])
                return {
                    "message": message,
                    "doctor_available_times": []
                }

            doctor_id = analysis['favorite_doctor_id']
            duration = self._parse_duration(analysis.get('favorite_duration'), default=60)
            available_times = self._find_available_times(doctor_id, duration)

            # 生成个性化消息
            message = await self.generate_personalized_reminder(user_id, available_times)

            return {
                "message": message,
                "doctor_available_times": available_times
            }

        except Exception as e:
            self.logger.error(f"获取提醒信息失败: {type(e).__name__}: {str(e)}")
            return {
                "message": "您好！系统暂时无法查询医生日程，请稍后再试或直接联系我们安排就诊。",
                "doctor_available_times": []
            }

    @staticmethod
    def _parse_duration(duration: Any, default: int = 60) -> int:
        """把时长偏好转换为分钟数"""
        digits = ''.join(filter(str.isdigit, str(duration))) if duration is not None else ''
        return int(digits) if digits and int(digits) > 0 else default

    def _find_available_times(self, doctor_id: int, duration: int) -> list:
        """在未来两天的工作时段中寻找医生可用的整点时段，最多返回3个"""
        from datetime import timedelta
        from config.time_config import time_config
        from services.appointment_service import AppointmentService

        appointment_service = AppointmentService()
        now = time_config.now().replace(tzinfo=None)
        available_times = []

        for day_offset in range(2):
            day = (now + timedelta(days=day_offset)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            for hour in range(9, 18):
                check_time = day.replace(hour=hour)
                if check_time <= now:
                    continue
                end_time = check_time + timedelta(minutes=duration)

                if appointment_service.is_doctor_available(doctor_id, check_time, end_time):
                    prefix = "今天" if day_offset == 0 else "明天"
                    available_times.append({
                        "date": day.strftime("%Y-%m-%d"),
                        "time": f"{hour}:00",
                        "formatted": f"{prefix}{hour}:00"
                    })
                    if len(available_times) >= 3:
                        return available_times

        return available_times
