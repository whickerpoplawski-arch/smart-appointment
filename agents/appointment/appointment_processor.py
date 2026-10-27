"""
就诊预约处理器

负责协调整个就诊预约流程：信息收集、医生匹配、确认交互与落库
"""

import logging
from typing import Dict, Any, AsyncGenerator, Optional

logger = logging.getLogger(__name__)
from .input_parser import InputParser
from .doctor_finder import DoctorFinder, extract_duration_minutes
from .message_builder import MessageBuilder
from .appointment_database import AppointmentDatabase


# 就诊类型未指定时长时的默认分钟数。认不出时用 30。
DEFAULT_DURATION_BY_VISIT = {
    "初诊": 30,
    "复诊": 15,
    "咨询": 20,
}
DEFAULT_DURATION_MINUTES = 30

# 就诊类型与渠道的常见说法归一化
VISIT_TYPE_ALIASES = {
    "初诊": "初诊", "首诊": "初诊", "第一次": "初诊",
    "复诊": "复诊", "复查": "复诊", "再次": "复诊",
    "咨询": "咨询", "问诊": "咨询",
}
CHANNEL_ALIASES = {
    "门诊": "门诊", "线下": "门诊", "现场": "门诊", "到院": "门诊", "到医院": "门诊",
    "互联网医院": "互联网医院", "线上": "互联网医院", "视频": "互联网医院", "远程": "互联网医院",
}

# 会话信息字段
HISTORY_FIELDS = [
    "patient_name", "specialty", "visit_type", "channel",
    "start_time", "duration", "doctor_name", "preference"
]


