"""请求级会话：交错消息互不覆盖，槽位快照可以按 session 恢复。"""

import inspect

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agents.appointment.appointment_processor import AppointmentProcessor
from agents.appointment.message_builder import MessageBuilder
from agents.appointment_agent import AppointmentAgent
from agents.session_registry import SessionRegistry
from api.chat_handler import ProcessUserInput_stream
from config.constants import StateEnum
from web.routes import ChatRequest


def make_processor():
    return AppointmentProcessor(None, None, MessageBuilder(), None, None)


class TestSessionIsolation:
    def test_two_sessions_do_not_contaminate_each_other(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'sessions.db'}"
        registry = SessionRegistry(db_path=db_path)
        processor = make_processor()
        agent = AppointmentAgent(
            llm=FakeListChatModel(responses=["占位"]),
            session_id="boot",
            timeout_seconds=1,
        )

        def turn(session_id, data):
            record = registry.get(session_id)
            agent.bind_session(record)
            processor.update_history_from_data(agent.appointment_history, data)
            agent.chat_history.add_user_message(str(data))
            registry.save(record)

        turn("session-a", {"patient_name": "张三"})
        turn("session-b", {"patient_name": "李四"})
        turn("session-a", {"specialty": "内科"})
        turn("session-b", {"visit_type": "初诊"})

        agent.bind_session(registry.get("session-a"))
        assert agent.appointment_history["patient_name"] == "张三"
        assert agent.appointment_history["specialty"] == "内科"
        assert agent.appointment_history.get("visit_type") in (None, "未知")
        assert len(agent.chat_history.messages) == 2

        agent.bind_session(registry.get("session-b"))
        assert agent.appointment_history["patient_name"] == "李四"
        assert agent.appointment_history.get("specialty") in (None, "未知")
        assert agent.appointment_history["visit_type"] == "初诊"
        assert agent.state is not registry.get("session-a").shared_state
        assert len(agent.chat_history.messages) == 2

        record_c = registry.get("session-c")
        record_c.appointment_history["patient_name"] = "王五"
        record_c.appointment_history["awaiting_confirmation"] = True
        registry.save(record_c)

        restarted = SessionRegistry(db_path=db_path)
        restored_a = restarted.get("session-a")
        restored_b = restarted.get("session-b")
        restored_c = restarted.get("session-c")

        assert restored_a.appointment_history["patient_name"] == "张三"
        assert restored_a.appointment_history["specialty"] == "内科"
        assert restored_b.appointment_history["patient_name"] == "李四"
        assert restored_b.appointment_history.get("specialty") in (None, "未知")
        assert restored_c.appointment_history["awaiting_confirmation"] is True
        assert restored_c.shared_state.value == StateEnum.APPOINTMENT
        assert restored_a.shared_state is not restored_b.shared_state

    def test_chat_request_takes_session_id_and_drops_state(self):
        request = ChatRequest(message="约一面", session_id="s-1")

        assert request.session_id == "s-1"
        assert "state" not in ChatRequest.model_fields
        parameters = inspect.signature(ProcessUserInput_stream).parameters
        assert "session_id" in parameters
        assert "state" not in parameters

    def test_handler_has_no_global_session_id(self):
        import api.chat_handler as handler

        assert not hasattr(handler, "global_session_id")
