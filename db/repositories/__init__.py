"""
Repositories Module

数据访问对象模块，包含：
- 医生数据仓库
- 预约记录数据仓库
- 知识库数据仓库
- 患者行为数据仓库
"""

from .doctor_repository import DoctorRepository
from .appointment_repository import AppointmentRepository
from .knowledge_repository import KnowledgeRepository
from .patient_behavior_repository import PatientBehaviorRepository

__all__ = [
    'DoctorRepository',
    'AppointmentRepository',
    'KnowledgeRepository',
    'PatientBehaviorRepository'
]
