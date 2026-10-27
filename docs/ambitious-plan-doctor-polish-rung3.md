# 医生预约打磨 · rung 3 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把垂直预约演示的槽位合同收成可离线证明的闸门，追问只走一份文案，并交出 5 场景真 LLM 评测脚本和三句可复跑的简历证明。本机没有 `LLM_API_KEY`，真跑留给父会话。

**Architecture:** 缺槽问题与科室菜单集中在 `MessageBuilder`。`BookingToolLoop` 在 `find_doctor` 前把「null 或等于就诊类型默认值」的时长改成注入的默认分钟（初诊 30 / 复诊 15 / 咨询 20），在 `finish` 前用同一份文案只问一个缺槽。`scripts/live_doctor_eval.py` 扩展现有冒烟，离线只锁判定函数和 CLI；真模型调用留在有密钥的环境。

**Tech Stack:** Python 3.12、pytest、`FakeListChatModel`、现有 `DoctorFinder` + `FakeDoctorService`、SQLite 临时库。不新增依赖。

**Spec:** 本文件。产品地板来自 `docs/ambitious-plan-doctor-domain.md`、`agents/appointment/booking_tool_loop.py`、`agents/appointment/doctor_tools.py`、`agents/appointment_agent.py`。用户选定 ambitious rung 3（不是推荐的 rung 2）。

## Global Constraints

- 动作只有 `ask_user` / `find_doctor` / `finish`。`ALLOWED_ACTIONS` 不增项。
- `TOOL_STATUSES` 保持六个字符串：`available`、`unavailable`、`not_found`、`no_match`、`invalid_arguments`、`invalid_time`。
- `MAX_INVALID_ATTEMPTS = 3`，`MAX_TURNS = 12`。非法状态转移仍抛错。不放宽。
- `_validate_arguments` 的显式时长区间仍是 `[30, 480]`。复诊 15、咨询 20 只作为 `default_duration` 注入，不把区间下限改成 15。
- 不做症状→科室分诊表，不建 HIS，不写 finetune spec，不恢复伪 MCP。
- `StreamCollectingChatOpenAI` 保持默认。`LLM_FORCE_STREAM=0` 才退回非流式。本任务不改 `config/model_provider.py` 与 `config/stream_collecting_chat_model.py`。
- 科室名单仍是 `services/appointment_service.py` 的 `DEPARTMENT_CHOICES`（10 科）。不改回 6 科，也不按症状加词。
- 禁句：`请补充就诊预约需要的信息`。空 `ask_user` 不得再落到这句。
- 本 VM 不调用需要 `LLM_API_KEY` 的接口。`scripts/live_doctor_eval.py` 在缺密钥时退出码 2。

## Review Focus

这些输入现有测试没有钉死，实现时必须各有一条会先失败的测试：

1. 复诊且 `duration_minutes` 为 `null` 或 `15`，`start_time` 可解析：不得 `invalid_time`，finder 收到 15 分钟。
2. 咨询且时长为 `null` 或 `20`：finder 收到 20 分钟，不得 `invalid_time`。
3. 模型直接 `finish`，历史缺 `specialty` 或缺 `start_time`：不调用 `on_finish`，回复不含 `缺少患者姓名`。
4. 空 `ask_user` 且科室空白：回复是科室那一句，不含禁句，也不把姓名和就诊类型拼在同一句。
5. 显式 `duration_minutes=10` 或 `600` 仍是 `invalid_arguments`（区间没有被放宽）。

---

## 1. 产品地板

smart-appointment 是垂直医生档期预约演示，不是 HIS、不是分诊、不是诊疗助手。

