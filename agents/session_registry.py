"""请求级会话：槽位、聊天记录和共享状态按 session_id 分开。"""

from langchain_core.chat_history import InMemoryChatMessageHistory

from config.constants import SharedState
from db.base.session_manager import SessionManager
from db.repositories.session_snapshot_repository import SessionSnapshotRepository

SLOT_FIELDS = (
    "patient_name",
    "specialty",
    "visit_type",
    "start_time",
)


def new_appointment_history() -> dict:
    return {
        "patient_name": None,
        "specialty": None,
        "visit_type": None,
        "channel": None,
        "start_time": None,
        "duration": None,
        "doctor_name": None,
        "preference": None,
        "recommended_doctor": None,
        "original_doctor": None,
        "awaiting_confirmation": False,
        "recommendation_declined": False,
        "pending_alternative_slots": [],
        "awaiting_slot_choice": False,
    }


def history_has_progress(history: dict) -> bool:
    if history.get("awaiting_confirmation") or history.get("awaiting_slot_choice"):
        return True
    return any(history.get(field) not in (None, "", "未知") for field in SLOT_FIELDS)


class SessionRecord:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.shared_state = SharedState()
        self.appointment_history = new_appointment_history()
        self.chat_history = InMemoryChatMessageHistory()
        self.turn_count = 0


class SessionRegistry:
    def __init__(self, db_path: str = "sqlite:///data/doctor_appointment.db", snapshots=None,
                 enable_snapshots: bool = True):
        self._records = {}
        if snapshots is not None:
            self.snapshots = snapshots
        elif enable_snapshots:
            self.snapshots = SessionSnapshotRepository(SessionManager(db_path))
        else:
            self.snapshots = None

    def get(self, session_id: str) -> SessionRecord:
        record = self._records.get(session_id)
        if record is not None:
            return record

        record = SessionRecord(session_id)
        saved = self.snapshots.load(session_id) if self.snapshots is not None else None
        if saved:
            record.appointment_history.update(saved)
            if history_has_progress(record.appointment_history):
                from agents.task_classification.state_manager import StateManager
                StateManager(record.shared_state).transition_to_appointment()
        self._records[session_id] = record
        return record

    def save(self, record: SessionRecord) -> None:
        if self.snapshots is not None:
            self.snapshots.save(record.session_id, record.appointment_history)
