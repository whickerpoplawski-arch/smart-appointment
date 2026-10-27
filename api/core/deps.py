"""
API 依赖工厂

1. 统一从环境变量 APPOINTMENT_DB_PATH 读取数据库路径，便于测试与部署时切换到独立库；
2. 统一在这里创建服务实例，API 模块不直接依赖具体数据库连接串。
"""

import os
from typing import Optional

# 与 services / db 层保持一致的默认路径
DEFAULT_DB_PATH = "sqlite:///data/doctor_appointment.db"


def database_path() -> str:
    """读取当前生效的数据库路径，未配置时使用默认库"""
    return os.getenv("APPOINTMENT_DB_PATH") or DEFAULT_DB_PATH


def appointment_service(db_path: Optional[str] = None):
    """创建就诊服务实例（含预约记录与医生日程能力）"""
    from services.appointment_service import AppointmentService

    return AppointmentService(db_path or database_path())


def doctor_service(db_path: Optional[str] = None):
    """创建医生服务实例"""
    from services.doctor_service import DoctorService

    return DoctorService(db_path or database_path())


def patient_behavior_service(db_path: Optional[str] = None):
    """创建患者行为服务实例"""
    from services.patient_behavior_service import PatientBehaviorService

    return PatientBehaviorService(db_path or database_path())


def ensure_default_doctors(service=None) -> None:
    """
    幂等补齐默认医生

    初始化失败只记录日志：库里已有多少就返回多少，避免因为一次补种失败
    让只读页面整体 500，预约失败也会在匹配阶段给出明确提示。
    """
    import logging

    try:
        if service is None:
            service = doctor_service()
        service.initialize_default_doctors()
    except Exception as e:
        logging.getLogger(__name__).warning(f"默认医生初始化失败，继续使用现有数据: {e}")