class AppointmentProcessor:
    """就诊预约处理器"""

    def __init__(self, input_parser: InputParser, doctor_finder: DoctorFinder,
                 message_builder: MessageBuilder, appointment_database: AppointmentDatabase, llm=None):
        self.input_parser = input_parser
        self.doctor_finder = doctor_finder
        self.message_builder = message_builder
        self.appointment_database = appointment_database
        self.llm = llm

    # ------------------------------------------------------------------
    # 字段归一化
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_visit_type(round_value: str) -> str:
        """归一化就诊类型，无法识别时保留原值（可能为「未知」）"""
        if not round_value or round_value == "未知":
            return "未知"
        key = str(round_value).strip().lower().replace(" ", "")
        return VISIT_TYPE_ALIASES.get(key, str(round_value).strip())

    @staticmethod
    def normalize_channel(mode_value: str) -> str:
        """归一化就诊形式，无法识别时保留原值（可能为「未知」）"""
        if not mode_value or mode_value == "未知":
            return "未知"

        text = str(mode_value).strip()
        key = text.lower()
        if key in CHANNEL_ALIASES:
            return CHANNEL_ALIASES[key]

        # 兼容「视频会议」「到公司现场」等带修饰的说法
        if any(word in text for word in ("互联网医院", "线上", "视频", "远程")):
            return "互联网医院"
        if any(word in text for word in ("门诊", "线下", "现场", "到院", "到医院")):
            return "门诊"
        return text

    @staticmethod
    def resolve_duration(round_name: str, duration_value: Any) -> Optional[int]:
        """
        计算就诊时长：患者明确给出时优先，否则按轮次取默认值

        Returns:
            分钟数；轮次与时长都无法识别时返回 None
        """
        if duration_value not in (None, "", "未知"):
            minutes = extract_duration_minutes(duration_value)
            if minutes:
                return minutes

        # 轮次可能带空格或别名（如「HR 面」「初面」），先归一化再取默认值
        normalized_round = AppointmentProcessor.normalize_visit_type(round_name or "")
        return DEFAULT_DURATION_BY_VISIT.get(normalized_round, DEFAULT_DURATION_MINUTES)

    def _prepare_defaults(self, appointment_history: Dict[str, Any]):
        """补齐默认形式与时长，写回会话历史供匹配与落库使用"""
        channel = self.normalize_channel(appointment_history.get("channel"))
        if channel == "未知":
            channel = "门诊"
        appointment_history["channel"] = channel

        minutes = self.resolve_duration(appointment_history.get("visit_type"),
                                        appointment_history.get("duration"))
        if minutes:
            appointment_history["duration"] = f"{minutes}分钟"

    # ------------------------------------------------------------------
    # 会话历史更新
    # ------------------------------------------------------------------

    def update_history_from_data(self, appointment_history: Dict[str, Any],
                                 data: Dict[str, Any]) -> bool:
        """从解析数据更新就诊历史，返回必填信息是否已齐全"""
        # 检查是否在等待患者对推荐医生的确认
        if appointment_history.get('awaiting_confirmation'):
            return self._handle_recommendation_response(appointment_history, data)

        # 只更新有值的字段，避免覆盖之前的信息
        for key in HISTORY_FIELDS:
            value = data.get(key)
            if not value or value == "未知":
                continue
            if key == "visit_type":
                value = self.normalize_visit_type(value)
            elif key == "channel":
                value = self.normalize_channel(value)
            appointment_history[key] = value

        # 必填：患者姓名、就诊科室、就诊类型、开始时间
        required_fields = ["patient_name", "specialty", "visit_type", "start_time"]
        return all(
            appointment_history.get(field) and appointment_history[field] != "未知"
            for field in required_fields
        )

    def _handle_recommendation_response(self, appointment_history: Dict[str, Any],
                                        data: Dict[str, Any]) -> bool:
        """处理患者对推荐医生的回应"""
        user_response = str(data.get('confirmation', '') or '').strip().lower()
        if not user_response or user_response == "未知":
            return False

        # 判断患者是否同意推荐
        positive_responses = ['是', '好', '可以', '同意', '确定', '行', '没问题', 'yes', 'ok']
        negative_responses = ['不', '不要', '不行', '不同意', '换', '改期', 'no']

        is_positive = any(pos in user_response for pos in positive_responses)
        is_negative = any(neg in user_response for neg in negative_responses)

        if is_positive and not is_negative:
            # 患者同意推荐，记录待确认的医生
            recommended_doctor = appointment_history.get('recommended_doctor')
            if recommended_doctor:
                appointment_history['confirmed_doctor'] = recommended_doctor
                appointment_history['awaiting_confirmation'] = False
                return True
        elif is_negative:
            # 患者拒绝推荐
            appointment_history['recommendation_declined'] = True
            appointment_history['awaiting_confirmation'] = False
            return True

        # 回应不明确，继续等待
        return False

    # ------------------------------------------------------------------
    # 流程处理
    # ------------------------------------------------------------------

    async def handle_unrelated_request(self, user_input: str, unrelated_callback,
                                       state) -> AsyncGenerator[str, None]:
        """处理与就诊预约无关的请求，保留已收集的就诊信息"""
        if unrelated_callback:
            try:
                yield "[REPLY][预约助手]和就诊预约信息无关，已交给归类机器人处理\n"
                result = await unrelated_callback(user_input, depth=1)
                if hasattr(result, '__aiter__'):
                    async for token in result:
                        yield token
                else:
                    yield result
            except Exception as e:
                yield f"[ERROR]处理请求时发生错误: {str(e)}\n"
                yield self.message_builder.create_unrelated_message()
        else:
            yield self.message_builder.create_unrelated_message()

    async def handle_complete_appointment(self, appointment_history: Dict[str, Any],
                                        session_id: str) -> AsyncGenerator[str, None]:
        """处理就诊信息完整的情况"""
        # 患者拒绝了推荐
        if appointment_history.get('recommendation_declined'):
            reply = self.message_builder.create_recommendation_declined_message(self.llm)
            yield f"[REPLY][预约助手]{reply}"
            self._clear_recommendation_state(appointment_history)
            return

        # 患者确认了推荐医生
        if appointment_history.get('confirmed_doctor'):
            doctor = appointment_history['confirmed_doctor']
            reply = await self._process_successful_appointment(doctor, appointment_history, session_id)
            yield f"[REPLY][预约助手]{reply}"
            self._clear_recommendation_state(appointment_history)
            return

        # 仍在等待确认，但本轮没有识别出明确回复
        if appointment_history.get('awaiting_confirmation'):
            yield f"[REPLY][预约助手]{self.message_builder.create_confirmation_hint_message()}"
            return

        # 补齐默认形式与时长后再匹配
        self._prepare_defaults(appointment_history)

        # 收集思考过程
        thought_msgs = []
        result = self.doctor_finder.find_doctor_with_thought(
            appointment_history, thought_msgs.append,
            default_duration=self.resolve_duration(appointment_history.get("visit_type"), None)
        )
        for msg in thought_msgs:
            yield msg

        status = result.get('status')

        if status == 'recommendation':
            original_doctor = result.get('original_doctor')
            recommended_doctor = result.get('recommended_doctor')

            recommendation_msg = self.message_builder.create_doctor_recommendation_message(
                original_doctor, recommended_doctor, appointment_history, self.llm
            )
            yield f"[REPLY][预约助手]{recommendation_msg}"

            # 存储推荐信息，等待患者确认
            appointment_history['recommended_doctor'] = recommended_doctor
            appointment_history['original_doctor'] = original_doctor
            appointment_history['awaiting_confirmation'] = True

            # 告诉调用方预约尚未真正完成，需要继续等待患者输入
            yield "[SIGNAL]recommendation_pending"
            return

        if status == 'available':
            reply = await self._process_successful_appointment(result, appointment_history, session_id)
            yield f"[REPLY][预约助手]{reply}"
            return

        # 查找失败：保留已收集信息，给出准确原因
        appointment_history['booking_failed'] = True
        failure_msg = self.message_builder.create_appointment_failure_message(result, appointment_history)
        yield f"[REPLY][预约助手]{failure_msg}"

    @staticmethod
    def _clear_recommendation_state(appointment_history: Dict[str, Any]):
        """清理推荐确认相关的临时状态"""
        for key in ('recommendation_declined', 'confirmed_doctor',
                    'recommended_doctor', 'original_doctor'):
            appointment_history.pop(key, None)

    async def _process_successful_appointment(self, doctor: Dict[str, Any],
                                            appointment_history: Dict[str, Any],
                                            session_id: str) -> str:
        """处理就诊预约成功的情况。门诊的出行提醒写在成功文案里，不另起工具代理。"""
        minutes = self.resolve_duration(appointment_history.get("visit_type"),
                                        appointment_history.get("duration"))
        start_time, end_time, _ = self.doctor_finder.parse_time_and_duration(
            appointment_history.get("start_time"),
            appointment_history.get("duration"),
            default_duration=minutes
        )

        if not start_time or not end_time:
            appointment_history['booking_failed'] = True
            return self.message_builder.create_save_failure_message("就诊时间无法识别")

        success, reason = self.appointment_database.save_appointment(
            doctor["id"], start_time, end_time, appointment_history, session_id
        )
        if not success:
            appointment_history['booking_failed'] = True
            return self.message_builder.create_save_failure_message(reason)

        appointment_history['booking_failed'] = False
        return self.message_builder.create_appointment_success_message(
            doctor, appointment_history
        )

    async def handle_incomplete_info(self, data: Dict[str, Any],
                                     appointment_history: Optional[Dict[str, Any]] = None
                                     ) -> AsyncGenerator[str, None]:
        """处理信息不完整的情况，逐项追问缺失的必填信息"""
        appointment_history = appointment_history if appointment_history is not None else data

        missing = []
        for field, label in (
            ("patient_name", "患者姓名"),
            ("specialty", "就诊科室"),
            ("visit_type", "就诊类型"),
            ("start_time", "就诊时间")
        ):
            value = appointment_history.get(field)
            if not value or value == "未知":
                missing.append(field)

        logger.info("就诊信息不完整，缺少：%s", ", ".join(missing))
        reply = self.message_builder.create_missing_info_questions(missing)
        yield f"[REPLY][预约助手]{reply}"
