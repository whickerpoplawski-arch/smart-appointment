from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime


class BaseDoctorRepository(ABC):
    """
    医生数据访问抽象接口

    定义医生相关的所有数据操作方法
    """

    @abstractmethod
    def add_doctor(self, name: str, department: Optional[str] = None, bio: Optional[str] = None) -> int:
        """添加医生"""
        pass

    @abstractmethod
    def get_doctor_by_id(self, doctor_id: int) -> Optional[Dict[str, Any]]:
        """根据ID获取医生信息"""
        pass

    @abstractmethod
    def get_doctor_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """根据姓名获取医生信息"""
        pass

    @abstractmethod
    def get_all_doctors(self) -> List[Dict[str, Any]]:
        """获取所有医生"""
        pass

    @abstractmethod
    def get_doctors_by_department(self, department: str) -> List[Dict[str, Any]]:
        """根据科室获取医生"""
        pass

    @abstractmethod
    def get_all_departments(self) -> List[str]:
        """获取所有科室"""
        pass

    @abstractmethod
    def update_doctor(self, doctor_id: int, **updates) -> bool:
        """更新医生信息"""
        pass

    @abstractmethod
    def delete_doctor(self, doctor_id: int) -> bool:
        """删除医生"""
        pass


class BaseScheduleRepository(ABC):
    """
    医生日程数据访问抽象接口

    定义日程占用、查询与冲突检查方法
    """

    @abstractmethod
    def add_schedule(self, doctor_id: int, start_time: datetime, end_time: datetime,
                     status: str, appointment_id: Optional[int] = None) -> int:
        """添加日程"""
        pass

    @abstractmethod
    def get_doctor_schedules(self, doctor_id: int, date: datetime) -> List[Dict[str, Any]]:
        """获取医生指定日期的日程"""
        pass

    @abstractmethod
    def get_schedules_by_date(self, date: datetime) -> List[Dict[str, Any]]:
        """获取所有医生指定日期的日程"""
        pass

    @abstractmethod
    def is_doctor_available(self, doctor_id: int, start_time: datetime, end_time: datetime) -> bool:
        """检查医生时间段是否可用"""
        pass

    @abstractmethod
    def get_available_doctors(self, start_time: datetime, end_time: datetime,
                                   department: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取指定时段内空闲的医生"""
        pass

    @abstractmethod
    def update_schedule_status(self, schedule_id: int, status: str,
                               appointment_id: Optional[int] = None) -> bool:
        """更新日程状态"""
        pass

    @abstractmethod
    def delete_schedule(self, schedule_id: int) -> bool:
        """删除日程"""
        pass


class BaseAppointmentRepository(ABC):
    """
    就诊数据访问抽象接口

    定义预约记录的创建、查询与取消方法
    """

    @abstractmethod
    def create_appointment(self, patient_name: str, specialty: str, visit_type: str, channel: str,
                         doctor_id: int, start_time: datetime, end_time: datetime,
                         session_id: Optional[str] = None) -> int:
        """创建预约记录（不写日程）"""
        pass

    @abstractmethod
    def book_appointment(self, patient_name: str, specialty: str, visit_type: str, channel: str,
                       doctor_id: int, start_time: datetime, end_time: datetime,
                       session_id: Optional[str] = None) -> int:
        """在同一事务内创建预约记录并占用医生日程"""
        pass

    @abstractmethod
    def get_appointment_by_id(self, appointment_id: int) -> Optional[Dict[str, Any]]:
        """根据ID获取预约记录"""
        pass

    @abstractmethod
    def get_appointments_by_patient(self, patient_name: str) -> List[Dict[str, Any]]:
        """根据患者姓名获取预约记录"""
        pass

    @abstractmethod
    def get_appointments_by_date(self, date: datetime) -> List[Dict[str, Any]]:
        """获取指定日期的预约记录"""
        pass

    @abstractmethod
    def get_all_appointments(self) -> List[Dict[str, Any]]:
        """获取所有预约记录"""
        pass

    @abstractmethod
    def cancel_appointment(self, appointment_id: int) -> bool:
        """取消就诊并释放对应日程"""
        pass


class BaseKnowledgeRepository(ABC):
    """
    知识库数据访问抽象接口

    定义知识库相关的所有数据操作方法
    """

    @abstractmethod
    def add_document(self, content: str, category: str, keywords: Optional[List[str]] = None,
                    embedding: Optional[List[float]] = None) -> int:
        """添加知识文档"""
        pass

    @abstractmethod
    def get_document(self, doc_id: int) -> Optional[Dict[str, Any]]:
        """获取指定文档"""
        pass

    @abstractmethod
    def get_all_documents(self, include_inactive: bool = False) -> List[Dict[str, Any]]:
        """获取所有文档"""
        pass

    @abstractmethod
    def update_document(self, doc_id: int, content: Optional[str] = None, category: Optional[str] = None,
                       keywords: Optional[List[str]] = None, embedding: Optional[List[float]] = None) -> bool:
        """更新文档"""
        pass

    @abstractmethod
    def delete_document(self, doc_id: int, soft_delete: bool = True) -> bool:
        """删除文档（支持软删除）"""
        pass

    @abstractmethod
    def search_documents_by_category(self, category: str) -> List[Dict[str, Any]]:
        """按分类搜索文档"""
        pass

    @abstractmethod
    def search_documents_by_keywords(self, keywords: List[str]) -> List[Dict[str, Any]]:
        """按关键词搜索文档"""
        pass

    @abstractmethod
    def get_all_categories(self) -> List[str]:
        """获取所有分类"""
        pass

    @abstractmethod
    def get_documents_count(self) -> int:
        """获取文档总数"""
        pass


class BasePatientBehaviorRepository(ABC):
    """
    患者行为数据访问抽象接口

    定义患者行为分析相关的所有数据操作方法
    """

    @abstractmethod
    def record_behavior(self, user_id: str, action_type: str, action_data: Optional[Dict[str, Any]] = None,
                       doctor_id: Optional[int] = None, session_id: Optional[str] = None) -> int:
        """记录患者行为"""
        pass

    @abstractmethod
    def get_patient_behaviors(self, user_id: str, action_type: Optional[str] = None,
                               days_back: Optional[int] = None) -> List[Dict[str, Any]]:
        """获取患者行为历史"""
        pass

    @abstractmethod
    def update_patient_preference(self, user_id: str, preference_type: str, preference_value: str) -> bool:
        """更新患者偏好"""
        pass

    @abstractmethod
    def get_patient_preferences(self, user_id: str, preference_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取患者偏好"""
        pass

    @abstractmethod
    def create_recommendation(self, user_id: str, recommendation_type: str, content: str,
                            doctor_id: Optional[int] = None) -> int:
        """创建推荐"""
        pass

    @abstractmethod
    def get_pending_recommendations(self, user_id: str) -> List[Dict[str, Any]]:
        """获取待发送的推荐"""
        pass

    @abstractmethod
    def mark_recommendation_sent(self, recommendation_id: int) -> bool:
        """标记推荐为已发送"""
        pass

    @abstractmethod
    def get_patient_statistics(self, user_id: str, days_back: int = 30) -> Dict[str, Any]:
        """获取患者统计信息"""
        pass
