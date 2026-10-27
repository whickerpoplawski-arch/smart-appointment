"""
Database Base Module

数据库基础模块，包含：
- 会话管理
- 抽象接口
- 通用工具
"""

from .session_manager import SessionManager
from .interfaces import (
    BaseDoctorRepository,
    BaseScheduleRepository,
    BaseAppointmentRepository,
    BaseKnowledgeRepository,
    BasePatientBehaviorRepository,
)

__all__ = [
    'SessionManager',
    'BaseDoctorRepository',
    'BaseScheduleRepository',
    'BaseAppointmentRepository',
    'BaseKnowledgeRepository',
    'BasePatientBehaviorRepository',
]
