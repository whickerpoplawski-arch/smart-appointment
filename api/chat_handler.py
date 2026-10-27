"""
聊天流式处理入口

会话按请求里的 session_id 隔离。槽位、聊天记录和共享状态都放在 SessionRegistry 里，
不再使用模块级的全局 session。

依赖说明：Agent 依赖 LLM 凭证与向量检索依赖，这里改为首次对话时惰性构建。
管理页面（面试官、日程、候选人分析、知识库）因此不依赖模型凭证即可打开，
只有真正进入对话时才需要完整的模型配置。
"""
import uuid

# 惰性构建的任务分类 Agent 与会话登记表。Agent 壳可以复用，状态必须按 session 分开。
_task_agent = None
_session_registry = None


def get_session_registry():
    """获取（必要时创建）会话登记表。"""
    global _session_registry
    if _session_registry is None:
        from agents.session_registry import SessionRegistry
        _session_registry = SessionRegistry()
    return _session_registry


def get_task_agent():
    """获取（必要时创建）任务分类 Agent"""
    global _task_agent
    if _task_agent is None:
        from agents.task_classification_agent import TaskClassificationAgent
        from agents.interview_agent import InterviewAgent
        from agents.consultant_agent import ConsultantAgent

        _task_agent = TaskClassificationAgent(
            InterviewAgent(),
            ConsultantAgent()
        )
    return _task_agent


async def ProcessUserInput_stream(user_input, session_id=None):
    """
    user_input: 候选人输入
    session_id: 本次会话。缺省时新建一个，调用方应在后续请求里带回。
    """
    if not session_id:
        session_id = str(uuid.uuid4())

    try:
        registry = get_session_registry()
        record = registry.get(session_id)
        task_agent = get_task_agent()
        task_agent.bind_session(record)
    except Exception as e:
        # 模型或向量依赖未配置时给出可读提示，而不是让聊天接口直接 500
        yield f"[REPLY][面试预约助手]服务暂未就绪，请检查模型配置后重试。（{e}）"
        return

    try:
        async for token in task_agent.classify_task_stream(user_input):
            yield token
    finally:
        registry.save(record)
