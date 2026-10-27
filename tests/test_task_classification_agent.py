"""任务分类与状态路由测试"""

import pytest

from agents.task_classification.agent_router import AgentRouter
from agents.task_classification.state_manager import IllegalStateTransition, StateManager
from agents.task_classification.task_classifier import TaskClassifier
from config.constants import StateEnum, SharedState


class FakeAppointmentAgent:
    def __init__(self):
        self.state = None
        self.received = []

    def set_shared_state(self, state):
        self.state = state

    async def run_stream(self, user_input=None):
        self.received.append(user_input)
        yield "[REPLY][预约助手]已收到"


class FakeConsultantAgent:
    def __init__(self):
        self.state = None
        self.received = []

    def set_shared_state(self, state):
        self.state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def consult_stream(self, user_input):
        self.received.append(user_input)
        yield "[REPLY][咨询机器人]已回复"


class TestTaskClassifierPrompt:
    def test_should_use_interview_domain_categories(self):
        classifier = TaskClassifier.__new__(TaskClassifier)
        classifier._initialize_prompt()

        template = classifier.prompt.template

        assert "appointment（预约就诊任务）" in template
        assert "query（查询任务）" in template
        assert "医生" in template
        assert "pay" not in template
        assert "statistics" not in template
        assert TaskClassifier.VALID_CATEGORIES == {"appointment", "query", "other"}


class TestStateManager:
    def test_should_start_in_classify_state(self):
        manager = StateManager()

        assert manager.get_current_state() == StateEnum.CLASSIFY
        assert manager.should_classify() is True

    def test_should_transition_to_appointment_and_back(self):
        manager = StateManager()

        manager.transition_to_appointment()
        assert manager.is_in_appointment_flow() is True
        assert manager.should_classify() is False

        manager.reset_to_classify()
        assert manager.should_classify() is True

    def test_should_transition_to_consultation(self):
        manager = StateManager()

        manager.transition_to_consultation()

        assert manager.is_in_consultation_flow() is True

    def test_should_share_state_with_agents(self):
        shared_state = SharedState()
        manager = StateManager(shared_state)
        manager.transition_to_appointment()

        assert shared_state.value == StateEnum.APPOINTMENT


class TestAgentRouter:
    @pytest.mark.asyncio
    async def test_should_route_to_appointment_agent(self):
        appointment_agent = FakeAppointmentAgent()
        manager = StateManager()
        router = AgentRouter(appointment_agent, FakeConsultantAgent(), manager)

        tokens = [token async for token in router.route_to_appointment("约明天下午三点一面")]

        assert appointment_agent.received == ["约明天下午三点一面"]
        assert not any("[THOUGHT]" in token for token in tokens)
        assert manager.is_in_appointment_flow() is True

    @pytest.mark.asyncio
    async def test_should_route_to_consultant(self):
        consultant = FakeConsultantAgent()
        manager = StateManager()
        router = AgentRouter(FakeAppointmentAgent(), consultant, manager)

        tokens = [token async for token in router.route_to_consultation("互联网医院要准备什么")]

        assert consultant.received == ["互联网医院要准备什么"]
        assert not any("[THOUGHT]" in token for token in tokens)
        assert manager.is_in_consultation_flow() is True

    @pytest.mark.asyncio
    async def test_should_handle_unsupported_task(self):
        router = AgentRouter(None, None, StateManager())

        tokens = [token async for token in router.handle_unsupported_task("other")]
        reply = "".join(tokens)

        assert "暂不支持" in reply


class TestStateBudget:
    def test_should_reject_illegal_transition(self):
        manager = StateManager()
        manager.transition_to_appointment()

        with pytest.raises(IllegalStateTransition):
            manager.transition_to_consultation()

        assert manager.is_in_appointment_flow() is True

    def test_should_allow_reset_when_already_classifying(self):
        manager = StateManager()

        manager.reset_to_classify()
        manager.force_reset()

        assert manager.get_current_state() == StateEnum.CLASSIFY

    @pytest.mark.asyncio
    async def test_should_stop_reclassify_when_unrelated_depth_reached(self):
        from agents.task_classification.unrelated_handler import UnrelatedHandler
        from agents.task_classification_agent import TaskClassificationAgent

        class ExplodingProcessor:
            async def process_task_stream(self, task):
                raise AssertionError("深度达到 1 后不能再进 process_task_stream")
                yield task

        agent = TaskClassificationAgent.__new__(TaskClassificationAgent)
        agent.unrelated_handler = UnrelatedHandler(StateManager())
        agent.classification_processor = ExplodingProcessor()

        reply = await agent.handle_unrelated("今天天气怎么样", depth=1)
        streamed = [token async for token in agent.handle_unrelated_async("今天天气怎么样", depth=1)]

        assert "就诊" in reply
        assert "process_task_stream" not in reply
        assert not any("[THOUGHT]" in token for token in streamed)
        assert "就诊" in "".join(streamed)
