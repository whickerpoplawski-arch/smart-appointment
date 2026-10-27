from langchain_core.prompts import PromptTemplate
from langchain_core.language_models.chat_models import BaseChatModel
from typing import Dict, Any


class TaskClassifier:
    """任务分类器 - 使用LLM进行智能任务分类"""

    VALID_CATEGORIES = {"appointment", "query", "other"}

    def __init__(self, llm: BaseChatModel):
        self.llm = llm
        self._initialize_prompt()
        self.chain = self.prompt | self.llm

    def _initialize_prompt(self):
        """初始化分类提示词模板"""
        self.prompt = PromptTemplate(
            input_variables=["task"],
            template=(
                "你是一个医生档期预约系统的助手，你会处理来自患者和工作人员的消息，你的任务是对本次任务进行分类。\n"
                "患者可能会咨询预约步骤、就诊类型时长、互联网医院设备、门诊地址、改期政策等，这类任务归类为查询任务。\n"
                "患者可能会请求预约医生，比如'我想约明天下午三点内科初诊'，这类任务归类为预约就诊任务。\n"
                "工作人员可能会发来任务，比如告知需要调整患者的就诊时间，这类任务归类为预约就诊任务。\n"
                "如果输入的任务与上述都无关，请归类为其它任务。\n"
                "请将以下任务归类为以下类别，输出只能选择以下之一：\n"
                "1. appointment（预约就诊任务）\n"
                "2. query（查询任务）\n"
                "3. other（其它任务）\n"
                "只返回类别英文名。\n\n"
                "举例说明：假如task为'我想约陈安医生明早十点看内科初诊'，则输出appointment。\n"
                "假如输入为我想问一下互联网医院需要准备什么，则输出query。\n"
                "以下是本次归类任务:\n"
                "任务内容：{task}"
            )
        )

    async def classify_task(self, task: str) -> str:
        """
        分类任务

        Args:
            task: 用户输入的任务内容

        Returns:
            str: 分类结果 ('appointment', 'query', 'other')
        """
        try:
            category_msg = await self.chain.ainvoke({"task": task})
            category = category_msg.content.strip().lower()

            # 验证分类结果是否有效
            valid_categories = self.VALID_CATEGORIES
            if category not in valid_categories:
                return 'other'  # 默认归类为其他

            return category

        except Exception as e:
            print(f"任务分类失败: {str(e)}")
            return 'other'  # 发生错误时默认归类为其他

    def get_category_description(self, category: str) -> str:
        """获取分类类别的描述信息"""
        descriptions = {
            'appointment': '预约就诊任务 - 患者发起的就诊预约请求',
            'query': '查询任务 - 咨询预约步骤、就诊类型、渠道、改期',
            'other': '其他任务 - 与医生档期预约无关的请求'
        }
        return descriptions.get(category, '未知任务类型')
