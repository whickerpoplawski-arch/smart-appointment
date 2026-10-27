"""
医生查找器

负责根据患者的岗位、偏好与时间查找合适的医生：
1. 指定医生时先查该医生是否空闲，忙碌则按简介相似度推荐同方向空闲医生
2. 未指定医生时，按「岗位方向 + 该时段空闲」筛选，偏好仅用于排序
"""

import logging
import re
from typing import Optional, Dict, Any, Callable, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# 中文数字在时长里的常用写法
CHINESE_NUMERALS = {
    "一": "1", "两": "2", "二": "2", "三": "3", "四": "4", "五": "5",
    "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
}

# 指定医生忙碌时，最多返回的替代人选数量（列表越长，小模型越容易挑错或复述错名字）
ALTERNATIVES_LIMIT = 3


def extract_duration_minutes(duration_value: Any) -> Optional[int]:
    """
    从患者给出的时长中提取分钟数

    支持「45分钟」「1小时」「1.5小时」「1小时30分钟」「半小时」「60」等写法，
    无法识别时返回 None，由调用方决定是否使用轮次默认值。
    """
    if duration_value in (None, "", "未知"):
        return None

    text = str(duration_value).strip()
    if "半" in text and "小时" in text:
        return 30

    # 兼容「一小时」「两小时」「十分钟」等中文数字写法
    text = re.sub(
        r'([一二两三四五六七八九十])\s*(?:个)?(小时|分钟)',
        lambda match: CHINESE_NUMERALS.get(match.group(1), match.group(1)) + match.group(2),
        text
    )

    hours_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:个)?小时', text)
    minutes_match = re.search(r'(\d+)\s*分钟', text)

    total_minutes = 0.0
    if hours_match:
        total_minutes += float(hours_match.group(1)) * 60
    if minutes_match:
        total_minutes += int(minutes_match.group(1))

    if total_minutes == 0:
        # 纯数字按分钟处理
        number_match = re.search(r'\d+(?:\.\d+)?', text)
        if not number_match:
            return None
        total_minutes = float(number_match.group())

    minutes = int(round(total_minutes))
    return minutes if minutes > 0 else None


# 兼容常见的时间书写格式，避免 LLM 输出带秒或 ISO 分隔符时解析失败
START_TIME_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%dT%H:%M:%S",
)


