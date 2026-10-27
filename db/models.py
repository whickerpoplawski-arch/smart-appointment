from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, JSON, Index, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()


class Doctor(Base):
    """医生——被患者预约的资源"""
    __tablename__ = 'doctors'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)  # 姓名全局唯一，避免重名导致匹配歧义
    department = Column(String, nullable=True)          # 科室，如内科、外科
    bio = Column(Text, nullable=True)                   # 简介，用于同科室医生的相似度推荐

    schedules = relationship("DoctorSchedule", back_populates="doctor", cascade="all, delete-orphan")
    appointments = relationship("Appointment", back_populates="doctor")

    __table_args__ = (
        Index('ix_doctors_department', 'department'),
    )


class DoctorSchedule(Base):
    """医生日程——只负责时段占用与冲突检查"""
    __tablename__ = 'doctor_schedules'
    id = Column(Integer, primary_key=True)
    doctor_id = Column(Integer, ForeignKey('doctors.id', ondelete='CASCADE'), nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default='busy')  # 'busy' 或 'free'
    appointment_id = Column(Integer, ForeignKey('appointments.id'), nullable=True)  # 关联预约记录，便于回溯

    doctor = relationship("Doctor", back_populates="schedules")
    appointment = relationship("Appointment", back_populates="schedules")

    __table_args__ = (
        # 可用性查询固定按 (医生, 时间段) 过滤，做成联合索引
        Index('ix_doctor_schedules_doctor_time', 'doctor_id', 'start_time', 'end_time'),
        Index('ix_doctor_schedules_status', 'status'),
        Index('ix_doctor_schedules_appointment_id', 'appointment_id'),
    )


class Appointment(Base):
    """就诊预约记录——承载患者、科室、就诊类型与渠道"""
    __tablename__ = 'appointments'
    id = Column(Integer, primary_key=True)
    patient_name = Column(String, nullable=False)
    specialty = Column(String, nullable=False)             # 科室原话，如「内科」
    visit_type = Column(String, nullable=False)                # 初诊/复诊/咨询
    channel = Column(String, nullable=False, default='门诊')  # 门诊/互联网医院
    doctor_id = Column(Integer, ForeignKey('doctors.id'), nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default='booked')  # 'booked' 或 'cancelled'
    session_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    doctor = relationship("Doctor", back_populates="appointments")
    schedules = relationship("DoctorSchedule", back_populates="appointment")

    __table_args__ = (
        Index('ix_appointments_patient_name', 'patient_name'),
        Index('ix_appointments_start_time', 'start_time'),
        Index('ix_appointments_status', 'status'),
        Index('ix_appointments_doctor_start', 'doctor_id', 'start_time'),
    )


class KnowledgeDocument(Base):
    """知识库文档——结构保持不变，内容改为就诊 FAQ"""
    __tablename__ = 'knowledge_documents'
    id = Column(Integer, primary_key=True)
    content = Column(Text, nullable=False)
    category = Column(String, nullable=False)
    keywords = Column(JSON, nullable=True)  # 存储关键词列表
    embedding = Column(JSON, nullable=True)  # 存储嵌入向量
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Integer, default=1)  # 软删除标记

    __table_args__ = (
        Index('ix_knowledge_documents_category', 'category'),
        Index('ix_knowledge_documents_is_active', 'is_active'),
    )


class PatientBehavior(Base):
    """患者行为记录——替代原用户行为表"""
    __tablename__ = 'patient_behaviors'
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, default='default_patient')  # 单患者场景使用默认ID
    action_type = Column(String, nullable=False)  # 'appointment', 'consultation', 'inquiry'
    action_data = Column(JSON, nullable=True)     # 存储行为相关的详细数据
    doctor_id = Column(Integer, ForeignKey('doctors.id'), nullable=True)
    session_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    doctor = relationship("Doctor")

    __table_args__ = (
        Index('ix_patient_behaviors_user_created', 'user_id', 'created_at'),
        Index('ix_patient_behaviors_action_type', 'action_type'),
        Index('ix_patient_behaviors_doctor_id', 'doctor_id'),
    )


class PatientPreference(Base):
    """患者偏好——用于推荐排序与文案"""
    __tablename__ = 'patient_preferences'
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, default='default_patient')
    preference_type = Column(String, nullable=False)  # 'doctor', 'time', 'specialty', 'visit_type'
    preference_value = Column(String, nullable=False)
    confidence_score = Column(Integer, default=1)  # 偏好的置信度（出现次数）
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        # 同一患者的同类偏好值只保留一行，靠累加置信度表达强度
        UniqueConstraint('user_id', 'preference_type', 'preference_value', name='uq_candidate_preference'),
        Index('ix_patient_preferences_user_id', 'user_id'),
    )


class SessionSlotSnapshot(Base):
    """进行中的预约槽位。按 session 保存，进程重启后可以恢复到缺哪个槽、是否在等确认。"""
    __tablename__ = "session_slot_snapshots"
    session_id = Column(String, primary_key=True)
    slots_json = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PatientRecommendation(Base):
    """患者推荐——医生推荐与就诊回访提醒"""
    __tablename__ = 'patient_recommendations'
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, default='default_patient')
    recommendation_type = Column(String, nullable=False)  # 'doctor_available', 'appointment_reminder'
    content = Column(Text, nullable=False)
    doctor_id = Column(Integer, ForeignKey('doctors.id'), nullable=True)
    is_sent = Column(Integer, default=0)  # 是否已发送
    created_at = Column(DateTime, default=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)

    doctor = relationship("Doctor")

    __table_args__ = (
        Index('ix_patient_recommendations_user_sent', 'user_id', 'is_sent'),
        Index('ix_patient_recommendations_doctor_id', 'doctor_id'),
    )
