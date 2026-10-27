"""
PatientBehavior Module

提供患者行为分析相关的核心组件（简化版）：
- PatientBehaviorRecorder: 患者行为记录器 - 记录患者操作行为
- PatientPatternAnalyzer: 患者模式分析器 - 分析常选医生、常申岗位与常用时段
- PatientPreferenceManager: 患者偏好管理器 - 管理患者偏好数据
"""

from .behavior_recorder import PatientBehaviorRecorder
from .pattern_analyzer import PatientPatternAnalyzer
from .preference_manager import PatientPreferenceManager

__all__ = [
    'PatientBehaviorRecorder',
    'PatientPatternAnalyzer',
    'PatientPreferenceManager'
]
