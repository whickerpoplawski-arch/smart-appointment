"""
Database Module

数据库模块，包含：
- 数据模型定义
- 数据访问对象 (Repository)
- 数据库路由器
- 会话管理
"""

from .db_router import DatabaseRouter, DoctorDBRouter
from .repositories import (
    DoctorRepository,
    AppointmentRepository,
    KnowledgeRepository,
    PatientBehaviorRepository,
)
from .base import SessionManager
from .models import (
    Base, Doctor, DoctorSchedule, Appointment,
    KnowledgeDocument, PatientBehavior, PatientPreference, PatientRecommendation
)

__all__ = [
    # 主要入口
    'DatabaseRouter',

    # 兼容性路由器
    'DoctorDBRouter',

    # Repository模式
    'DoctorRepository',
    'AppointmentRepository',
    'KnowledgeRepository',
    'PatientBehaviorRepository',

    # 基础设施
    'SessionManager',

    # 数据模型
    'Base',
    'Doctor',
    'DoctorSchedule',
    'Appointment',
    'KnowledgeDocument',
    'PatientBehavior',
    'PatientPreference',
    'PatientRecommendation'
]
