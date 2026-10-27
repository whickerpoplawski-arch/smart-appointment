# 面试预约控制面升级计划

**改写后的任务**

把面试预约从「模型抽槽、Python 写死五步」抬成一条可核对的垂直工具环：每轮只在 `ask_user`、`find_interviewer`、`finish` 里选动作；失败时在域内改期再查；会话按请求隔离；状态机和转交有预算；伪 MCP 与悬空文档删掉。做完才配说三句现在还不许说的话：动作空间只有预约的垂直 ReAct、这个 Agent 能多用户跑、面试时不会死在名词上。范围仍只做面试预约。

**要用的方法和工具**

- 对话系统：参数用已有 `FIND_INTERVIEWER_SCHEMA` 与 `_validate_arguments`；`invalid_arguments` 最多重填 3 次，超时或连续非法回退今天的 `InterviewAgent.run_stream`。观察值用代码里的 `TOOL_STATUSES`（`available` / `unavailable` / `not_found` / `no_match` / `invalid_arguments` / `invalid_time`），交给显式策略函数再选下一动作。假模型沿用 `tests/test_interview_agent.py` 的 `FakeListChatModel`。
- 会话存储：`ChatRequest` 增加 `session_id`，并删除只收不用的 `state`（客户端 state 会绕过转移表）。`interview_history`、`InMemoryChatMessageHistory`、`SharedState` 按 session 分字典，删除 `api/chat_handler.py` 的 `global_session_id`。槽位快照（缺哪个槽、是否 `awaiting_confirmation`）写入 SQLite，重启按 session 恢复。
- 调度：`no_match`，或 `unavailable` 且 `alternatives` 为空时，调用 `search_alternative_slots`。扫描搬 `CandidateBehaviorAgent._find_available_times`，收成未来两个工作日、时长用 `DEFAULT_DURATION_BY_ROUND`、最多两个选项写回 observation；用户选定后再第二次 `find_interviewer`。
- 挑错：删除 `WeatherMCPTool` 和落库后的 `AgentExecutor` 旁路。`requirements.txt` 去掉未被 import 的 `mcp`、`langchain-experimental`，以及只被该旁路 import 的 `aiohttp`。删除 README 第 219–220 行两篇不存在的 spec 链接；`interviewer_tools.py` 里「后训练后是 Planner 小模型」改成当前调用方。`pay` / `statistics` 从提示词删除，不另做功能，也不补写后训练文档。
- 状态机：`set_state` 必须先过 `can_transition_to`，非法抛错。unrelated 深度 ≥ 1 只走 `UnrelatedHandler` 固定回复，禁止再进 `process_task_stream`。主对话 `max_turns=12`，到点输出已收集槽位并停止。聊天流里的 `[THOUGHT]` 改日志，只留 `[REPLY]`。

**野心目标**

做完后，审查者不用真模型，只凭下面这组已经跑绿的离线 pytest 所锁住的合同，就能核对「垂直 ReAct、多用户、名词诚实」这三句。

**外部验证**

当前 `main` 上已跑通，68 passed，不调用真 LLM：

`python3 -m pytest tests/test_interviewer_tools.py tests/test_interviewer_finder.py tests/test_interview_agent.py tests/test_task_classification_agent.py tests/test_interview_service.py`

它们锁住的是现在的合同：工具参数与六态、面试官匹配、抽槽合并与推荐确认、状态转移与分类提示词、事务落库。本阶段不新增测试。真 LLM 联调不是这条检查。

**需要用户提供的**

本阶段不集成模型。以后若要做真 LLM 联调，需要 `LLM_API_KEY` 与 `LLM_BASE_URL`（`config/model_provider.py` 的 OpenAI 兼容路径；Azure 则是 `AZURE_OPENAI_API_KEY` 与 `AZURE_OPENAI_ENDPOINT`）。除此以外没有。

按这个方案执行，还是要调整？
