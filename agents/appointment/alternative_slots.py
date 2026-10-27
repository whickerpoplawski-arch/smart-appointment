"""域内改期：未来两个工作日里最多给出两个可约时段。"""

from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from config.time_config import time_config

MAX_ALTERNATIVE_SLOTS = 2
BUSINESS_DAY_COUNT = 2
WORK_START_HOUR = 9
WORK_END_HOUR = 18


def iter_business_days(now: datetime, count: int = BUSINESS_DAY_COUNT) -> List[datetime]:
    """从 now 所在日期起，取接下来 count 个工作日（含今天，若今天是工作日）。"""
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    cursor = now.replace(hour=0, minute=0, second=0, microsecond=0)
    found = []
    while len(found) < count:
        if cursor.weekday() < 5:
            found.append(cursor)
        cursor = cursor + timedelta(days=1)
    return found


def search_alternative_slots(now: datetime, duration_minutes: int,
                             is_available: Callable[[datetime, datetime], bool],
                             limit: int = MAX_ALTERNATIVE_SLOTS) -> List[dict]:
    """按整点扫描。is_available(start, end) 为真才收下，最多返回 limit 个。"""
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    duration_minutes = int(duration_minutes)
    options = []
    for day in iter_business_days(now):
        for hour in range(WORK_START_HOUR, WORK_END_HOUR):
            start = day.replace(hour=hour)
            if start <= now:
                continue
            end = start + timedelta(minutes=duration_minutes)
            if not is_available(start, end):
                continue
            options.append({
                "start_time": start.strftime("%Y-%m-%d %H:%M"),
                "duration_minutes": duration_minutes,
                "label": start.strftime("%Y-%m-%d %H:%M"),
            })
            if len(options) >= limit:
                return options
    return options


def _naive_beijing(moment: datetime) -> datetime:
    """搜索用无时区的北京时间，避免 aware/naive 比大小失败。"""
    if moment.tzinfo is not None:
        moment = moment.astimezone(time_config.BEIJING_TZ).replace(tzinfo=None)
    return moment.replace(microsecond=0)


def requested_start(history: Optional[Dict]) -> Optional[datetime]:
    """解析 history.start_time。空白、未知或无法解析时返回 None。"""
    raw = (history or {}).get("start_time")
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "" or text == "未知" or text.lower() == "null":
        return None
    from agents.appointment.doctor_finder import DoctorFinder

    parsed = DoctorFinder._parse_start_time(text)
    if parsed is None:
        return None
    return _naive_beijing(parsed)


def requested_day_label(value: Optional[str]) -> Optional[str]:
    """请求日 YYYY-MM-DD。解析失败时不编一个日期。"""
    parsed = requested_start({"start_time": value})
    if parsed is None:
        return None
    return parsed.strftime("%Y-%m-%d")


def search_lower_bound(history: Optional[Dict], now: Optional[datetime] = None) -> datetime:
    """请求时刻能解析且晚于墙钟时，从请求时刻起搜；否则用墙钟。"""
    wall = _naive_beijing(now or time_config.now())
    requested = requested_start(history)
    if requested is not None and requested > wall:
        return requested
    return wall


def slots_for_history(finder, history: Dict, now: Optional[datetime] = None) -> List[dict]:
    """用轮次默认时长找改期选项。点名医生时只给这位医生有空的整点。"""
    from agents.appointment.appointment_processor import DEFAULT_DURATION_MINUTES, AppointmentProcessor
    from agents.appointment.message_builder import slot_is_blank

    duration = AppointmentProcessor.resolve_duration(history.get("visit_type"), history.get("duration"))
    if not duration:
        duration = DEFAULT_DURATION_MINUTES
    moment = search_lower_bound(history, now)
    service = finder.appointment_service

    def is_available(start: datetime, end: datetime) -> bool:
        name = history.get("doctor_name")
        if not slot_is_blank(name):
            person = service.get_doctor_by_name(name)
            if not person:
                return False
            return bool(service.is_doctor_available(person["id"], start, end))
        department = None
        if hasattr(service, "match_department"):
            department = service.match_department(history.get("specialty"))
        return bool(service.get_available_doctors(start, end, department))

    return search_alternative_slots(moment, duration, is_available)
