"""
医生 API

提供医生信息、科室筛选与日程查询接口。

路由顺序说明：`/schedules/today` 必须声明在 `/{doctor_id}` 之前，
否则 FastAPI 会先匹配动态路由，把 `schedules` 当成医生 ID 并返回 422。
"""

from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from pydantic import BaseModel

from api.core.deps import ensure_default_doctors, appointment_service, doctor_service
from api.core.serializers import format_time

router = APIRouter(prefix="/api/doctors", tags=["医生管理"])


class DoctorResponse(BaseModel):
    """医生信息响应：方向用于筛选，简介用于相似度推荐"""
    id: int
    name: str
    department: Optional[str] = None
    bio: Optional[str] = None


class ScheduleResponse(BaseModel):
    """医生日程响应：只表达时段占用，就诊业务信息在预约记录中"""
    id: int
    doctor_id: int
    start_time: str
    end_time: str
    status: str
    appointment_id: Optional[int] = None


class BusyPeriod(BaseModel):
    """今日忙碌时段，带上就诊信息便于前台直接辨认"""
    id: int
    start: str
    end: str
    appointment_id: Optional[int] = None
    patient_name: Optional[str] = None
    specialty: Optional[str] = None
    visit_type: Optional[str] = None
    channel: Optional[str] = None


class DoctorScheduleToday(BaseModel):
    """单个医生今日日程"""
    doctor_id: int
    doctor_name: str
    department: Optional[str] = None
    busy_periods: List[BusyPeriod] = []


@router.get("/", response_model=List[DoctorResponse], summary="获取医生列表")
async def get_all_doctors(department: Optional[str] = Query(None, description="按科室筛选，如「内科」")):
    """获取所有医生信息，可按科室过滤"""
    return await list_doctors(department=department)


async def list_doctors(department: Optional[str] = None) -> List[DoctorResponse]:
    """
    医生列表查询的实现（供路由与 Web 页面共用）

    注意：不要把 Query(None) 的默认值直接暴露给内部调用方。页面渲染会直接调用
    本函数，若沿用路由签名，department 会是一个 truthy 的 Query 对象，
    进而把 `WHERE department = ?` 绑上非法参数。
    """
    try:
        service = doctor_service()
        ensure_default_doctors(service)

        if department:
            doctors = service.get_doctors_by_department(department)
        else:
            doctors = service.get_all_doctors()

        return [
            DoctorResponse(
                id=item["id"],
                name=item["name"],
                department=item.get("department"),
                bio=item.get("bio")
            )
            for item in doctors
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取医生信息失败: {str(e)}")


@router.get("/schedules/today", response_model=List[DoctorScheduleToday], summary="获取所有医生今日日程")
async def get_all_doctors_schedule_today():
    """获取所有医生今天的忙碌时段"""
    return await list_doctors_schedule_today()


async def list_doctors_schedule_today() -> List[DoctorScheduleToday]:
    """
    今日日程的实现（供路由与 Web 页面共用）

    预约记录按天一次性取出后按 appointment_id 关联，避免逐个时段回查就诊表。
    """
    try:
        from config.time_config import time_config

        service = doctor_service()
        ensure_default_doctors(service)

        today = time_config.today()
        doctors = service.get_all_doctors()

        # 今日预约记录：一次查询，供忙碌时段补充患者与科室
        appointments_today = appointment_service().get_appointments_by_date(today) or []
        appointment_map = {item["id"]: item for item in appointments_today}

        schedules_data = []
        for doctor in doctors:
            doctor_id = doctor["id"]
            schedules = service.get_doctor_schedules(doctor_id, today) or []

            busy_periods = []
            for schedule in schedules:
                if schedule.get("status") != "busy":
                    continue

                appointment = appointment_map.get(schedule.get("appointment_id")) or {}
                busy_periods.append(BusyPeriod(
                    id=schedule["id"],
                    start=format_time(schedule["start_time"]) or "",
                    end=format_time(schedule["end_time"]) or "",
                    appointment_id=schedule.get("appointment_id"),
                    patient_name=appointment.get("patient_name"),
                    specialty=appointment.get("specialty"),
                    visit_type=appointment.get("visit_type"),
                    channel=appointment.get("channel")
                ))

            schedules_data.append(DoctorScheduleToday(
                doctor_id=doctor_id,
                doctor_name=doctor["name"],
                department=doctor.get("department"),
                busy_periods=busy_periods
            ))

        return schedules_data

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取医生日程失败: {str(e)}")


@router.get("/{doctor_id}/schedule", response_model=List[ScheduleResponse], summary="获取单个医生日程")
async def get_appointmenter_schedule(doctor_id: int,
                                   date: Optional[str] = Query(None, description="查询日期，格式 YYYY-MM-DD，缺省为今天")):
    """获取指定医生某一天的日程，默认今天"""
    try:
        from api.core.serializers import parse_date
        from config.time_config import time_config

        service = doctor_service()
        ensure_default_doctors(service)

        doctor = service.get_doctor_by_id(doctor_id)
        if not doctor:
            raise HTTPException(status_code=404, detail="医生不存在")

        try:
            target_date = parse_date(date) if date else time_config.today()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        schedules = service.get_doctor_schedules(doctor_id, target_date) or []
        return [
            ScheduleResponse(
                id=schedule["id"],
                doctor_id=schedule["doctor_id"],
                start_time=format_time(schedule["start_time"]) or "",
                end_time=format_time(schedule["end_time"]) or "",
                status=schedule["status"],
                appointment_id=schedule.get("appointment_id")
            )
            for schedule in schedules
        ]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取医生日程失败: {str(e)}")


@router.get("/{doctor_id}", response_model=DoctorResponse, summary="获取单个医生信息")
async def get_appointmenter(doctor_id: int):
    """获取指定医生的详细信息"""
    try:
        service = doctor_service()
        ensure_default_doctors(service)

        doctor = service.get_doctor_by_id(doctor_id)
        if not doctor:
            raise HTTPException(status_code=404, detail="医生不存在")

        return DoctorResponse(
            id=doctor["id"],
            name=doctor["name"],
            department=doctor.get("department"),
            bio=doctor.get("bio")
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取医生信息失败: {str(e)}")
