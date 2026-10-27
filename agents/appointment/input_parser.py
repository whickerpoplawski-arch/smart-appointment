"""
患者输入解析器

负责解析患者的自然语言输入，抽取就诊预约所需的字段
"""

import json
from typing import Dict, Any, Generator
from langchain_core.prompts import PromptTemplate
from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, AIMessage


class InputParser:
    """患者输入解析器"""

    def __init__(self, llm: BaseChatModel):
        self.llm = llm
        self.prompt = self._create_prompt_template()
        self.chain = self.prompt | self.llm

    def _create_prompt_template(self) -> PromptTemplate:
        """创建就诊信息提取的Prompt模板"""
        from config.time_config import time_config
        current_date = time_config.current_date_str()
        current_datetime = time_config.current_datetime_str()

        return PromptTemplate(
            input_variables=["history", "user_input"],
            template=(
                "你是一个就诊预约助手，负责帮患者预约医生，并从对话中提取预约信息。\n"
                f"当前日期是{current_date}，当前北京时间是{current_datetime}。\n"
                "当前已知信息：{history}\n"
                "患者输入：{user_input}\n"
                "特别注意：如果患者是在回应「是否接受推荐医生」的确认问题（如\"是\"、\"好\"、\"可以\"、\"不\"、\"不要\"等简短回复），"
                "请优先识别为confirmation，而不要标记为unrelated。\n"
                "重要：请你只输出纯JSON格式，不要添加任何markdown标记如```json或```，不要添加任何其他文字说明，直接输出JSON：\n"
                "{{\n"
                '  "patient_name": "患者姓名（如张三；未提及则为未知）",\n'
                '  "specialty": "科室原话（如内科；只说症状、没说科室则为未知，不要根据症状猜测科室）",\n'
                '  "visit_type": "就诊类型，只能是 初诊/复诊/咨询/未知 之一。首诊、第一次归为初诊；复查、再次归为复诊；问诊归为咨询",\n'
                '  "channel": "渠道，只能是 门诊/互联网医院/未知 之一。线下、现场、到院归为门诊；线上、视频、远程归为互联网医院。未说明则为未知",\n'
                '  "start_time": "就诊开始时间，必须转换为标准格式YYYY-MM-DD HH:MM。如果患者说今天下午3点，转换为当前日期 15:00；'
                '如果说明天上午10点，转换为明天日期 10:00。如果只说时间没说日期，默认为今天。如果完全没有时间信息则为未知",\n'
                '  "duration": "就诊时长，统一转换为分钟数格式，如45分钟、60分钟。未明确提及则为未知",\n'
                '  "doctor_name": "指定医生姓名（如果患者明确提到医生名字，如张伟、李娜等，否则为未知）",\n'
                '  "preference": "患者对医生的偏好（如希望偏系统设计、希望经验丰富、无；未提及则为未知）",\n'
                '  "confirmation": "如果患者在回应推荐医生的确认问题，提取其回复内容（如是/好/可以/不/不要等），否则为未知",\n'
                '  "unrelated": "如果患者的问题与就诊预约无关（如问天气、闲聊等），则为true，否则为false。注意：对推荐医生的确认回复（是/不等）不应标记为unrelated"\n'
                "}}\n"
                "判断逻辑：\n"
                "1. 如果患者明确指定了医生姓名（如\"张伟医生\"、\"约李娜\"等），请务必提取doctor_name\n"
                "2. 如果患者在回应推荐医生的确认问题（如回复\"是\"、\"好\"、\"可以\"、\"不\"、\"不要\"等），"
                "请提取到confirmation字段，并且不要将其标记为unrelated\n"
                "3. 就诊类型只记录，不参与医生筛选；不要因为患者没说就诊类型就把科室或时间标为未知\n"
                "4. 如果患者问的是预约步骤、就诊类型时长、携带物品、地点、改期等，这些属于咨询，"
                "但不要在unrelated字段里自行扩展判断，按是否与预约这一动作直接相关填写\n"
                "5. 所有未提及的字段统一填写「未知」，不要编造患者的姓名、科室或时间，也不要根据症状分诊\n"
                "再次强调：只输出纯JSON，不要有任何代码块标记或其他文字。"
            )
        )

    def parse_stream(self, user_input: str, chat_history: InMemoryChatMessageHistory) -> Generator[str, None, str]:
        """流式解析患者输入"""
        # 添加患者消息到历史
        chat_history.add_message(HumanMessage(content=user_input))

        # 构建历史字符串
        history_str = "\n".join(
            [f"患者：{m.content}" if m.type == "human" else f"机器人：{m.content}"
             for m in chat_history.messages]
        )

        # 流式调用LLM
        response_stream = self.chain.stream({"history": history_str, "user_input": user_input})
        ai_content = ""

        for chunk in response_stream:
            token = chunk.content if hasattr(chunk, "content") else str(chunk)
            ai_content += token
            yield token

        # 添加AI回复到历史
        chat_history.add_message(AIMessage(content=ai_content))
        return ai_content

    def parse_data(self, ai_content: str) -> Dict[str, Any]:
        """解析AI返回的JSON数据，解析失败时返回全部未知的安全默认值"""
        try:
            data = json.loads(ai_content)
            if not isinstance(data, dict):
                raise ValueError("解析结果不是JSON对象")
            return data
        except (json.JSONDecodeError, ValueError, TypeError):
            return {
                "patient_name": "未知",
                "specialty": "未知",
                "visit_type": "未知",
                "channel": "未知",
                "start_time": "未知",
                "duration": "未知",
                "doctor_name": "未知",
                "preference": "未知",
                "confirmation": "未知",
                "unrelated": False
            }
