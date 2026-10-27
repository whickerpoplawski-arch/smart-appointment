"""就诊预约的有界工具环。

模型每轮只能选 ask_user / find_doctor / finish。
参数非法最多重填 3 次；超时或连续非法则抛出回退信号，由调用方走 AppointmentAgent 原有 run_stream。
工具结果只经 policy_next_action 决定下一动作。
"""

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, Optional, Union

from services.appointment_service import DEPARTMENT_CHOICES
from agents.appointment.doctor_tools import (
    FIND_DOCTOR_SCHEMA,
    TOOL_ARGUMENT_KEYS,
    TOOL_STATUSES,
    _validate_arguments,
    find_doctor,
)

logger = logging.getLogger(__name__)

ALLOWED_ACTIONS = ("ask_user", "find_doctor", "finish")
MAX_INVALID_ATTEMPTS = 3
SCHEMA_KEYS = tuple(FIND_DOCTOR_SCHEMA["function"]["parameters"]["properties"])

FinishCallback = Callable[[Dict[str, Any], Dict[str, Any]], Union[str, Awaitable[str]]]


class ToolLoopFallback(Exception):
    """连续非法动作或参数，调用方应回退到现有 run_stream。"""


class ToolLoopTimeout(Exception):
    """模型调用超时，调用方应回退到现有 run_stream。"""


def policy_next_action(observation: Dict[str, Any]) -> str:
    """把工具状态映射成下一动作。返回值是 ask_user / finish / refill / search_alternative_slots。"""
    status = observation.get("status")
    if status == "invalid_arguments":
        return "refill"
    if status == "available":
        return "finish"
    if status == "no_match" and observation.get("needs_specialty"):
        return "ask_user"
    if status == "unavailable" and (observation.get("alternatives") or []):
        return "ask_user"
    if status in ("no_match", "unavailable"):
        return "search_alternative_slots"
    if status in TOOL_STATUSES:
        return "ask_user"
    return "ask_user"


def parse_model_action(text: str) -> Optional[Dict[str, Any]]:
    """解析模型输出。动作不在允许集合内时返回 None。"""
    if not text:
        return None
    raw = str(text).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("action") not in ALLOWED_ACTIONS:
        return None
    return payload


def _schema_keys_aligned() -> bool:
    return set(SCHEMA_KEYS) == set(TOOL_ARGUMENT_KEYS)


def reply_for_ask(message: str) -> str:
    text = (message or "").strip() or "请补充就诊预约需要的信息。"
    return f"[REPLY][预约助手]{text}"


def reply_for_observation(observation: Dict[str, Any]) -> str:
    status = observation.get("status")
    if status == "unavailable":
        names = "、".join(
            item.get("name", "") for item in (observation.get("alternatives") or []) if item.get("name")
        )
        if names:
            return f"[REPLY][预约助手]该时段指定医生没空，同方向可改约：{names}。请确认是否接受。"
        return "[REPLY][预约助手]该时段没有可替代的医生。"
    if status == "not_found":
        name = observation.get("requested_doctor_name") or "这位医生"
        return f"[REPLY][预约助手]没有找到{name}，请换一位，或让我按科室推荐。"
    if status == "invalid_time":
        return "[REPLY][预约助手]就诊时间没能识别，请给一个具体的开始时间。"
    if status == "no_match" and observation.get("needs_specialty"):
        names = "、".join(DEPARTMENT_CHOICES)
        return f"[REPLY][预约助手]请先选择科室：{names}。"
    if status == "no_match":
        return "[REPLY][预约助手]这个时段没有空闲医生。"
    return "[REPLY][预约助手]请补充预约需要的信息。"


def remember_find_arguments(history: Dict[str, Any], arguments: Dict[str, Any]) -> None:
    if arguments.get("specialty"):
        history["specialty"] = arguments["specialty"]
    if arguments.get("start_time"):
        history["start_time"] = arguments["start_time"]
    if isinstance(arguments.get("duration_minutes"), int):
        history["duration"] = f"{arguments['duration_minutes']}分钟"
    if arguments.get("doctor_name"):
        history["doctor_name"] = arguments["doctor_name"]