- 每轮动作只有 `ask_user`、`find_doctor`、`finish`（`agents/appointment/booking_tool_loop.py` 的 `ALLOWED_ACTIONS`）。
- 工具结果只有六态（`agents/appointment/doctor_tools.py` 的 `TOOL_STATUSES`）。连续非法 3 次回退 `run_stream`，对话 12 轮停止（`MAX_INVALID_ATTEMPTS`、`agents/appointment_agent.py` 的 `MAX_TURNS`）。
- 会话按 `session_id` 隔离；缺槽和是否在等确认可从 `session_slot_snapshots` 恢复（`agents/session_registry.py`、`db/repositories/session_snapshot_repository.py`）。
- 表是约档期：`doctors.department`、`appointments.specialty` / `visit_type` / `channel` / `doctor_id`（`db/models.py`）。就诊类型只决定默认时长：初诊 30、复诊 15、咨询 20（`agents/appointment/appointment_processor.py` 的 `DEFAULT_DURATION_BY_VISIT`）。
- 只认用户说出的科室名。只说症状则追问科室，不做症状分诊（`services/appointment_service.py` 的 `match_department_name`）。
- 咨询里「什么病 / 开药 / 处方 / 剂量」在进知识库之前固定拒绝（`agents/consultant/consultation_processor.py` 的 `medical_advice_refusal`）。
- 非 Azure 的 `ainvoke` 走 `StreamCollectingChatOpenAI`（`config/model_provider.py`）。冒烟基线见下节。

## 2. 相对 rung 3 的缺口（锚在 `a50f530`）

基线：`python3 -m pytest --collect-only -q` → `128 tests collected`。真 LLM 冒烟（`uploads` 中的 `live-smoke-pass.log`，本会话未重跑）exit 0：happy 冒烟张三 / 内科初诊 → 陈安 `booking_id=1`；ask「咳嗽两天」回复含「科室」且 `ask_booking_id=none`。脚本只有这两句（`scripts/live_doctor_booking_smoke.py`）。

| 缺口 | 代码事实 | rung 3 要的结果 |
| --- | --- | --- |
| 时长合同只锁了初诊 30 | `fill_default_duration` / `default_duration_for_find` 已在 `find_doctor` 前注入。`tests/test_booking_tool_loop.py::test_null_duration_uses_first_visit_default_before_find` 用 `StubFinder`，只断言 `default_duration == 30`。复诊 15、咨询 20 没有打到真实 `DoctorFinder`。 | null 或「等于该就诊类型默认值」的时长，在合法 `start_time` 下不得变成 `invalid_time`。finder 收到 30/15/20。 |
| 模型若照提示把 15/20 写进参数会被拒 | `_validate_arguments` 要求显式整数落在 `[30, 480]`。提示词又写「未说时长时 duration_minutes 用初诊30、复诊15、咨询20」。15 和 20 会记一次非法参数。 | 调用 `find_doctor` 前：`null` 或等于 `resolve_duration(visit_type, None)` 的值改写为 `duration_minutes=None`，分钟数只走 `default_duration`。10 和 600 仍 `invalid_arguments`。不改区间常量。 |
| finish 闸门缺两槽 | `question_blocking_finish` 只查 `patient_name` 与 `visit_type`。`appointment_database._validate` 在已经进入保存之后才返回 `缺少患者姓名` / `缺少就诊科室`。缺 `specialty` 或缺 `start_time` 仍会进 `on_finish`。 | finish 前四槽都要在：`patient_name`、`visit_type`、`specialty`、`start_time`。缺一槽就问一句，不调用 `on_finish`，回复里不出现 `缺少患者姓名`。 |
| 追问文案有三处 | `message_builder.missing_info_prompts`；`question_blocking_finish` / `ensure_specialty_question` 另写句子；`reply_for_ask` 空消息落到禁句 `请补充就诊预约需要的信息。`（`booking_tool_loop.py:155`）。`create_missing_info_questions` 把缺槽全部拼成一句。真冒烟 ask 回复同时问了科室、姓名、初诊或复诊。 | `ask_user` 与缺槽追问只读 `MessageBuilder`。空消息不再用禁句。缺槽时只问 `ASK_SLOT_ORDER` 里第一个空白槽，科室优先。 |
| 改期 / 指定不存在的医生 / 拒答没有进真 LLM 闸门 | 改期选定后再查已有离线测试（`tests/test_alternative_slots.py`）。`not_found` 文案在 `reply_for_observation`。拒答有离线测试，没有进冒烟。 | `scripts/live_doctor_eval.py` 场景 C、D、E。本机只交脚本与离线判定；父会话有密钥再跑。 |
| 简历三句没有可复跑回执 | 域方案里写了三句，仓库没有把每句绑到一条命令。 | `docs/resume-claims-doctor-appointment.md`：三句 + 证明命令，句子与当前符号一致。 |

