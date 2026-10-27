"""
预约记录 API

提供预约记录的查询与创建接口。预约记录是业务事实来源（患者、岗位、轮次、形式），
医生日程只是时段占用；两者通过 appointment_id 关联。

接口职责边界：
- 查询：支持按日期、患者、状态筛选，默认按开始时间倒序；
- 创建：直接落库，不经过对话流程，供外部系统或运维脚本调用；
- 可用性：给「先看空档再预约」的调用方一个只读接口。

错误语义约定（供调用方按状态码分支）：
- 400：参数缺失、时间格式错误或时间区间非法；
- 404：医生不存在；
- 409：该时段已被占用（含并发抢占，由数据库事务兜底）。
"""

from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from pydantic import BaseModel

from api.core.deps import ensure_default_doctors, appointment_service
from api.core.serializers import format_datetime, parse_date

router = APIRouter(prefix="/api/appointments", tags=["预约记录"])


class AppointmentResponse(BaseModel):
    """预约记录响应"""
    id: int
    patient_name: str
    specialty: str
    visit_type: str
    channel: str
    doctor_id: int
    doctor_name: Optional[str] = None
    start_time: str
    end_time: str
    status: str
    session_id: Optional[str] = None
    created_at: Optional[str] = None


class AppointmentCreateRequest(BaseModel):
    """创建就诊请求"""
    patient_name: str
    specialty: str
    visit_type: str
    start_time: str
    end_time: str
    doctor_id: int
    channel: str = "门诊"
    session_id: Optional[str] = None


class AvailabilityResponse(BaseModel):
    """时段可用性响应"""
    available: bool
    department: Optional[str] = None
    available_doctors: List[dict] = []


def _to_response(appointment: dict) -> AppointmentResponse:
    """把仓库层的就诊字典转换为响应模型，时间统一格式化为展示字符串"""
    return AppointmentResponse(
        id=appointment["id"],
        patient_name=appointment["patient_name"],
        specialty=appointment["specialty"],
        visit_type=appointment["visit_type"],
        channel=appointment["channel"],
        doctor_id=appointment["doctor_id"],
        doctor_name=appointment.get("doctor_name"),
        start_time=format_datetime(appointment["start_time"]) or "",
        end_time=format_datetime(appointment["end_time"]) or "",
        status=appointment["status"],
        session_id=appointment.get("session_id"),
        created_at=format_datetime(appointment.get("created_at"))
    )


def _record_api_behavior(patient_name: str, specialty: str, round_name: str, channel: str,
                         doctor_id: int, start_time, end_time, session_id: str) -> None:
    """
    记录经 API 创建就诊的患者行为

    对话流程由 AppointmentDatabase 负责记录行为；直接走 API 创建时在这里补上，
    保证患者偏好分析页对两种入口看到同一份数据。失败只记日志。
    """
    try:
        from api.core.deps import patient_behavior_service
        from config.time_config import time_config

        duration = int((end_time - start_time).total_seconds() / 60)
        patient_behavior_service().record_behavior(
            user_id="default_patient",
            action_type="appointment",
            action_data={
                "patient_name": patient_name,
                "specialty": specialty,
                "visit_type": round_name,
                "channel": channel,
                "duration": duration,
                "start_time": time_config.format_datetime(start_time, "%Y-%m-%d %H:%M:%S"),
                "end_time": time_config.format_datetime(end_time, "%Y-%m-%d %H:%M:%S"),
                "doctor_id": doctor_id
            },
            doctor_id=doctor_id,
            session_id=session_id
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"记录患者就诊行为失败（不影响预约）: {e}")


@router.get("/", response_model=List[AppointmentResponse], summary="获取预约记录列表")
async def get_appointments(date: Optional[str] = Query(None, description="按日期筛选，格式 YYYY-MM-DD"),
                         patient_name: Optional[str] = Query(None, description="按患者姓名筛选"),
                         status: Optional[str] = Query(None, description="按状态筛选：booked / cancelled")):
    """
    获取预约记录列表

    三种筛选可以叠加；都不传时返回全部记录。日期筛选基于就诊开始时间，
    与医生日程页使用同一个时间基准（北京时间）。
    """
    return await list_appointments(date=date, patient_name=patient_name, status=status)


async def list_appointments(date: Optional[str] = None,
                          patient_name: Optional[str] = None,
                          status: Optional[str] = None) -> List[AppointmentResponse]:
    """
    预约记录查询的实现（供路由与 Web 页面共用）

    路由声明里的 Query(None) 只对 HTTP 请求生效；页面渲染直接调用本函数，
    不会把 Query 对象当参数传进 SQL，这是 Web 层复用取数逻辑时最容易踩的坑。
    """
    try:
        service = appointment_service()

        if date:
            try:
                target_date = parse_date(date)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            appointments = service.get_appointments_by_date(target_date) or []
        elif patient_name:
            appointments = service.get_appointments_by_patient(patient_name) or []
        else:
            appointments = service.get_all_appointments() or []

        # 患者与状态在内存中二次过滤，避免为每种组合新增仓库方法
        if patient_name and date:
            appointments = [item for item in appointments if item.get("patient_name") == patient_name]
        if status:
            appointments = [item for item in appointments if item.get("status") == status]

        return [_to_response(item) for item in appointments]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取预约记录失败: {str(e)}")


