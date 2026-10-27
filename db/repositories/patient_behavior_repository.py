from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from ..base.interfaces import BasePatientBehaviorRepository
from ..base.session_manager import SessionManager
from ..models import PatientBehavior, PatientPreference, PatientRecommendation, Doctor

# 患者行为类型常量，避免各处硬编码字符串
ACTION_INTERVIEW = 'appointment'
ACTION_CONSULTATION = 'consultation'
ACTION_INQUIRY = 'inquiry'

# 单患者场景的默认用户ID
DEFAULT_PATIENT_ID = 'default_patient'


class PatientBehaviorRepository(BasePatientBehaviorRepository):
    """
    患者行为数据访问对象

    职责：
    1. 患者行为记录和查询
    2. 患者偏好管理
    3. 医生推荐与回访提醒数据支持
    4. 患者统计信息生成
    """

    def __init__(self, session_manager: SessionManager):
        """
        初始化患者行为数据仓库

        Args:
            session_manager: 会话管理器
        """
        self.session_manager = session_manager

    def record_behavior(self, user_id: str, action_type: str, action_data: Optional[Dict[str, Any]] = None,
                       doctor_id: Optional[int] = None, session_id: Optional[str] = None) -> int:
        """
        记录患者行为

        Args:
            user_id: 患者用户ID
            action_type: 行为类型
            action_data: 行为数据
            doctor_id: 医生ID
            session_id: 会话ID

        Returns:
            新创建的行为记录ID
        """
        with self.session_manager.session_scope() as session:
            behavior = PatientBehavior(
                user_id=user_id,
                action_type=action_type,
                action_data=action_data,
                doctor_id=doctor_id,
                session_id=session_id
            )
            session.add(behavior)
            session.flush()
            return behavior.id

    def get_patient_behaviors(self, user_id: str, action_type: Optional[str] = None,
                               days_back: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        获取患者行为历史

        Args:
            user_id: 患者用户ID
            action_type: 行为类型过滤
            days_back: 查询多少天内的记录

        Returns:
            患者行为列表
        """
        with self.session_manager.session_scope() as session:
            query = session.query(PatientBehavior).filter(PatientBehavior.user_id == user_id)

            if action_type:
                query = query.filter(PatientBehavior.action_type == action_type)

            if days_back:
                cutoff_date = datetime.utcnow() - timedelta(days=days_back)
                query = query.filter(PatientBehavior.created_at >= cutoff_date)

            behaviors = query.order_by(PatientBehavior.created_at.desc()).all()

            return [self._behavior_to_dict(behavior) for behavior in behaviors]

    def get_recent_behaviors(self, user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        获取患者最近的行为记录

        Args:
            user_id: 患者用户ID
            limit: 返回记录数量限制

        Returns:
            最近的行为记录列表
        """
        with self.session_manager.session_scope() as session:
            behaviors = session.query(PatientBehavior).filter(
                PatientBehavior.user_id == user_id
            ).order_by(PatientBehavior.created_at.desc()).limit(limit).all()

            return [self._behavior_to_dict(behavior) for behavior in behaviors]

    def update_patient_preference(self, user_id: str, preference_type: str, preference_value: str) -> bool:
        """
        更新患者偏好

        Args:
            user_id: 患者用户ID
            preference_type: 偏好类型
            preference_value: 偏好值

        Returns:
            更新是否成功
        """
        with self.session_manager.session_scope() as session:
            # 查找现有偏好
            existing = session.query(PatientPreference).filter(
                PatientPreference.user_id == user_id,
                PatientPreference.preference_type == preference_type,
                PatientPreference.preference_value == preference_value
            ).first()

            if existing:
                # 增加置信度
                existing.confidence_score += 1
                existing.last_updated = datetime.utcnow()
            else:
                # 创建新偏好
                preference = PatientPreference(
                    user_id=user_id,
                    preference_type=preference_type,
                    preference_value=preference_value,
                    confidence_score=1
                )
                session.add(preference)

            return True

    def get_patient_preferences(self, user_id: str, preference_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        获取患者偏好

        Args:
            user_id: 患者用户ID
            preference_type: 偏好类型过滤

        Returns:
            患者偏好列表
        """
        with self.session_manager.session_scope() as session:
            query = session.query(PatientPreference).filter(PatientPreference.user_id == user_id)

            if preference_type:
                query = query.filter(PatientPreference.preference_type == preference_type)

            preferences = query.order_by(PatientPreference.confidence_score.desc()).all()

            return [self._preference_to_dict(preference) for preference in preferences]

    def get_top_preferences(self, user_id: str, preference_type: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        获取患者特定类型的高置信度偏好

        Args:
            user_id: 患者用户ID
            preference_type: 偏好类型
            limit: 返回数量限制

        Returns:
            高置信度偏好列表
        """
        with self.session_manager.session_scope() as session:
            preferences = session.query(PatientPreference).filter(
                PatientPreference.user_id == user_id,
                PatientPreference.preference_type == preference_type
            ).order_by(PatientPreference.confidence_score.desc()).limit(limit).all()

            return [self._preference_to_dict(preference) for preference in preferences]

    def create_recommendation(self, user_id: str, recommendation_type: str, content: str,
                            doctor_id: Optional[int] = None) -> int:
        """
        创建推荐

        Args:
            user_id: 患者用户ID
            recommendation_type: 推荐类型
            content: 推荐内容
            doctor_id: 相关医生ID

        Returns:
            新创建的推荐ID
        """
        with self.session_manager.session_scope() as session:
            recommendation = PatientRecommendation(
                user_id=user_id,
                recommendation_type=recommendation_type,
                content=content,
                doctor_id=doctor_id
            )
            session.add(recommendation)
            session.flush()
            return recommendation.id

    def get_pending_recommendations(self, user_id: str) -> List[Dict[str, Any]]:
        """
        获取待发送的推荐

        Args:
            user_id: 患者用户ID

        Returns:
            待发送推荐列表
        """
        with self.session_manager.session_scope() as session:
            recommendations = session.query(PatientRecommendation).filter(
                PatientRecommendation.user_id == user_id,
                PatientRecommendation.is_sent == 0
            ).order_by(PatientRecommendation.created_at.desc()).all()

            return [self._recommendation_to_dict(recommendation) for recommendation in recommendations]

    def mark_recommendation_sent(self, recommendation_id: int) -> bool:
        """
        标记推荐为已发送

        Args:
            recommendation_id: 推荐ID

        Returns:
            标记是否成功
        """
        with self.session_manager.session_scope() as session:
            recommendation = session.query(PatientRecommendation).filter(
                PatientRecommendation.id == recommendation_id
            ).first()

            if recommendation:
                recommendation.is_sent = 1
                recommendation.sent_at = datetime.utcnow()
                return True
            return False

    def get_patient_statistics(self, user_id: str, days_back: int = 30) -> Dict[str, Any]:
        """
        获取患者统计信息

        Args:
            user_id: 患者用户ID
            days_back: 统计天数

        Returns:
            患者统计信息字典
        """
        with self.session_manager.session_scope() as session:
            cutoff_date = datetime.utcnow() - timedelta(days=days_back)

            # 总行为数
            total_behaviors = session.query(PatientBehavior).filter(
                PatientBehavior.user_id == user_id,
                PatientBehavior.created_at >= cutoff_date
            ).count()

            # 就诊预约次数
            interview_count = session.query(PatientBehavior).filter(
                PatientBehavior.user_id == user_id,
                PatientBehavior.action_type == ACTION_INTERVIEW,
                PatientBehavior.created_at >= cutoff_date
            ).count()

            # 咨询次数
            consultation_count = session.query(PatientBehavior).filter(
                PatientBehavior.user_id == user_id,
                PatientBehavior.action_type == ACTION_CONSULTATION,
                PatientBehavior.created_at >= cutoff_date
            ).count()

            # 最常预约的医生
            from sqlalchemy import func
            favorite_interviewer = session.query(
                PatientBehavior.doctor_id,
                Doctor.name,
                func.count(PatientBehavior.doctor_id).label('count')
            ).join(Doctor).filter(
                PatientBehavior.user_id == user_id,
                PatientBehavior.action_type == ACTION_INTERVIEW,
                PatientBehavior.created_at >= cutoff_date
            ).group_by(PatientBehavior.doctor_id, Doctor.name).order_by(
                func.count(PatientBehavior.doctor_id).desc()
            ).first()

            # 最后一次访问
            last_visit = session.query(PatientBehavior).filter(
                PatientBehavior.user_id == user_id,
                PatientBehavior.action_type == ACTION_INTERVIEW
            ).order_by(PatientBehavior.created_at.desc()).first()

            return {
                'total_behaviors': total_behaviors,
                'interview_count': interview_count,
                'consultation_count': consultation_count,
                'favorite_doctor_id': favorite_interviewer[0] if favorite_interviewer else None,
                'favorite_doctor_name': favorite_interviewer[1] if favorite_interviewer else None,
                'favorite_interviewer_visits': favorite_interviewer[2] if favorite_interviewer else 0,
                'last_visit_date': last_visit.created_at if last_visit else None,
                'days_since_last_visit': (datetime.utcnow() - last_visit.created_at).days if last_visit else None,
                'period_days': days_back
            }

    def get_interviewer_popularity(self, days_back: int = 30) -> List[Dict[str, Any]]:
        """
        获取医生受欢迎程度统计

        Args:
            days_back: 统计天数

        Returns:
            医生受欢迎程度列表
        """
        with self.session_manager.session_scope() as session:
            cutoff_date = datetime.utcnow() - timedelta(days=days_back)

            from sqlalchemy import func

            popularity = session.query(
                PatientBehavior.doctor_id,
                Doctor.name,
                func.count(PatientBehavior.doctor_id).label('interview_count'),
                func.count(func.distinct(PatientBehavior.user_id)).label('unique_candidates')
            ).join(Doctor).filter(
                PatientBehavior.action_type == ACTION_INTERVIEW,
                PatientBehavior.created_at >= cutoff_date
            ).group_by(PatientBehavior.doctor_id, Doctor.name).order_by(
                func.count(PatientBehavior.doctor_id).desc()
            ).all()

            return [
                {
                    'doctor_id': p[0],
                    'doctor_name': p[1],
                    'interview_count': p[2],
                    'unique_candidates': p[3]
                }
                for p in popularity
            ]

    def _behavior_to_dict(self, behavior: PatientBehavior) -> Dict[str, Any]:
        """将行为对象转换为字典"""
        return {
            'id': behavior.id,
            'user_id': behavior.user_id,
            'action_type': behavior.action_type,
            'action_data': behavior.action_data,
            'doctor_id': behavior.doctor_id,
            'doctor_name': behavior.doctor.name if behavior.doctor else None,
            'session_id': behavior.session_id,
            'created_at': behavior.created_at
        }

    def _preference_to_dict(self, preference: PatientPreference) -> Dict[str, Any]:
        """将偏好对象转换为字典"""
        return {
            'id': preference.id,
            'user_id': preference.user_id,
            'preference_type': preference.preference_type,
            'preference_value': preference.preference_value,
            'confidence_score': preference.confidence_score,
            'last_updated': preference.last_updated
        }

    def _recommendation_to_dict(self, recommendation: PatientRecommendation) -> Dict[str, Any]:
        """将推荐对象转换为字典"""
        return {
            'id': recommendation.id,
            'user_id': recommendation.user_id,
            'recommendation_type': recommendation.recommendation_type,
            'content': recommendation.content,
            'doctor_id': recommendation.doctor_id,
            'doctor_name': recommendation.doctor.name if recommendation.doctor else None,
            'is_sent': bool(recommendation.is_sent),
            'created_at': recommendation.created_at,
            'sent_at': recommendation.sent_at
        }