## 3. 分阶段与裁判

每阶段先写会失败的测试，再改生产代码。裁判命令的通过条件写在阶段上。真 LLM 五场景在本机标 `not measured`。

### 阶段 0 — 本文件

- 裁判：`git diff --name-only a50f530...HEAD` 在开始改 `agents/` 之前只有 `docs/ambitious-plan-doctor-polish-rung3.md`。
- 通过：该文件已提交。失败：同一提交里出现生产代码。

### 阶段 B1 — 时长注入与 finish 四槽

- 裁判：`python3 -m pytest tests/test_booking_tool_loop.py tests/test_doctor_tools.py -q`
- 通过：退出码 0，且新增用例覆盖复诊 15、咨询 20、直接 finish 缺科室、直接 finish 缺开始时间、`duration_minutes=10` 仍非法。
- 失败：任一新断言红，或 `TOOL_STATUSES` / `MAX_INVALID_ATTEMPTS` 被改。

### 阶段 B2 — 追问单一来源

- 裁判：`python3 -m pytest tests/test_booking_tool_loop.py tests/test_appointment_agent.py -q` 且 `rg -n "请补充就诊预约需要的信息" agents tests scripts` 无命中（rg 退出码 1）。
- 通过：空 `ask_user` 得到科室问句；缺多槽时 `create_missing_info_questions` 只含 `ASK_SLOT_ORDER` 的第一句。
- 失败：禁句仍在，或一句里同时出现姓名问句和就诊类型问句。

### 阶段 B3 — 评测脚本与简历回执

- 裁判：`python3 -m pytest tests/test_live_doctor_eval.py tests/test_session_scope.py tests/test_consultant_agent.py -q`
- 裁判：`python3 scripts/live_doctor_eval.py --dry-run` 退出码 0，stdout 含场景 A–E，不含密钥。
- 裁判：不设 `LLM_API_KEY` 时 `python3 scripts/live_doctor_eval.py` 退出码 2。
- 通过：判定函数对 A–E 的正反例与上面两条 CLI 一致。本机不跑带密钥的 `live()`。
- 失败：dry-run 访问网络，或场景 E 的期望回复不等于 `medical_advice_refusal` 的固定句。

### 阶段 B4 — 全量与控制面锁

- 裁判：`python3 -m pytest -q` 退出码 0，收集数 ≥ 128。
- 裁判：

```bash
rg -n "TOOL_STATUSES = " agents/appointment/doctor_tools.py
rg -n "MAX_TURNS = 12" agents/appointment_agent.py
rg -n "MAX_INVALID_ATTEMPTS = 3" agents/appointment/booking_tool_loop.py
rg -n "ALLOWED_ACTIONS = " agents/appointment/booking_tool_loop.py
```

- 通过：六态字符串集合不变；`MAX_TURNS` 仍是 12；非法上限仍是 3；动作仍是三元。
- 真 LLM 裁判（父会话，本机跳过）：

```bash
MODEL_PROVIDER=openai-compatible \
LLM_BASE_URL=https://d1api.xin/v1 \
LLM_MODEL=deepseek-v4.1-flash \
LLM_FORCE_STREAM=1 \
LLM_API_KEY=... \
python3 scripts/live_doctor_eval.py
```

退出码 0 才算 5/5。本机结果写 `not measured`。

## 4. 明确不做

- 不建症状→科室关键词表或分诊准确率。
- 不建号源池、叫号、缴费、病历、HIS。
- 不写 finetune / 训练 spec。
- 不恢复 `WeatherMCPTool`、`AgentExecutor` 或任何伪 MCP 医疗插件。
- 不放宽 `TOOL_STATUSES`、`MAX_TURNS = 12`、连续 3 次非法回退、session 隔离、非法状态转移抛错。
- 不把 `_validate_arguments` 的 `[30, 480]` 改成更宽的区间。
- 不改 stream 聚合实现，不用非流式请求打 `d1api.xin`。
- 不在本 VM 上发起带 `LLM_API_KEY` 的调用。
- 不改科室枚举，不改种子医生人数。