@router.post("/", response_model=AppointmentResponse, status_code=201, summary="创建就诊预约")
async def create_appointment(request: AppointmentCreateRequest):
    """
    创建就诊预约

    落库前先做可读性校验（医生存在、时间区间合法、时段空闲），
    真正的并发保护由 AppointmentRepository.book_appointment 的单事务复查完成。
    """
    try:
        from config.time_config import time_config

        patient_name = (request.patient_name or "").strip()
        specialty = (request.specialty or "").strip()
        round_name = (request.visit_type or "").strip()
        channel = (request.channel or "门诊").strip()

        if not patient_name or not specialty or not round_name:
            raise HTTPException(status_code=400, detail="患者姓名、就诊科室、就诊类型均不能为空")

        try:
            start_time = time_config.parse_datetime(request.start_time)
            end_time = time_config.parse_datetime(request.end_time)
        except (ValueError, TypeError):
            start_time, end_time = None, None
        if not start_time or not end_time:
            raise HTTPException(status_code=400, detail="时间格式应为 YYYY-MM-DD HH:MM")
        if end_time <= start_time:
            raise HTTPException(status_code=400, detail="就诊结束时间必须晚于开始时间")

        service = appointment_service()
        # 直接调用创建接口时也补齐默认医生，避免空库首次调用返回 404
        ensure_default_doctors()
        doctor = service.get_doctor_by_id(request.doctor_id)
        if not doctor:
            raise HTTPException(status_code=404, detail="医生不存在")

        # 预检查让绝大多数冲突返回明确的 409，而不是笼统的保存失败
        if not service.is_doctor_available(request.doctor_id, start_time, end_time):
            raise HTTPException(
                status_code=409,
                detail=f"{doctor.get('name', '该医生')}在所选时段已有安排"
            )

        success = service.save_appointment(
            request.doctor_id,
            start_time,
            end_time,
            {
                'patient_name': patient_name,
                'specialty': specialty,
                'visit_type': round_name,
                'channel': channel
            },
            request.session_id or "api"
        )
        if not success:
            # 事务已回滚：可能是预检查后被并发抢占，或数据库异常
            raise HTTPException(status_code=409, detail="就诊预约未成功，可能该时段刚被占用，请重试")

        # 记录患者行为，让偏好分析页也能看到经 API 创建的预约；
        # 行为记录失败不影响就诊本身，因此单独兜底。
        _record_api_behavior(
            patient_name=patient_name,
            specialty=specialty,
            round_name=round_name,
            channel=channel,
            doctor_id=request.doctor_id,
            start_time=start_time,
            end_time=end_time,
            session_id=request.session_id or "api"
        )

        # 返回落库后的完整记录（含医生姓名与标准化时间）
        appointments = service.get_appointments_by_patient(patient_name) or []
        created = next(
            (item for item in appointments
             if item.get("doctor_id") == request.doctor_id
             and format_datetime(item.get("start_time")) == format_datetime(start_time)),
            None
        )
        if not created:
            raise HTTPException(status_code=500, detail="就诊已写入，但读取回执失败，请查询预约记录列表")

        return _to_response(created)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建就诊预约失败: {str(e)}")


@router.get("/availability", response_model=AvailabilityResponse, summary="查询时段可用医生")
async def get_availability(start_time: str = Query(..., description="开始时间 YYYY-MM-DD HH:MM"),
                           end_time: str = Query(..., description="结束时间 YYYY-MM-DD HH:MM"),
                           specialty: Optional[str] = Query(None, description="就诊科室，用于匹配科室")):
    """
    查询指定时段空闲的医生

    传 specialty 时只返回该科室的空闲医生。说了话但认不出科室时返回空列表，
    不把全部医生当成候补，与对话里「先选科室」一致。未传 specialty 时返回该时段全部空闲医生。
    """
    try:
        from config.time_config import time_config

        start = time_config.parse_datetime(start_time)
        end = time_config.parse_datetime(end_time)
        if not start or not end:
            raise HTTPException(status_code=400, detail="时间格式应为 YYYY-MM-DD HH:MM")
        if end <= start:
            raise HTTPException(status_code=400, detail="就诊结束时间必须晚于开始时间")

        service = appointment_service()
        department = service.match_department(specialty) if specialty else None
        if specialty and not department:
            doctors = []
        elif department:
            doctors = service.get_available_doctors(start, end, department=department)
        else:
            doctors = service.get_available_doctors(start, end)

        return AvailabilityResponse(
            available=bool(doctors),
            department=department,
            available_doctors=[
                {
                    "id": item["id"],
                    "name": item["name"],
                    "department": item.get("department"),
                    "bio": item.get("bio")
                }
                for item in (doctors or [])
            ]
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询时段可用医生失败: {str(e)}")


@router.get("/{appointment_id}", response_model=AppointmentResponse, summary="获取单条预约记录")
async def get_appointment(appointment_id: int):
    """获取指定ID的预约记录"""
    try:
        appointment = appointment_service().get_appointment_by_id(appointment_id)
        if not appointment:
            raise HTTPException(status_code=404, detail="预约记录不存在")
        return _to_response(appointment)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取预约记录失败: {str(e)}")
