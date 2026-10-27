"""
提示词构建器

负责构建各种类型的提示词
"""

from typing import List, Dict, Any


class PromptBuilder:
    """提示词构建器"""

    def __init__(self):
        self.system_prompt = self._create_system_prompt()
        self.classification_prompt_template = self._create_classification_prompt_template()

    def _create_system_prompt(self) -> str:
        """创建系统提示词"""
        from config.constants import DEMO_DISCLAIMER
        return (
            "你是医生档期预约演示的咨询助手，只回答预约步骤、就诊类型时长、互联网医院设备、"
            "门诊演示地址、自行携带的物品、改期和到诊提醒。"
            f"{DEMO_DISCLAIMER}"
            "患者若询问症状判断、用药、处方或剂量，不要检索或编造答案，请其先选择科室后再预约档期。"
            "知识库没有对应条目时，只重复上述范围，不要补充临床建议。"
            "改期请至少提前 24 小时，同一预约最多一次。"
            "门诊地址只说「演示园区」，不要写成真实医院。"
            "与预约无关的问题（天气、股票、新闻）请说明你只能协助预约档期。"
        )

    def _create_classification_prompt_template(self) -> str:
        """创建分类提示词模板"""
        return (
            "你是一个分类器，判断患者输入是否是关于医生档期预约的咨询。\n"
            "咨询包括：预约步骤、就诊类型时长、互联网医院设备、门诊演示地址、携带物品、改期、到诊提醒。\n"
            "不是咨询：预约某个医生或时段、取消具体预约、症状判断、开药、处方、剂量，以及天气、股票、新闻。\n"
            "如果是咨询，回答'YES'。如果是预约或无关问题，回答'NO'。\n"
            "只回答YES或NO。\n\n"
            "患者输入：{user_input}"
        )

    def build_consultation_prompt(self, user_input: str, knowledge_docs: List[Dict[str, Any]]) -> str:
        """构建咨询提示词"""
        context = self._build_knowledge_context(knowledge_docs)
        return f"{self.system_prompt}\n\n{context}\n患者问题：{user_input}\n\n请回答患者的问题。"

    def build_classification_prompt(self, user_input: str) -> str:
        """构建分类提示词"""
        return self.classification_prompt_template.format(user_input=user_input)

    def _build_knowledge_context(self, knowledge_docs: List[Dict[str, Any]]) -> str:
        """构建知识库上下文"""
        if not knowledge_docs:
            return "没有找到直接相关的知识库信息，请只根据预约演示的范围回答，不要提供诊断或处方。"

        context = "\n以下是相关的知识库信息：\n"
        for i, doc in enumerate(knowledge_docs, 1):
            context += f"{i}. {doc['content']}\n"
        context += "\n请基于以上信息回答。信息不够时不要补充诊断、用药或真实医院信息。\n"

        return context