class BookingToolLoop:
    """一轮用户消息内的有界动作环。"""

    def __init__(self, llm, finder, timeout_seconds: float = 20.0,
                 on_finish: Optional[FinishCallback] = None,
                 slot_search: Optional[Callable[..., list]] = None):
        self.llm = llm
        self.finder = finder
        self.timeout_seconds = timeout_seconds
        self.on_finish = on_finish
        self.slot_search = slot_search
        self.last_observation: Optional[Dict[str, Any]] = None

    async def run(self, user_input: str, history: Dict[str, Any]) -> AsyncGenerator[str, None]:
        if history.get("awaiting_slot_choice") and history.get("pending_alternative_slots"):
            async for token in self._resolve_slot_choice(user_input, history):
                yield token
            return

        invalid_streak = 0
        while invalid_streak < MAX_INVALID_ATTEMPTS:
            try:
                raw = await self._call_model(user_input, history)
            except (asyncio.TimeoutError, TimeoutError) as exc:
                raise ToolLoopTimeout("模型调用超时") from exc

            action = parse_model_action(raw)
            if action is None:
                invalid_streak += 1
                logger.info("工具环拒绝非法动作，连续第 %s 次", invalid_streak)
                continue

            name = action["action"]
            if name == "ask_user":
                yield reply_for_ask(action.get("message"))
                return

            if name == "finish":
                yield await self._finish(history, self.last_observation)
                return

            arguments = action.get("arguments")
            if not _schema_keys_aligned() or _validate_arguments(arguments):
                invalid_streak += 1
                logger.info("工具环拒绝非法参数，连续第 %s 次", invalid_streak)
                continue

            observation = find_doctor(arguments, self.finder)
            self.last_observation = observation
            if observation.get("status") not in TOOL_STATUSES or observation.get("status") == "invalid_arguments":
                invalid_streak += 1
                logger.info("工具环拒绝非法工具结果，连续第 %s 次", invalid_streak)
                continue

            invalid_streak = 0
            remember_find_arguments(history, arguments)
            async for token in self._apply_policy(observation, history, allow_replan=True):
                yield token
            return

        raise ToolLoopFallback("连续非法动作或参数")

    async def _resolve_slot_choice(self, user_input: str, history: Dict[str, Any]) -> AsyncGenerator[str, None]:
        options = history.get("pending_alternative_slots") or []
        chosen = match_slot_choice(user_input, options)
        if chosen is None:
            yield reply_for_ask("请从给出的两个时段里选一个，回复 1 或 2。")
            return

        arguments = {
            "specialty": history.get("specialty"),
            "start_time": chosen["start_time"],
            "duration_minutes": chosen["duration_minutes"],
            "doctor_name": history.get("doctor_name"),
        }
        if _validate_arguments(arguments):
            yield reply_for_ask("选定的时段还不能查询，请换一个说法。")
            return

        observation = find_doctor(arguments, self.finder)
        self.last_observation = observation
        history["awaiting_slot_choice"] = False
        history["pending_alternative_slots"] = []
        history["start_time"] = chosen["start_time"]
        history["duration"] = f"{chosen['duration_minutes']}分钟"
        async for token in self._apply_policy(observation, history, allow_replan=False):
            yield token

    async def _apply_policy(self, observation: Dict[str, Any], history: Dict[str, Any],
                            allow_replan: bool) -> AsyncGenerator[str, None]:
        next_action = policy_next_action(observation)
        if next_action == "search_alternative_slots" and allow_replan:
            options = self._search_slots(history)
            observation = dict(observation)
            observation["alternative_slots"] = options
            self.last_observation = observation
            history["pending_alternative_slots"] = options
            history["awaiting_slot_choice"] = bool(options)
            yield reply_for_slots(options)
            return
        if next_action == "finish" or (next_action == "search_alternative_slots" and not allow_replan and observation.get("status") == "available"):
            yield await self._finish(history, observation)
            return
        if next_action == "finish":
            yield await self._finish(history, observation)
            return
        yield reply_for_observation(observation)

    def _search_slots(self, history: Dict[str, Any]) -> list:
        if self.slot_search is None:
            return []
        return list(self.slot_search(history) or [])[:2]

    async def _finish(self, history: Dict[str, Any], observation: Optional[Dict[str, Any]]) -> str:
        observation = observation or {}
        if self.on_finish is None:
            doctor = observation.get("doctor") or {}
            name = doctor.get("name") or "医生"
            return f"[REPLY][预约助手]已确定医生{name}。"
        result = self.on_finish(observation, history)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    async def _call_model(self, user_input: str, history: Dict[str, Any]) -> str:
        prompt = (
            "你是就诊预约助手。只输出一个 JSON 对象，不要其他文字。"
            '动作只能是 ask_user、find_doctor、finish。'
            "find_doctor 的 arguments 必须包含 "
            f"{list(SCHEMA_KEYS)}。"
            f"已知槽位：{json.dumps(history, ensure_ascii=False, default=str)}。"
            f"患者说：{user_input}"
        )
        result = await asyncio.wait_for(self.llm.ainvoke(prompt), timeout=self.timeout_seconds)
        content = getattr(result, "content", result)
        if isinstance(content, list):
            content = "".join(str(part) for part in content)
        return str(content)


def match_slot_choice(user_input: str, options: list) -> Optional[Dict[str, Any]]:
    """从用户回复里选出已给出的改期选项。无法识别时返回 None。"""
    if not options:
        return None
    text = (user_input or "").strip()
    for index, option in enumerate(options[:2], start=1):
        start_time = str(option.get("start_time") or "")
        label = str(option.get("label") or "")
        if text in {str(index), f"第{index}个", f"第{index}"}:
            return option
        if start_time and start_time in text:
            return option
        if label and label in text:
            return option
    if text in {"第一个", "前者", "选1", "选一"} and options:
        return options[0]
    if text in {"第二个", "后者", "选2", "选二"} and len(options) > 1:
        return options[1]
    return None


def reply_for_slots(options: list) -> str:
    if not options:
        return "[REPLY][预约助手]未来两个工作日里没有找到可改的时段，请另给一个时间。"
    lines = [f"{index}. {item.get('label') or item.get('start_time')}" for index, item in enumerate(options[:2], start=1)]
    return "[REPLY][预约助手]这个时段约不上。可以改到：" + "；".join(lines) + "。请回复 1 或 2。"


async def run_with_fallback(tool_loop: BookingToolLoop, legacy, user_input: str,
                            history: Dict[str, Any]) -> AsyncGenerator[str, None]:
    """先走工具环；超时或连续非法时改走 legacy（现有 run_stream）。"""
    try:
        async for token in tool_loop.run(user_input, history):
            yield token
        return
    except (ToolLoopFallback, ToolLoopTimeout):
        logger.info("工具环回退到既有预约流程")
    async for token in legacy(user_input):
        yield token
