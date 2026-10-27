"""应用启动初始化

Web/Application 层只调用 API 层，因此启动时的知识库、医生种子与
提醒调度初始化都收口在这里，由 app.py 调用。
"""

import logging

from api.core.deps import ensure_default_doctors, doctor_service

logger = logging.getLogger(__name__)


async def initialize_system() -> None:
    """初始化就诊须知库、默认医生与就诊提醒调度服务"""
    try:
        logger.info("🚀 正在初始化就诊预约系统...")

        logger.info("📚 初始化就诊须知库服务...")
        from api.knowledge import get_knowledge_service
        await get_knowledge_service()

        logger.info("👔 初始化医生服务...")
        service = doctor_service()
        ensure_default_doctors(service)
        logger.info(f"✅ 医生就绪，共 {service.get_doctors_count()} 位")

        logger.info("🎯 启动就诊提醒调度服务...")
        from services.recommendation_service import RecommendationService
        recommendation_service = RecommendationService()
        if recommendation_service.start_scheduler():
            logger.info("✅ 就诊提醒调度服务启动成功")
        else:
            logger.warning("⚠️ 就诊提醒调度服务启动失败，提醒功能暂不可用")

        logger.info("✅ 就诊预约系统初始化完成！")

    except Exception as e:
        logger.error(f"❌ 系统初始化失败: {e}")
        raise