class DoctorFinder:
    """医生查找器"""

    def __init__(self, appointment_service=None):
        """
        初始化医生查找器

        Args:
            appointment_service: 就诊服务实例，传入时便于测试替换
        """
        self._appointment_service = appointment_service

    @property
    def appointment_service(self):
        """懒加载就诊服务，避免导入期依赖数据库"""
        if self._appointment_service is None:
            from services.appointment_service import AppointmentService
            self._appointment_service = AppointmentService()
        return self._appointment_service

    def parse_time_and_duration(self, start_time_str: str, duration_str: str,
                                default_duration: Optional[int] = None) -> tuple:
        """
        解析就诊开始时间与时长

        Args:
            start_time_str: 标准格式的开始时间字符串
            duration_str: 时长字符串，如「45分钟」
            default_duration: 时长未知时使用的默认分钟数

        Returns:
            (start_time, end_time, duration_min)，无法解析时返回 (None, None, None)
        """
        if not start_time_str or start_time_str == "未知":
            return None, None, None

        try:
            start_time = self._parse_start_time(start_time_str)
            if start_time is None:
                return None, None, None

            duration_min = self._extract_minutes(duration_str)
            if duration_min is None:
                duration_min = default_duration
            if not duration_min or duration_min <= 0:
                return None, None, None

            end_time = start_time + timedelta(minutes=duration_min)
            return start_time, end_time, duration_min
        except Exception:
            return None, None, None

    @staticmethod
    def _parse_start_time(start_time_str: str) -> Optional[datetime]:
        """解析就诊开始时间，兼容带秒和 ISO 分隔符的写法"""
        from config.time_config import time_config

        text = str(start_time_str).strip()
        for format_str in START_TIME_FORMATS:
            try:
                start_time = datetime.strptime(text, format_str)
                return start_time.replace(tzinfo=time_config.BEIJING_TZ)
            except ValueError:
                continue

        # 最后回退到项目统一的时间解析
        try:
            start_time = datetime.fromisoformat(text)
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=time_config.BEIJING_TZ)
            return start_time
        except ValueError:
            return time_config.parse_datetime(text)

    @staticmethod
    def _extract_minutes(duration_str: str) -> Optional[int]:
        """从时长字符串中提取分钟数，无法提取时返回 None"""
        return extract_duration_minutes(duration_str)

    def _rank_by_similarity(self, query: str, candidates: List[Dict[str, Any]],
                            yield_func: Optional[Callable] = None) -> List[Dict[str, Any]]:
        """
        按简介相似度对患者选排序

        文本嵌入不可用时退化为原始顺序，保证就诊预约不被推荐环节阻断。
        """
        if len(candidates) <= 1:
            return candidates

        texts = [self._doctor_text(item) for item in candidates]
        if not any(text.strip() for text in texts):
            return candidates

        try:
            from services.text_embedding import find_best_match_indices
            indices = find_best_match_indices(query, texts)
            ordered = [candidates[i] for i in indices if 0 <= i < len(candidates)]
            # 补齐可能被去重的候选项，避免遗漏
            ordered_ids = {item.get('id') for item in ordered}
            ordered.extend(item for item in candidates if item.get('id') not in ordered_ids)
            return ordered
        except Exception as e:
            logger.info("简介相似度排序不可用（%s），按默认顺序查找", e)
            return candidates

    @staticmethod
    def _doctor_text(doctor: Dict[str, Any]) -> str:
        """拼接医生的方向与简介，作为相似度匹配文本"""
        department = doctor.get('department') or ''
        bio = doctor.get('bio') or ''
        return f"{department} {bio}".strip()

    def resolve_available(self, start_time_str: str, duration_str: Optional[str] = None,
                          specialty: Optional[str] = None,
                          doctor_name: Optional[str] = None,
                          preference: Optional[str] = None,
                          default_duration: Optional[int] = None) -> Dict[str, Any]:
        """
        医生可约性查询的唯一实现（Agent 工具 find_doctor 的核心）

        指定姓名时只校验此人（忙碌则附同方向替代者）；未指定时按岗位方向搜索。
        返回四种状态之一：
        - available：附带 doctor
        - unavailable：附带 requested_doctor 与 alternatives（最多 3 位）
        - not_found：指定姓名不存在
        - no_match：按条件搜不到可约医生
        时间或时长无法解析时返回 invalid_time，由调用方按参数错误处理。
        """
        start_time, end_time, _ = self.parse_time_and_duration(
            start_time_str, duration_str, default_duration=default_duration
        )
        if not start_time or not end_time:
            return {
                "status": "invalid_time",
                "detail": {"start_time": start_time_str, "duration": duration_str},
            }

        if doctor_name and doctor_name != "未知":
            target = self.appointment_service.get_doctor_by_name(doctor_name)
            if not target:
                return {"status": "not_found", "requested_doctor_name": doctor_name}

            if self.appointment_service.is_doctor_available(target["id"], start_time, end_time):
                return {"status": "available", "doctor": target}

            return {
                "status": "unavailable",
                "requested_doctor": target,
                "alternatives": self._collect_alternatives(target, start_time, end_time),
            }

        department = self.appointment_service.match_department(specialty) if specialty else None
        if not department:
            return {
                "status": "no_match",
                "department": None,
                "searched_specialty": specialty,
                "needs_specialty": True,
            }
        candidates = self.appointment_service.get_doctors_by_department(department)

        doctor = self._pick_available(candidates, start_time, end_time, preference)
        if doctor:
            return {"status": "available", "doctor": doctor}

        return {"status": "no_match", "department": department, "searched_specialty": specialty}

    def _collect_alternatives(self, target_doctor: Dict[str, Any], start_time: datetime,
                              end_time: datetime) -> List[Dict[str, Any]]:
        """
        构造指定医生忙碌时的同方向替代者

        规则：同科室 + 该时段空闲 → 排除本人 → 按简介相似度排序 → 最多取 ALTERNATIVES_LIMIT 位。
        """
        department = target_doctor.get('department')
        available = self.appointment_service.get_available_doctors(
            start_time, end_time, department=department
        )
        others = [item for item in available
                  if item.get('id') != target_doctor.get('id')]
        if not others:
            return []

        query = target_doctor.get('bio') or department or ''
        ordered = self._rank_by_similarity(query, others)
        return ordered[:ALTERNATIVES_LIMIT]

    def _pick_available(self, candidates: List[Dict[str, Any]], start_time: datetime,
                        end_time: datetime,
                        preference: Optional[str] = None) -> Optional[Dict]:
        """在候选医生中按偏好顺序找出第一位空闲者"""
        if preference and preference not in ("无", "未知"):
            candidates = self._rank_by_similarity(preference, candidates)

        for doctor in candidates:
            if self.appointment_service.is_doctor_available(
                doctor["id"], start_time, end_time
            ):
                return doctor
        return None

    def find_doctor_with_thought(self, appointment_history: Dict[str, Any],
                                      yield_func: Optional[Callable] = None,
                                      default_duration: Optional[int] = None) -> Dict[str, Any]:
        """
        带思考提示的医生检索入口

        内部走统一的 find_doctor 工具，再映射回历史 status 取值：
        - available：可直接预约，附带医生字段
        - recommendation：需要患者确认推荐，附带 original_doctor / recommended_doctor / alternatives
        - doctor_not_found：指定医生不存在
        - conflict：指定医生忙碌且无同方向可替代者
        - no_available_doctor：岗位方向下没有空闲医生
        - invalid_time：时间或时长无法解析
        """
        specialty = appointment_history.get("specialty")
        start_time_str = appointment_history.get("start_time")
        duration_str = appointment_history.get("duration")
        doctor_name = appointment_history.get("doctor_name")

        if doctor_name and doctor_name != "未知":
            logger.info("患者指定了医生：%s，正在查询其日程", doctor_name)
        else:
            logger.info("正在按岗位方向查找空闲医生")

        from agents.appointment.doctor_tools import find_doctor

        preference = appointment_history.get("preference")
        result = find_doctor(
            {
                "specialty": None if specialty == "未知" else specialty,
                "start_time": None if start_time_str == "未知" else start_time_str,
                "duration_minutes": extract_duration_minutes(duration_str),
                "doctor_name": None if doctor_name == "未知" else doctor_name,
            },
            finder=self,
            default_duration=default_duration,
            preference=None if preference == "未知" else preference,
        )
        status = result.get("status")

        if status == "available":
            doctor = result["doctor"]
            logger.info("%s在指定时间有空", doctor["name"])
            payload = dict(doctor)
            payload["status"] = "available"
            payload["requires_confirmation"] = False
            return payload

        if status == "unavailable":
            original = result["requested_doctor"]
            alternatives = result.get("alternatives") or []
            if not alternatives:
                logger.info("同方向下没有其他空闲医生")
                return {"status": "conflict", "original_doctor": original, "alternatives": []}

            names = "、".join(item["name"] for item in alternatives)
            logger.info("%s该时段已有安排，同方向可约：%s", original["name"], names)
            return {
                "status": "recommendation",
                "is_recommendation": True,
                "original_doctor": original,
                "recommended_doctor": alternatives[0],
                "alternatives": alternatives,
                "requires_confirmation": True,
            }

        if status == "not_found":
            logger.info("未找到名为'%s'的医生", result["requested_doctor_name"])
            return {
                "status": "doctor_not_found",
                "requested_doctor": result["requested_doctor_name"],
            }

        if status == "invalid_time" or status == "invalid_arguments":
            logger.info("就诊时间或时长信息不完整，无法检索医生")
            return {"status": "invalid_time"}

        if result.get("needs_specialty"):
            logger.info("原话里没有科室，请患者选择科室")
            return {"status": "needs_specialty", "searched_specialty": result.get("searched_specialty")}

        logger.info("没有找到空闲医生")
        return {"status": "no_available_doctor", "department": result.get("department")}

    @staticmethod
    def normalize_visit_type_name(round_name: str) -> str:
        """把轮次别名归一到默认时长表的键（面/试 后缀差异都兼容）"""
        text = (round_name or "").strip()
        if not text or text == "未知":
            return ""
        aliases = {"首诊": "初诊", "第一次": "初诊", "复查": "复诊", "再次": "复诊", "问诊": "咨询"}
        return aliases.get(text, text)

