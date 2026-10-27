"""咨询 Agent 的提示词与分类逻辑测试（不依赖真实模型）"""

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agents.consultant.consultation_classifier import ConsultationClassifier
from agents.consultant.prompt_builder import PromptBuilder


class TestPromptBuilder:
    def test_should_describe_interview_scope(self):
        builder = PromptBuilder()

        assert "就诊类型" in builder.system_prompt
        assert "不提供诊断、处方或急救建议" in builder.system_prompt
        assert "改期" in builder.system_prompt

    def test_should_build_prompt_with_knowledge(self):
        builder = PromptBuilder()
        docs = [
            {"content": "互联网医院请提前 5 分钟进入会议，检查摄像头和麦克风。", "category": "互联网医院"},
        ]

        prompt = builder.build_consultation_prompt("互联网医院要准备什么？", docs)

        assert "互联网医院要准备什么？" in prompt
        assert "提前 5 分钟" in prompt

    def test_should_fall_back_when_no_knowledge(self):
        builder = PromptBuilder()

        prompt = builder.build_consultation_prompt("结果多久通知？", [])

        assert "没有找到直接相关的知识库信息" in prompt

    def test_should_build_classification_prompt(self):
        builder = PromptBuilder()

        prompt = builder.build_classification_prompt("预约流程是怎样的？")

        assert "预约流程是怎样的？" in prompt
        assert "YES" in prompt


class TestConsultationClassifier:
    @pytest.mark.asyncio
    async def test_should_recognize_consultation(self):
        classifier = ConsultationClassifier(FakeListChatModel(responses=["YES"]))

        assert await classifier.is_consultation_related("预约流程是怎样的？") is True

    @pytest.mark.asyncio
    async def test_should_reject_booking_request(self):
        classifier = ConsultationClassifier(FakeListChatModel(responses=["NO"]))

        assert await classifier.is_consultation_related("帮我约明天下午三点内科初诊") is False

    def test_should_refuse_diagnosis_and_prescription_without_knowledge(self):
        from agents.consultant.consultation_processor import medical_advice_refusal

        for text in ("这是什么病", "帮我开药", "给我一张处方", "这个剂量怎么吃"):
            reply = medical_advice_refusal(text)
            assert reply is not None
            assert "不提供诊断、处方或急救建议" in reply
            assert "选择科室" in reply
            assert "[THOUGHT]" not in reply

        assert medical_advice_refusal("预约步骤是什么？") is None

    def test_should_seed_eight_visit_notices(self, tmp_path):
        from services.knowledge_service import KnowledgeService

        service = KnowledgeService(db_path=f"sqlite:///{tmp_path / 'notes.db'}")
        notes = service.default_knowledge
        text = "\n".join(item["content"] for item in notes)

        assert len(notes) == 8
        assert "初诊 30 分钟" in text
        assert "复诊 15 分钟" in text
        assert "咨询 20 分钟" in text
        assert "演示园区" in text
        assert "不收费" in text
        assert "一面" not in text
        assert "简历" not in text
