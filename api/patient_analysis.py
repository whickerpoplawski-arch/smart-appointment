"""
患者偏好分析 API

基于 patient_behaviors / patient_preferences 输出患者的就诊偏好画像，
并为「生成就诊安排提醒」提供可用时段。

设计取舍：
1. 分析数据直接从 PatientPatternAnalyzer 取，它与 LLM 无关，未配置模型时页面依然可用；
2. 提醒文案优先走 LLM 个性化生成，模型不可用时回退到模板文案并照常返回可用时段，
   避免一个可选的润色能力把整个分析页拖垮。
"""

from fastapi import APIRouter
from typing import List, Optional
from datetime import timedelta
from pydantic import BaseModel

from api.core.deps import patient_behavior_service, appointment_service

# 单患者场景的默认患者ID，与 agents / services 层保持一致
DEFAULT_PATIENT_ID = "default_patient"

router = APIRouter(prefix="/api/patient-analysis", tags=["患者偏好分析"])
# 兼容旧的 underscore 风格前缀，前端与外部脚本都可能缓存了旧路径
router_underscore = APIRouter(prefix="/api/patient_analysis", tags=["患者偏好分析"])


class PatientAnalysisResponse(BaseModel):
    """患者偏好分析响应"""
    favorite_doctor_id: Optional[int] = None
    favorite_doctor_name: Optional[str] = None
    favorite_position: Optional[str] = None
    favorite_round: Optional[str] = None
    favorite_mode: Optional[str] = None
    favorite_duration: Optional[int] = None
    preferred_time_period: Optional[str] = None
    total_appointments: int = 0
    days_since_last_interview: Optional[int] = None
    should_send_reminder: bool = False


class AvailableTime(BaseModel):
    """医生可选时段"""
    date: str
    time: str
    formatted: str


class ReminderRequest(BaseModel):
    """生成就诊提醒请求"""
    user_id: str = DEFAULT_PATIENT_ID


class ReminderResponse(BaseModel):
    """就诊提醒响应"""
    message: str
    doctor_available_times: List[AvailableTime] = []


def _build_analyzer():
    """创建患者偏好分析器，服务不可用时由调用方捕获异常"""
    from agents.patient_behavior.pattern_analyzer import PatientPatternAnalyzer
    return PatientPatternAnalyzer(patient_behavior_service())


def _parse_duration(duration) -> int:
    """把「60」或「60分钟」统一转成分钟数，无法识别时返回 60"""
    digits = ''.join(filter(str.isdigit, str(duration))) if duration is not None else ''
    return int(digits) if digits and int(digits) > 0 else 60


def _find_available_times(doctor_id: int, duration: int) -> List[AvailableTime]:
    """
    在未来两天的工作时段（9:00-18:00）里寻找医生可用的整点时段，最多 3 个

    这是提醒功能的辅助信息，任何异常都只导致「没有可选时段」，不抛出给调用方。
    """
    try:
        from config.time_config import time_config

        service = appointment_service()
        now = time_config.now().replace(tzinfo=None)
        available: List[AvailableTime] = []

        for day_offset in range(2):
            day = (now + timedelta(days=day_offset)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            for hour in range(9, 18):
                start = day.replace(hour=hour)
                if start <= now:
                    continue
                end = start + timedelta(minutes=duration)
                if not service.is_doctor_available(doctor_id, start, end):
                    continue

                prefix = "今天" if day_offset == 0 else "明天"
                available.append(AvailableTime(
                    date=start.strftime("%Y-%m-%d"),
                    time=f"{hour}:00",
                    formatted=f"{prefix}{hour}:00"
                ))
                if len(available) >= 3:
                    return available

        return available
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"查询医生可用时段失败: {e}")
        return []


def _empty_analysis() -> PatientAnalysisResponse:
    """无行为数据时的空画像"""
    return PatientAnalysisResponse()


def _resolve_doctor_name(doctor_id: Optional[int], current_name: Optional[str]) -> Optional[str]:
    """
    补齐常选医生姓名

    偏好分析器内部的姓名查询走的是默认库路径；这里用 API 层同一套依赖再查一次，
    确保切换 APPOINTMENT_DB_PATH 后页面展示的姓名与医生页一致。
    """
    if current_name or not doctor_id:
        return current_name
    try:
        from api.core.deps import doctor_service

        doctor = doctor_service().get_doctor_by_id(doctor_id)
        return doctor.get("name") if doctor else None
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"补查医生姓名失败: {e}")
        return current_name