## 5. 文件地图

| 工作 | 文件 |
| --- | --- |
| B1 时长改写与 finish 闸门 | 改 `agents/appointment/booking_tool_loop.py`。测 `tests/test_booking_tool_loop.py`。`doctor_tools._validate_arguments` 只被回归测锁住，不改区间。 |
| B2 问句 | 改 `agents/appointment/message_builder.py`（`ASK_SLOT_ORDER`、`question_for`、`first_missing_slot`、`ask_one`、`specialty_menu_question`）。`booking_tool_loop.reply_for_ask` / `reply_for_observation` / `ask_user` 分支改读它。`AppointmentProcessor.handle_incomplete_info` 仍调用 `create_missing_info_questions`，该方法改为只返回一槽。测 `tests/test_booking_tool_loop.py`、`tests/test_appointment_agent.py`。 |
| B3 评测 | 新增 `scripts/live_doctor_eval.py`、`tests/test_live_doctor_eval.py`。不改 `scripts/live_doctor_booking_smoke.py` 的两条场景合同。 |
| B3 回执 | 新增 `docs/resume-claims-doctor-appointment.md`、`docs/live-doctor-eval-results.md`（五行模板，结果栏 `not measured`）。 |
| B4 控制面锁 | 在 `tests/test_doctor_tools.py` 加一条断言 `TOOL_STATUSES`、`ALLOWED_ACTIONS`、`MAX_INVALID_ATTEMPTS`、`MAX_TURNS` 的当前值。该测试在当前 main 上已经为绿，它是锁不是新行为。 |

不改：`config/model_provider.py`、`config/stream_collecting_chat_model.py`、`agents/consultant/consultation_processor.py`（拒答句已固定，E 只调用它）、`db/models.py`。

## 接口（后面的任务只准用这些名字）

`MessageBuilder` 增加：

- `ASK_SLOT_ORDER = ("specialty", "patient_name", "visit_type", "start_time")`
- `question_for(field: str) -> str`：返回现有 `missing_info_prompts` 里的句子，不新写近义句。
- `first_missing_slot(history: dict) -> str | None`：按 `ASK_SLOT_ORDER`，空白、`未知`、`null` 算缺。
- `ask_one(history: dict) -> str`：有缺槽时返回那一句；四槽都在时返回 `请确认是否按已记下的姓名、科室、就诊类型和时间预约。` 这个常量放在类上，名叫 `ASK_WHEN_COMPLETE`。
- `specialty_menu_question() -> str`：`请先选择科室：` + `DEPARTMENT_CHOICES` 用顿号连接 + `。`

`booking_tool_loop` 增加：

- `prepare_find_arguments(history, arguments) -> dict`：非 dict 原样返回。`duration_minutes is None` 或等于 `AppointmentProcessor.resolve_duration(visit_type, None)` 时，拷贝并把该键设为 `None`。其它整数保留。
- `question_blocking_finish` 改为：`first_missing_slot` 非空则 `question_for`，否则 `None`。
- `run` 在校验参数前对 `find_doctor` 的 arguments 调用 `prepare_find_arguments`。`find_doctor(..., default_duration=default_duration_for_find(...))` 使用改写后的 arguments。
- `ask_user`：只要 `first_missing_slot` 非空，就忽略模型 `message`，只发 `question_for` 那一句。四槽都在才使用模型原文；原文空白则 `ask_one`。
- `reply_for_ask`：消息空白时用 `ask_one(history)`，禁止禁句。非空消息原样包上 `[REPLY][预约助手]`（改期「回复 1 或 2」必须原样留下）。
- `reply_for_observation` 的 `no_match + needs_specialty` 使用 `specialty_menu_question()`。其它状态的现有句子保留，但默认分支不得使用禁句，改为 `ask_one({})`。

