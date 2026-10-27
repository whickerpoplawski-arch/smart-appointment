# 简历三句 · 可复跑证明

这三句只覆盖当前代码里为真的事。诊断、处方、分诊准确率、HIS、号源池、叫号、缴费、电子病历、真实医院或真实患者，都不在这三句里。

## 1. 动作空间只有垂直预约环

每轮模型只能选 `ask_user`、`find_doctor`、`finish`。不在这三项里的输出连续 3 次就回退原来的抽槽流程，不会执行工具。

证明：

```bash
rg -n "ALLOWED_ACTIONS = " agents/appointment/booking_tool_loop.py
python3 -m pytest tests/test_booking_tool_loop.py::TestToolLoopFallback::test_should_fall_back_after_three_illegal_actions -q
```

`agents/appointment/booking_tool_loop.py` 第 25 行是 `ALLOWED_ACTIONS = ("ask_user", "find_doctor", "finish")`。工具状态仍是六态，见 `agents/appointment/doctor_tools.py` 的 `TOOL_STATUSES`。对话上限仍是 `agents/appointment_agent.py` 的 `MAX_TURNS = 12`。非法上限仍是 `MAX_INVALID_ATTEMPTS = 3`。锁在 `tests/test_doctor_tools.py::TestArgumentValidation::test_control_plane_constants_stay_locked`。

## 2. 会话按 session_id 隔离，槽位可从 SQLite 恢复

不同 `session_id` 的姓名、科室、就诊类型互不覆盖。进程重启后，`session_slot_snapshots` 能把这些槽位以及是否在等确认读回来。聊天原文不在这张快照里，这句不声称聊天记录已恢复。

证明：

```bash
python3 -m pytest tests/test_session_scope.py::TestSessionIsolation::test_two_sessions_do_not_contaminate_each_other -q
```

快照键在 `db/repositories/session_snapshot_repository.py` 的 `SNAPSHOT_KEYS`。`api/chat_handler.py` 没有 `global_session_id`。

## 3. 名词与表一致，说的是约医生档期

医生在 `doctors`，占用在 `doctor_schedules`，预约在 `appointments`。预约行上是 `specialty`、`visit_type`、`channel`、`doctor_id`，不是诊断或处方。

证明：

```bash
rg -n "class Doctor|class DoctorSchedule|class Appointment|specialty =|visit_type =|channel =|doctor_id =" db/models.py
```

`db/models.py` 里 `Doctor`、`DoctorSchedule`、`Appointment` 三张表带着这些列。同一条搜索还会命中患者行为表上的 `doctor_id`，那是行为记录上的医生外键，不是病历主表。

## 本提交实测

三条 pytest 与控制面锁在同一次命令里跑过：

```bash
python3 -m pytest \
  tests/test_booking_tool_loop.py::TestToolLoopFallback::test_should_fall_back_after_three_illegal_actions \
  tests/test_session_scope.py::TestSessionIsolation::test_two_sessions_do_not_contaminate_each_other \
  tests/test_doctor_tools.py::TestArgumentValidation::test_control_plane_constants_stay_locked \
  -q
```

最后一行：`3 passed, 5 warnings in 1.01s`。警告来自 `session_snapshot_repository.py` 的 `datetime.utcnow()`，不是失败。

同一工作区随后跑了全量 `python3 -m pytest -q`。最后一行：`145 passed, 23 warnings in 2.51s`。基线在 `a50f530` 上是 `128 tests collected`。

`rg` 原文：

```text
agents/appointment/booking_tool_loop.py:25:ALLOWED_ACTIONS = ("ask_user", "find_doctor", "finish")
agents/appointment/booking_tool_loop.py:26:MAX_INVALID_ATTEMPTS = 3
agents/appointment/doctor_tools.py:25:TOOL_STATUSES = ("available", "unavailable", "not_found", "no_match", "invalid_arguments", "invalid_time")
agents/appointment_agent.py:16:MAX_TURNS = 12
db/models.py:8:class Doctor(Base):
db/models.py:24:class DoctorSchedule(Base):
db/models.py:28:    doctor_id = Column(...)  # doctor_schedules
db/models.py:45:class Appointment(Base):
db/models.py:50:    specialty = Column(String, nullable=False)
db/models.py:51:    visit_type = Column(String, nullable=False)
db/models.py:52:    channel = Column(String, nullable=False, default='门诊')
db/models.py:53:    doctor_id = Column(Integer, ForeignKey('doctors.id'), nullable=False)
```

同一条 `db/models.py` 搜索还会命中约第 96 行和第 141 行的 `doctor_id`，那是患者行为表上的外键。

真 LLM 五场景不在这三句的证明里。`scripts/live_doctor_eval.py` 的实跑在本环境是 `not measured`（没有 `LLM_API_KEY`）。
