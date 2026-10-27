from dotenv import load_dotenv
import uuid
from langchain_core.chat_history import InMemoryChatMessageHistory
from config.model_provider import create_chat_model
from .appointment import (
    InputParser,
    DoctorFinder,
    AppointmentProcessor,
    MessageBuilder,
    AppointmentDatabase
)
from .appointment.booking_tool_loop import BookingToolLoop, run_with_fallback
from .session_registry import new_appointment_history
from .task_classification.state_manager import StateManager

MAX_TURNS = 12

load_dotenv()


class AppointmentAgent:
    """
    就诊预约机器人主控制器

    职责：
    1. 初始化各个组件
    2. 管理会话状态
    3. 协调整个就诊预约流程
    """

    def __init__(self, session_id=None, unrelated_callback=None, llm=None,
                 doctor_finder=None, tool_loop=None, timeout_seconds: float = 20.0):
        # 基础设置
        self.session_id = session_id or str(uuid.uuid4())
        self.unrelated_callback = unrelated_callback
        self.state = None
        self._turn_count = 0

        # 初始化LLM。测试注入假模型，避免在导入或单测里访问真实接口。
        self.llm = llm if llm is not None else self._initialize_llm()

        # 初始化组件
        self.input_parser = InputParser(self.llm)
        self.doctor_finder = doctor_finder or DoctorFinder()
        self.message_builder = MessageBuilder()
        self.appointment_database = AppointmentDatabase()
        self.appointment_processor = AppointmentProcessor(
            self.input_parser,
            self.doctor_finder,
            self.message_builder,
            self.appointment_database,
            self.llm
        )
        self.tool_loop = tool_loop or BookingToolLoop(
            self.llm,
            self.doctor_finder,
            timeout_seconds=timeout_seconds,
            on_finish=self._finish_from_tool,
            slot_search=self._search_alternative_slots,
            slot_reader=self._read_booking_slots,
        )

        # 会话管理
        self.chats_by_session_id = {}
        self.chat_history = self._get_chat_history(self.session_id)

        # 就诊预约状态
        self.reset()

    def _initialize_llm(self):
        """初始化通用聊天模型"""
        return create_chat_model(temperature=0)

    def _get_chat_history(self, session_id: str) -> InMemoryChatMessageHistory:
        """获取或创建会话历史记录"""
        chat_history = self.chats_by_session_id.get(session_id)
        if chat_history is None:
            chat_history = InMemoryChatMessageHistory()
            self.chats_by_session_id[session_id] = chat_history
        return chat_history

    def reset(self):
        """重置当前绑定的就诊预约历史。清空原字典，避免和会话登记表脱节。"""
        blank = new_appointment_history()
        if getattr(self, "appointment_history", None) is None:
            self.appointment_history = blank
        else:
            self.appointment_history.clear()
            self.appointment_history.update(blank)
        self.finished = False
        if self.chat_history is not None:
            self.chat_history.clear()

    def bind_session(self, record):
        """把本轮请求切到指定 session 的槽位、聊天记录和共享状态。"""
        self.session_id = record.session_id
        self._session_record = record
        self.appointment_history = record.appointment_history
        self.chat_history = record.chat_history
        self.state = record.shared_state
        self.chats_by_session_id[record.session_id] = record.chat_history

    def set_shared_state(self, shared_state):
        """设置共享状态"""
        self.state = shared_state

    async def _read_booking_slots(self, user_input: str, history: dict) -> dict:
        """用和回退流程同一套 InputParser 抽出本轮槽位，不写入聊天记录。"""
        import json

        known = {
            key: history.get(key)
            for key in (
                "patient_name", "specialty", "visit_type", "channel",
                "start_time", "duration", "doctor_name",
            )
        }
        message = await self.input_parser.chain.ainvoke({
            "history": json.dumps(known, ensure_ascii=False, default=str),
            "user_input": user_input,
        })
        content = getattr(message, "content", message)
        if isinstance(content, list):
            content = "".join(str(part) for part in content)
        return self.input_parser.parse_data(str(content))

    def _search_alternative_slots(self, history):
        from .appointment.alternative_slots import slots_for_history
        return slots_for_history(self.doctor_finder, history)

    async def _finish_from_tool(self, observation, history) -> str:
        """策略选定 finish 后，沿用现有落库路径。"""
        doctor = (observation or {}).get("doctor")
        if not doctor:
            return "[REPLY][预约助手]还不能结束，我还没有查到可约的医生。"
        reply = await self.appointment_processor._process_successful_appointment(
            doctor, history, self.session_id
        )
        return f"[REPLY][预约助手]{reply}"

    async def _legacy_stream(self, user_input):
        async for token in self._run_stream_fallback(user_input):
            yield token

    async def run_stream(self, user_input=None):
        """
        流式处理患者就诊预约请求的主函数

        先走有界工具环。模型超时或连续非法参数时，回退到原来的抽槽流程。
        """
        if user_input is None:
            user_input = input("患者：")

        if self._consume_turn() >= MAX_TURNS:
            yield format_turn_limit(self.appointment_history)
            return

        async for token in run_with_fallback(
            self.tool_loop, self._legacy_stream, user_input, self.appointment_history
        ):
            yield token

    async def _run_stream_fallback(self, user_input=None):
        """原来的抽槽、追问、落库流程，只在工具环回退时使用。"""
        if user_input is None:
            user_input = input("患者：")

        # 1. 解析患者输入（内部 JSON，不向用户流式输出，避免英文字段名暴露在聊天界面）
        ai_content = ""
        try:
            for token in self.input_parser.parse_stream(user_input, self.chat_history):
                ai_content += token

            # 2. 解析AI返回的数据
            data = self.input_parser.parse_data(ai_content)
            self.finished = self.appointment_processor.update_history_from_data(
                self.appointment_history, data
            )

            # 3. 处理与就诊预约无关的请求
            # 如果正在等待患者确认推荐医生，不要转交给归类机器人
            if data.get("unrelated", False) and not self.appointment_history.get('awaiting_confirmation'):
                # 这里不清空就诊历史，保留患者已输入的信息
                # 只设置状态为CLASSIFY，让系统转交给其他机器人处理
                self._return_to_classify()

                async for token in self.appointment_processor.handle_unrelated_request(
                    user_input, self.unrelated_callback, self.state
                ):
                    yield token
                return

            # 4. 处理就诊信息完整的情况；等待确认时也走该分支，避免把非确认输入当成信息缺失
            if self.finished or self.appointment_history.get('awaiting_confirmation'):
                recommendation_pending = False
                async for token in self.appointment_processor.handle_complete_appointment(
                    self.appointment_history, self.session_id
                ):
                    # 检查是否有推荐等待确认
                    if token == "[SIGNAL]recommendation_pending":
                        recommendation_pending = True
                        # 将 finished 设为 False，让就诊预约流程继续
                        self.finished = False
                        continue
                    yield token

                # 只有在真正完成预约时才重置状态；失败时保留已填写的信息便于重试
                if not recommendation_pending and not self.appointment_history.get('awaiting_confirmation'):
                    if self.appointment_history.get('booking_failed'):
                        self.appointment_history['booking_failed'] = False
                        self.finished = False
                    else:
                        self._reset_state_after_interview()
                return

            # 5. 处理信息不完整的情况
            async for token in self.appointment_processor.handle_incomplete_info(
                data, self.appointment_history
            ):
                yield token

        except Exception as e:
            print(f"处理就诊预约请求失败: {e}")
            yield self.message_builder.create_parse_error_message()

    async def run(self, user_input=None) -> str:
        """
        非流式入口：收集 run_stream 的全部输出并返回

        供同步任务分类流程和简单脚本调用，流式协议仍以 run_stream 为准
        """
        chunks = []
        async for token in self.run_stream(user_input=user_input):
            chunks.append(token)
        return "".join(chunks)

    def _consume_turn(self) -> int:
        record = getattr(self, "_session_record", None)
        if record is not None:
            record.turn_count += 1
            return record.turn_count
        self._turn_count += 1
        return self._turn_count

    def _return_to_classify(self):
        if self.state is None:
            return
        from config.constants import StateEnum
        StateManager(self.state).set_state(StateEnum.CLASSIFY)

    def _reset_state_after_interview(self):
        """就诊预约完成后重置状态"""
        self.reset()
        self._return_to_classify()


def format_turn_limit(history) -> str:
    """回合用尽时只汇报已经记下的槽位。"""
    labels = (
        ("patient_name", "姓名"),
        ("specialty", "科室"),
        ("visit_type", "就诊类型"),
        ("channel", "渠道"),
        ("start_time", "时间"),
        ("duration", "时长"),
        ("doctor_name", "医生"),
    )
    parts = []
    for key, label in labels:
        value = (history or {}).get(key)
        if value and value != "未知":
            parts.append(f"{label}{value}")
    collected = "、".join(parts) if parts else "还没有记下槽位"
    return f"[REPLY][预约助手]对话已到{MAX_TURNS}轮上限。已收集：{collected}。先停在这里。"