`create_missing_info_questions(missing_info)`：在 `missing_info` 里取 `ASK_SLOT_ORDER` 的第一项，只返回那一句（前后保持现有的换行包裹）。列表为空时用 `ask_one({})`。

## 任务

### Task 1: 时长注入（B1）

**Files:**
- Modify: `agents/appointment/booking_tool_loop.py`
- Test: `tests/test_booking_tool_loop.py`
- Test: `tests/test_doctor_tools.py`（只加「10 仍非法」，不改 `[30, 480]` 的实现，除非该测试意外失败）

**Interfaces:**
- Produces: `prepare_find_arguments(history, arguments) -> dict`

- [ ] **Step 1: 写失败测试**

在 `tests/test_booking_tool_loop.py` 用真实 `DoctorFinder(appointment_service=FakeDoctorService(...))`，不要用总是返回 `available` 的 `StubFinder` 来证明「不是 invalid_time」。`FakeDoctorService` 来自 `tests/conftest.py`。医生一条即可：`{"id": 1, "name": "陈安", "department": "内科", "bio": "演示人设，非真实医师。匹配用专长词：内科。"}`。

断言：

- `prepare_find_arguments({"visit_type": "复诊"}, {"specialty": "内科", "start_time": "2026-11-20 15:00", "duration_minutes": 15, "doctor_name": None})["duration_minutes"] is None`
- 同样对咨询 `20`、初诊 `None`。
- `duration_minutes=45` 保持 45。
- 工具环：复诊 + `duration_minutes=15` + 合法开始时间 → `finder` 的可用性调用时长为 15 分钟，`last_observation["status"] != "invalid_time"`，回复不含 `就诊时间没能识别`。
- 咨询 + `duration_minutes=None` → 时长 20 分钟，状态不是 `invalid_time`。
- `tests/test_doctor_tools.py`：`find_doctor(args(duration_minutes=10), finder)["status"] == "invalid_arguments"`。现有 600 的用例保持。

- [ ] **Step 2: 跑测试，确认因函数不存在或时长不是 15/20 而失败**

Run: `python3 -m pytest tests/test_booking_tool_loop.py::TestVisitDurationInjection tests/test_doctor_tools.py::TestArgumentValidation::test_should_reject_duration_below_floor -q`

- [ ] **Step 3: 实现 `prepare_find_arguments` 并在 `BookingToolLoop.run` 的 `find_doctor` 分支、以及 `_resolve_slot_choice` 调用 `find_doctor` 之前使用它**

不修改 `_validate_arguments` 的区间。

- [ ] **Step 4: 上述 pytest 通过**

- [ ] **Step 5: Commit** `test: 就诊类型默认时长在 find_doctor 前注入`

### Task 2: finish 四槽（B1）

**Files:**
- Modify: `agents/appointment/booking_tool_loop.py`（可先局部调用 Task 3 的问句；若 Task 3 未落地，本任务内先把 `question_blocking_finish` 扩到四槽，Task 3 再把句子挪到 `MessageBuilder`）
- Test: `tests/test_booking_tool_loop.py`

**Interfaces:**
- Consumes: 四槽名 `patient_name`、`specialty`、`visit_type`、`start_time`
- Produces: `question_blocking_finish` 在任一槽空白时返回问句，否则 `None`

- [ ] **Step 1: 写失败测试**

- 直接 `finish`，history 空，`on_finish` 记录调用：调用次数 0，回复含 `科室`，不含 `缺少患者姓名`。
- history 有姓名、就诊类型、科室，没有 `start_time`，动作 `finish`：调用次数 0，回复含 `时间`，不含 `缺少患者姓名`。
- 四槽都在且已有 `last_observation` 为 available 的医生：现有 `test_should_finish_from_available_observation` 仍通过（它靠 `remember_find_arguments` 写入科室和时间）。

- [ ] **Step 2: 跑新测试，确认现在会调用 `on_finish` 或回复不含科室**

- [ ] **Step 3: 在 `_finish_or_ask` 里对四槽调用阻断。缺槽不进入 `_finish`。**

- [ ] **Step 4: pytest 通过，含 `tests/test_booking_tool_loop.py` 全文件**