async def get_patient_analysis(user_id: str = DEFAULT_PATIENT_ID) -> PatientAnalysisResponse:
    """
    获取患者偏好画像

    没有任何行为记录时返回空对象而不是报错，页面对空数据展示「暂无记录」。
    """
    try:
        analyzer = _build_analyzer()
        preferences = analyzer.analyze_patient_preferences(user_id)
        if not preferences:
            return _empty_analysis()

        # 距上次就诊天数：只用于提醒时机判断，时间解析失败时降级为 None
        days_since_last = None
        last_interview = preferences.get('last_appointment_date')
        if last_interview:
            from datetime import datetime
            if isinstance(last_interview, str):
                last_interview = datetime.fromisoformat(last_interview.replace('Z', '+00:00'))
            if last_interview.tzinfo is not None:
                last_interview = last_interview.replace(tzinfo=None)
            days_since_last = (datetime.now() - last_interview).days

        return PatientAnalysisResponse(
            favorite_doctor_id=preferences.get('favorite_doctor_id'),
            favorite_doctor_name=_resolve_doctor_name(
                preferences.get('favorite_doctor_id'),
                preferences.get('favorite_doctor_name')
            ),
            favorite_position=preferences.get('favorite_position'),
            favorite_round=preferences.get('favorite_round'),
            favorite_mode=preferences.get('favorite_mode'),
            favorite_duration=_parse_duration(preferences.get('favorite_duration'))
            if preferences.get('favorite_duration') else None,
            preferred_time_period=preferences.get('preferred_time_period'),
            total_appointments=preferences.get('total_appointments') or 0,
            days_since_last_interview=days_since_last,
            should_send_reminder=bool(analyzer.should_send_follow_up(user_id))
        )
    except Exception as e:
        # 记录异常并返回空画像，避免分析页因单条脏数据整体 500
        import logging
        logging.getLogger(__name__).error(f"获取患者偏好分析失败: {e}")
        return _empty_analysis()


@router.get("/analysis", response_model=PatientAnalysisResponse, summary="获取患者偏好分析")
async def get_default_patient_analysis():
    """获取默认患者的偏好分析数据"""
    return await get_patient_analysis(DEFAULT_PATIENT_ID)


@router.get("/dashboard_data", response_model=PatientAnalysisResponse, summary="患者分析仪表板数据")
async def get_dashboard_data():
    """患者分析仪表板数据（与 analysis 同源，保留给仪表板组件）"""
    return await get_patient_analysis(DEFAULT_PATIENT_ID)


@router_underscore.get("/analysis", response_model=PatientAnalysisResponse, summary="获取患者偏好分析（下划线前缀）")
async def get_default_patient_analysis_underscore():
    """兼容下划线前缀的患者偏好分析"""
    return await get_patient_analysis(DEFAULT_PATIENT_ID)


@router_underscore.get("/dashboard_data", response_model=PatientAnalysisResponse, summary="患者分析仪表板数据（下划线前缀）")
async def get_dashboard_data_underscore():
    """兼容下划线前缀的仪表板数据"""
    return await get_patient_analysis(DEFAULT_PATIENT_ID)


async def _generate_reminder(user_id: str) -> ReminderResponse:
    """
    生成就诊安排提醒

    先用偏好画像确定「常选医生 + 常用时长」，再查未来两天的可用时段，
    最后交给 LLM 生成提醒文案；LLM 不可用时回退到模板文案。
    """
    analysis = await get_patient_analysis(user_id)

    doctor_id = analysis.favorite_doctor_id
    if not doctor_id:
        message = "您好！目前还没有就诊预约记录，如需安排就诊，我可以按岗位方向为您预约合适的医生。"
        return ReminderResponse(message=message, doctor_available_times=[])

    duration = analysis.favorite_duration or 60
    available_times = _find_available_times(doctor_id, duration)

    # 优先使用 Agent 的个性化文案（内部已做 LLM 失败回退）
    try:
        from agents.patient_behavior_agent import PatientBehaviorAgent
        agent = PatientBehaviorAgent()
        result = await agent.get_reminder_with_schedule(user_id)
        message = (result.get("message") or "").strip()
        # Agent 查不到记录时会返回固定话术；此时本层已有画像，改用下面的模板文案，
        # 避免「有偏好却告知没有记录」的自相矛盾。
        if message and "目前还没有就诊预约记录" not in message:
            return ReminderResponse(
                message=message,
                doctor_available_times=available_times
            )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"LLM 生成就诊提醒失败，使用模板文案: {e}")

    # 模板文案：按偏好拼接，确保没有模型密钥时功能仍可用
    specialty = analysis.favorite_position or "目标岗位"
    round_name = analysis.favorite_round or "就诊"
    period = analysis.preferred_time_period or "合适的时间"
    doctor_name = analysis.favorite_doctor_name or "对应医生"

    message = (
        f"您好！您之前主要申请{specialty}，习惯安排在{period}进行{round_name}，"
        f"常选{doctor_name}医生。如有需要，我可以按同样的偏好为您协调下一轮就诊。"
    )
    return ReminderResponse(message=message, doctor_available_times=available_times)


@router.post("/send-reminder", response_model=ReminderResponse, summary="生成就诊安排提醒")
async def send_reminder(request: ReminderRequest):
    """生成并返回给患者的就诊安排提醒"""
    try:
        return await _generate_reminder(request.user_id)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"生成就诊提醒失败: {e}")
        return ReminderResponse(
            message="您好！系统暂时无法查询医生日程，请稍后再试或直接联系我们安排就诊。",
            doctor_available_times=[]
        )


@router_underscore.post("/send-reminder", response_model=ReminderResponse, summary="生成就诊安排提醒（下划线前缀）")
async def send_reminder_underscore(request: ReminderRequest):
    """兼容下划线前缀的提醒接口"""
    return await send_reminder(request)
