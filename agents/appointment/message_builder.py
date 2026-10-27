"""
消息构建器

负责构建就诊预约各个环节的响应消息
"""

from typing import Dict, Any, List, Optional


def slot_is_blank(value: Any) -> bool:
    """空白、未知、null 都不算已收集的槽位。"""
    if value is None:
        return True
    text = str(value).strip()
    return text == "" or text == "未知" or text.lower() == "null"


class MessageBuilder:
    """消息构建器"""

    ASK_SLOT_ORDER = ("specialty", "patient_name", "visit_type", "start_time")
    ASK_WHEN_COMPLETE = "请确认是否按已记下的姓名、科室、就诊类型和时间预约。"

    def __init__(self):
        self.missing_info_prompts = {
            "patient_name": "请问患者的姓名是？",
            "visit_type": "请问是初诊、复诊还是咨询？",
            "start_time": "请问希望把就诊安排在什么时间？",
            "duration": "请问就诊预计需要多长时间？未说明时我会按就诊类型设置默认时长。",
            "channel": "请问是门诊还是互联网医院？未说明时按门诊。"
        }

    @classmethod
    def question_for(cls, field: str) -> str:
        """缺槽追问的唯一句子。科室走完整菜单，不按症状推荐。"""
        if field == "specialty":
            return cls.specialty_menu_question()
        return cls().missing_info_prompts[field]

    @classmethod
    def first_missing_slot(cls, history: Optional[Dict[str, Any]]) -> Optional[str]:
        """科室优先，其次姓名、就诊类型、开始时间。"""
        history = history or {}
        for field in cls.ASK_SLOT_ORDER:
            if slot_is_blank(history.get(field)):
                return field
        return None

    @classmethod
    def ask_one(cls, history: Optional[Dict[str, Any]]) -> str:
        """只问最缺的一槽；四槽都在时请患者确认，不用空泛补信息句。"""
        field = cls.first_missing_slot(history)
        if field is None:
            return cls.ASK_WHEN_COMPLETE
        return cls.question_for(field)

    @staticmethod
    def specialty_menu_question() -> str:
        """列出全部科室，并说明不会按症状判断或推荐。"""
        from config.constants import SPECIALTY_CHOICE_LIMIT
        from services.appointment_service import DEPARTMENT_CHOICES

        names = "、".join(DEPARTMENT_CHOICES)
        return (
            f"{SPECIALTY_CHOICE_LIMIT}"
            f"可选科室：{names}。"
            "请您自己选定一个，我不会按症状推荐科室。"
        )

    def create_appointment_success_message(self, doctor: Dict[str, Any],
                                         appointment_history: Dict[str, Any]) -> str:
        """创建就诊预约成功消息，按线上/线下给出不同的准备提醒"""
        patient_name = appointment_history.get('patient_name', '患者')
        specialty = appointment_history.get('specialty', '')
        round_name = appointment_history.get('visit_type', '')
        channel = appointment_history.get('channel', '门诊')
        start_time = appointment_history.get('start_time', '')
        department = doctor.get('department') or ''

        header = (
            f"\n机器人：已为患者{patient_name}预约{specialty}{round_name}就诊，"
            f"医生：{doctor['name']}"
        )
        if department:
            header += f"（{department}方向）"
            header += f"，时间：{start_time}，渠道：{channel}。预约成功！\n"

        return header + self._create_mode_tip(channel)

    @staticmethod
    def _create_mode_tip(channel: str) -> str:
        """生成就诊形式对应的准备提醒"""
        from config.constants import DEMO_DISCLAIMER
        if channel == '互联网医院':
            return (
                "温馨提示：请提前 5 分钟进入互联网医院，确认网络稳定，"
                f"检查摄像头与麦克风可用。{DEMO_DISCLAIMER}\n"
            )
        return (
            f"温馨提示：请提前 15 分钟到达演示园区门诊完成登记。{DEMO_DISCLAIMER}\n"
        )

    def create_doctor_recommendation_message(self, original_doctor: Dict[str, Any],
                                                  recommended_doctor: Dict[str, Any],
                                                  appointment_history: Dict[str, Any],
                                                  llm=None) -> str:
        """创建医生推荐消息，优先使用LLM生成个性化措辞"""
        specialty = appointment_history.get('specialty', '该科室')
        round_name = appointment_history.get('visit_type', '')
        start_time = appointment_history.get('start_time', '')

        if llm:
            try:
                prompt = f"""
作为专业的就诊预约助手，患者希望{start_time}进行{specialty}的{round_name}就诊，指定医生{original_doctor['name']}在该时段已有安排。

我找到了一位同方向且空闲的医生：
- 姓名：{recommended_doctor['name']}
- 科室：{recommended_doctor.get('department', '')}
- 简介：{recommended_doctor.get('bio', '')}

原医生简介：{original_doctor.get('bio', '')}

请生成一段专业、简洁的推荐话术，说明原医生时间冲突，介绍推荐医生在相同方向上的经验，并明确询问患者是否愿意改由该医生就诊。

要求：
1. 语气专业、尊重患者
2. 突出推荐医生与科室的匹配度
3. 明确询问患者意愿
4. 字数控制在80字以内
"""

                response = llm.invoke(prompt)
                if hasattr(response, 'content'):
                    generated_msg = response.content.strip()
                    if generated_msg:
                        return f"\n机器人：{generated_msg}\n"

            except Exception as e:
                print(f"LLM生成推荐消息失败: {e}")

        # 如果LLM失败，使用默认消息
        direction = recommended_doctor.get('department') or '相同方向'
        return (
            f"\n机器人：抱歉，{original_doctor['name']}医生在{start_time}这个时间段已有安排。"
            f"不过{recommended_doctor['name']}医生同样负责{direction}方向，这个时间段有空，"
            f"请问您愿意改由{recommended_doctor['name']}医生为患者就诊吗？\n"
        )

    def create_recommendation_declined_message(self, llm=None) -> str:
        """创建患者拒绝推荐时的消息"""
        if llm:
            try:
                prompt = """
患者拒绝了我推荐的医生，请生成一段专业、简洁的回复：表达理解，并提供改期或重新按科室推荐其他医生的选择。

要求：
1. 尊重患者的选择
2. 给出可执行的下一步（改时间、重新推荐）
3. 保持专业友好的语气
4. 字数控制在60字以内
"""
                response = llm.invoke(prompt)
                if hasattr(response, 'content'):
                    generated_msg = response.content.strip()
                    if generated_msg:
                        return f"\n机器人：{generated_msg}\n"
            except Exception as e:
                print(f"LLM生成拒绝消息失败: {e}")

        # 默认消息
        return (
            "\n机器人：好的，我理解您的选择。您可以更换就诊时间，"
            "或由我按就诊科室方向重新推荐其他医生。请问您希望怎么安排？\n"
        )

    def create_appointment_failure_message(self, result: Dict[str, Any],
                                         appointment_history: Dict[str, Any]) -> str:
        """
        根据查找结果创建准确的失败消息

        Args:
            result: 医生查找结果，包含 status
            appointment_history: 会话中的就诊信息
        """
        status = result.get('status')
        start_time = appointment_history.get('start_time', '')
        specialty = appointment_history.get('specialty', '该科室')

        if status == 'needs_specialty':
            return f"\n机器人：{self.specialty_menu_question()}\n"
        if status == 'doctor_not_found':
            name = result.get('requested_doctor') or appointment_history.get('doctor_name')
            return (
                f"\n机器人：抱歉，没有找到名为'{name}'的医生。"
                "请确认医生姓名，或者我可以按科室为您推荐其他医生。\n"
            )
        if status == 'conflict':
            original = result.get('original_doctor') or {}
            return (
                f"\n机器人：抱歉，{original.get('name', '该医生')}在{start_time}这个时间段已有安排，"
                "同方向暂时没有其他空闲医生。建议调整就诊时间后再预约。\n"
            )
        if status == 'invalid_time':
            return (
                "\n机器人：抱歉，我没能识别出明确的就诊时间或时长，"
                "请按「YYYY-MM-DD HH:MM」格式再说明一次，比如 2026-10-27 15:00。\n"
            )
        if status == 'no_available_doctor':
            department = result.get('department')
            direction = f"{department}" if department else "该科室"
            return (
                f"\n机器人：抱歉，{direction}在{start_time}没有空闲的医生。"
                "建议更换就诊时间，或告知我其他可接受的时间段。\n"
            )

        return "\n机器人：抱歉，暂时无法安排该就诊，请稍后重试或调整就诊条件。\n"

    def create_missing_info_questions(self, missing_info: List[str]) -> str:
        """只问 ASK_SLOT_ORDER 里最靠前的一个缺槽。"""
        missing = set(missing_info or [])
        field = next((name for name in self.ASK_SLOT_ORDER if name in missing), None)
        if field is None:
            question = self.ask_one({}) if not missing else self.missing_info_prompts.get(
                missing_info[0], f"请补充{missing_info[0]}信息"
            )
        else:
            question = self.question_for(field)
        return "\n" + question + "\n"

    def create_confirmation_hint_message(self) -> str:
        """患者回应不明确时，提示明确确认或拒绝"""
        return "\n机器人：请您明确回复\"是\"或\"不\"，我好为您确认医生安排。\n"

    def create_unrelated_message(self) -> str:
        """创建无关请求的消息"""
        return (
            "[REPLY][预约助手]抱歉，我只能协助处理就诊预约相关的问题，"
            "比如按科室和时间预约医生。请问需要我帮您安排就诊吗？\n"
        )

    def create_parse_error_message(self) -> str:
        """创建解析错误消息"""
        return "[REPLY][预约助手]\n机器人：解析失败，请重试。\n"

    def create_save_failure_message(self, reason: str) -> str:
        """创建保存失败消息，带上具体原因而不是笼统的保存失败"""
        return f"\n机器人：抱歉，就诊预约未成功：{reason}。请调整后重试。\n"