- [ ] **Step 5: Commit** `fix: finish 前问齐姓名科室就诊类型和时间`

### Task 3: 追问单一来源（B2）

**Files:**
- Modify: `agents/appointment/message_builder.py`
- Modify: `agents/appointment/booking_tool_loop.py`
- Modify: `agents/appointment/appointment_processor.py`（仅当 `handle_incomplete_info` 仍自己拼多句时；优先只改 `create_missing_info_questions`）
- Test: `tests/test_booking_tool_loop.py`
- Test: `tests/test_appointment_agent.py`

**Interfaces:**
- Produces: 上一节「接口」里的 `MessageBuilder` 方法
- Consumes: Task 2 的 `question_blocking_finish` 改为调用这些方法

- [ ] **Step 1: 写失败测试**

- 空 `ask_user`，history 空：回复等于 `[REPLY][预约助手]请问要挂哪个科室？比如内科、外科或儿科。`，不含 `请补充就诊预约需要的信息`，不含 `姓名`。
- 模型 `ask_user` 原文是 `请问姓名和是初诊还是复诊`，history 空：回复仍只有科室那一句（更新 `test_should_not_fall_back_when_illegal_streak_is_broken`：不断言 `请问姓名`，改断言科室问句且 `seen == []`）。
- `MessageBuilder().create_missing_info_questions(["patient_name", "specialty", "visit_type", "start_time"])` 含科室问句，不含 `请问患者的姓名是`，不含 `初诊、复诊还是咨询`。
- `reply_for_observation({"status": "no_match", "needs_specialty": True})` 含 `请先选择科室` 且含 `内科`（现有症状测试保持）。

- [ ] **Step 2: 跑测试，确认空消息仍是禁句或多槽拼接**

- [ ] **Step 3: 按接口实现。删掉 `booking_tool_loop.py` 里的禁句。**

- [ ] **Step 4: pytest + `rg -n "请补充就诊预约需要的信息" agents tests scripts` 无命中**

- [ ] **Step 5: Commit** `fix: 缺槽追问只走 message_builder 的一句`

### Task 4: 五场景评测脚本（B3）

**Files:**
- Create: `scripts/live_doctor_eval.py`
- Create: `tests/test_live_doctor_eval.py`
- Create: `docs/live-doctor-eval-results.md`

**Interfaces:**
- Consumes: `agents.consultant.consultation_processor.medical_advice_refusal`
- Consumes: `AppointmentAgent.run`、`AppointmentService.save_appointment`、`DoctorService.initialize_default_doctors`（与 `scripts/live_doctor_booking_smoke.py` 相同的临时库接法）
- Produces: 纯函数 `happy_failure`、`ask_failure`、`unknown_doctor_failure`、`busy_failure`、`refusal_failure`；CLI `--dry-run` / 缺密钥退出码 2 / 场景失败退出码 1 / 全过退出码 0

场景（各用新的 `AppointmentAgent`，因此各有 `session_id`）：

- A 齐信息：`我是评测张三，想挂内科初诊，时间2026-11-20 16:00，门诊，不指定医生。` 最多 4 轮，沿用冒烟的追问补全方式。通过：回复含 `预约成功`，该 session 恰好 1 行且有 `id`。
- B 只症状：`我咳嗽两天，有点头疼，想预约就诊。` 通过：回复含 `科室`，该 session 0 行，回复不含 `预约成功`。
- C 不存在的医生：`我是评测王五，想挂内科初诊，时间2026-11-21 10:00，门诊，指定医生王不存在。` 最多 3 轮。通过：回复含 `没有找到`，该 session 0 行，进程没抛。
- D 占用档期：先把内科陈安、林夏在 `2026-11-20 15:00` 起 30 分钟写为已占用。用户：`我是评测赵六，想挂内科初诊，时间2026-11-20 15:00，门诊，指定医生陈安。` 若回复含 `请回复 1 或 2` 或 `可以改到`，下一轮发 `1`。通过：没抛；回复含 `可以改到` / `请回复 1 或 2` / `没有找到可改` / `该时段` 之一；若出现 `预约成功`，新行的开始时间不是 `2026-11-20 15:00`。明确失败文案且未抛，也算通过。
- E 咨询：对 `这是什么病` 与 `帮我开药` 各走 `ConsultantAgent.consult_stream`（拒绝发生在模型调用之前）。通过：拼接回复等于 `medical_advice_refusal` 的返回值。不进知识库，因此不会编造处方。

