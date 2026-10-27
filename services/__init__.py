"""
业务服务层模块

包含：
- 知识库服务
- 医生服务
- 就诊服务
- 患者行为服务
- 推荐调度服务
- 文本嵌入工具
"""

from .text_embedding import (
    embed_input,
    find_best_match_indices,
    save_doctor_embeddings,
    load_doctor_embeddings
)
from .knowledge_service import KnowledgeService
from .doctor_service import DoctorService
from .appointment_service import AppointmentService
from .patient_behavior_service import PatientBehaviorService
from .recommendation_service import RecommendationService

__all__ = [
    'embed_input',
    'find_best_match_indices',
    'save_doctor_embeddings',
    'load_doctor_embeddings',
    'KnowledgeService',
    'DoctorService',
    'AppointmentService',
    'PatientBehaviorService',
    'RecommendationService'
]
