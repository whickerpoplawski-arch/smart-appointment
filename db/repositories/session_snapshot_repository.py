"""按 session 读写预约槽位快照。"""

from datetime import datetime

from db.base.session_manager import SessionManager
from db.models import SessionSlotSnapshot

SNAPSHOT_KEYS = (
    "candidate_name",
    "position",
    "round",
    "mode",
    "start_time",
    "duration",
    "interviewer_name",
    "preference",
    "awaiting_confirmation",
    "recommended_interviewer",
    "pending_alternative_slots",
    "awaiting_slot_choice",
)


class SessionSnapshotRepository:
    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

    def save(self, session_id: str, history: dict) -> None:
        payload = {key: history.get(key) for key in SNAPSHOT_KEYS}
        payload["awaiting_confirmation"] = bool(history.get("awaiting_confirmation"))
        payload["awaiting_slot_choice"] = bool(history.get("awaiting_slot_choice"))
        with self.session_manager.session_scope() as session:
            row = session.query(SessionSlotSnapshot).filter_by(session_id=session_id).one_or_none()
            if row is None:
                session.add(SessionSlotSnapshot(
                    session_id=session_id,
                    slots_json=payload,
                    updated_at=datetime.utcnow(),
                ))
            else:
                row.slots_json = payload
                row.updated_at = datetime.utcnow()

    def load(self, session_id: str):
        with self.session_manager.session_scope() as session:
            row = session.query(SessionSlotSnapshot).filter_by(session_id=session_id).one_or_none()
            if row is None or not row.slots_json:
                return None
            payload = dict(row.slots_json)
        payload["awaiting_confirmation"] = bool(payload.get("awaiting_confirmation"))
        payload["awaiting_slot_choice"] = bool(payload.get("awaiting_slot_choice"))
        return payload