`docs/live-doctor-eval-results.md` 用表列出 A–E：utterance、期望、结果。结果一律写 `not measured`，并写明本代理环境没有 `LLM_API_KEY`，父会话用阶段 B4 的命令填写。不要写 `[TK]` 或 `TODO`。

- [ ] **Step 1: 写 `tests/test_live_doctor_eval.py`，只测纯函数与 CLI，不调用 `live()`**

正例与反例各至少一条：A 缺 `预约成功` 失败；B 落库失败；C 没有 `没有找到` 失败；D 空回复失败、含 `可以改到` 且未抛则通过；E 固定拒绝通过，含 `阿莫西林` 的回复失败。`--dry-run` 退出码 0 且 stdout 含 `A` `B` `C` `D` `E`。缺 `LLM_API_KEY` 退出码 2。环境变量里的假密钥不得出现在 stdout/stderr。

- [ ] **Step 2: 跑测试，确认脚本还不存在而失败**

- [ ] **Step 3: 实现脚本与结果模板。强制 `LLM_FORCE_STREAM=1`。打印 `eval_db=` 与每轮 user/assistant，不打印密钥。**

- [ ] **Step 4: pytest 与 dry-run、缺密钥三条命令通过**

- [ ] **Step 5: Commit** `test: 增加五场景医生预约评测脚本`

### Task 5: 简历三句回执（B3）

**Files:**
- Create: `docs/resume-claims-doctor-appointment.md`
- Test: `tests/test_doctor_tools.py` 增加 `test_control_plane_constants_stay_locked`（当前即为绿；跑一次确认，不要为了让它变红去改常量）

三句只写代码里为真的话：

1. 动作空间只有 `ask_user` / `find_doctor` / `finish`。证明：`rg -n "ALLOWED_ACTIONS = " agents/appointment/booking_tool_loop.py`，以及 `python3 -m pytest tests/test_booking_tool_loop.py::TestToolLoopFallback::test_should_fall_back_after_three_illegal_actions -q`。
2. 会话按 `session_id` 隔离，槽位可从 SQLite `session_slot_snapshots` 恢复。证明：`python3 -m pytest tests/test_session_scope.py::TestSessionIsolation::test_two_sessions_do_not_contaminate_each_other -q`。
3. 名词与表一致，说的是约医生档期：`doctors`、`doctor_schedules`、`appointments` 上的科室、`specialty`、`visit_type`、`channel`、`doctor_id`。证明：`rg -n "class Doctor|class Appointment|specialty =|visit_type =|channel =|doctor_id =" db/models.py`。不写诊断、处方、分诊准确率、HIS、号源池、真实医院。

文档写每句的命令。实现结束后把本会话实际 pytest 的最后一行抄进文档的「本提交实测」小节。不写未跑过的通过人数。

- [ ] **Step 1: 写文档与控制面锁测试**
- [ ] **Step 2: 跑锁测试与三句命令，把输出原文写入「本提交实测」**
- [ ] **Step 3: Commit** `docs: 给简历三句绑上可复跑命令`

### Task 6: 全量 pytest（B4）

- [ ] **Step 1: `python3 -m pytest -q`**
- [ ] **Step 2: 退出码 0 且收集数 ≥ 128。失败则修到绿，不放宽约束。**
- [ ] **Step 3: 把各阶段命令与最后一行输出记入 PR 正文。**

## 自检

- B1 时长、B1 finish、B2 文案、B3 五场景、B3 简历、B4 pytest 都有任务。
- 禁句、四槽、15/20 注入、控制面不放宽、无密钥不真跑，都有裁判。
- `StreamCollectingChatOpenAI` 不在改动列表里。
- 真 LLM 5/5 本机不可测，文档用 `not measured`，不把 dry-run 写成五场景已绿。
