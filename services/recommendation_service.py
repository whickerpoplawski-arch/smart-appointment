"""
推荐调度服务

职责：
1. 为患者推荐同方向且空闲的医生
2. 定时生成就诊回访提醒
3. 管理推荐调度任务
4. 提供手动触发推荐功能
"""

import schedule
import time
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

# 单患者场景的默认用户ID
DEFAULT_PATIENT_ID = 'default_patient'


class RecommendationService:
    """推荐调度服务类"""

    def __init__(self):
        # 延迟导入避免循环依赖
        self._patient_behavior_service = None
        self._appointment_service = None
        self.is_running = False
        self.scheduler_thread = None

    @property
    def patient_behavior_service(self):
        """懒加载患者行为服务"""
        if self._patient_behavior_service is None:
            from services.patient_behavior_service import PatientBehaviorService
            self._patient_behavior_service = PatientBehaviorService()
        return self._patient_behavior_service

    @property
    def appointment_service(self):
        """懒加载就诊服务"""
        if self._appointment_service is None:
            from services.appointment_service import AppointmentService
            self._appointment_service = AppointmentService()
        return self._appointment_service

    def recommend_interviewers(self, specialty: str, start_time: datetime, end_time: datetime,
                               limit: int = 3) -> List[Dict[str, Any]]:
        """
        推荐指定岗位且在该时段空闲的医生

        医生推荐的核心方法：先按岗位匹配科室，再按可用性过滤。

        Args:
            specialty: 就诊科室
            start_time: 就诊开始时间
            end_time: 就诊结束时间
            limit: 最多返回数量

        Returns:
            推荐医生列表
        """
        try:
            department = self.appointment_service.match_department(specialty)
            doctors = self.appointment_service.get_available_doctors(
                start_time, end_time, department=department
            )
            return doctors[:limit]

        except Exception as e:
            logger.error(f"推荐医生失败: {e}")
            return []

    def generate_recommendations_job(self) -> Optional[List[Dict[str, Any]]]:
        """定时生成推荐的任务：为明天的就诊生成回访提醒"""
        try:
            logger.info("开始执行定时推荐生成任务...")

            tomorrow = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            appointments = self.appointment_service.get_appointments_by_date(tomorrow)

            pending = self.patient_behavior_service.patient_behavior_repo.get_pending_recommendations(
                DEFAULT_PATIENT_ID
            )
            pending_contents = {item['content'] for item in pending}

            recommendations = []
            for appointment in appointments:
                if appointment.get('status') != 'booked':
                    continue

                content = (
                    f"就诊提醒：{appointment['patient_name']} 的 {appointment['specialty']} "
                    f"{appointment['visit_type']} 将于 {appointment['start_time']} 进行，"
                    f"医生 {appointment.get('doctor_name') or '待定'}，渠道为{appointment['channel']}。"
                    "这是到诊提醒，不是诊断。"
                )
                if content in pending_contents:
                    continue

                self.patient_behavior_service.patient_behavior_repo.create_recommendation(
                    user_id=DEFAULT_PATIENT_ID,
                    recommendation_type='appointment_reminder',
                    content=content,
                    doctor_id=appointment.get('doctor_id')
                )
                recommendations.append({'type': 'appointment_reminder', 'content': content})

            if recommendations:
                logger.info(f"成功生成 {len(recommendations)} 条推荐:")
                for rec in recommendations:
                    logger.info(f"- {rec['type']}: {rec['content'][:50]}...")
                return recommendations
            else:
                logger.info("本次没有生成新的推荐")
                return None

        except Exception as e:
            logger.error(f"定时推荐生成任务失败: {str(e)}")
            return None

    def start_scheduler(self) -> bool:
        """启动定时任务调度器"""
        if self.is_running:
            logger.warning("调度器已经在运行中")
            return False

        try:
            # 设置定时任务
            # 每天 9 点、14 点、19 点检查并生成就诊提醒
            schedule.every().day.at("09:00").do(self.generate_recommendations_job)
            schedule.every().day.at("14:00").do(self.generate_recommendations_job)
            schedule.every().day.at("19:00").do(self.generate_recommendations_job)

            # 每 2 小时检查一次（用于测试，实际可根据需要调整）
            schedule.every(2).hours.do(self.generate_recommendations_job)

            self.is_running = True

            def run_scheduler():
                logger.info("推荐调度器已启动")
                while self.is_running:
                    schedule.run_pending()
                    time.sleep(60)  # 每分钟检查一次
                logger.info("推荐调度器已停止")

            # 在后台线程中运行调度器
            self.scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
            self.scheduler_thread.start()
            return True

        except Exception as e:
            logger.error(f"启动推荐调度器失败: {str(e)}")
            return False

    def stop_scheduler(self) -> bool:
        """停止定时任务调度器"""
        try:
            self.is_running = False
            schedule.clear()
            logger.info("推荐调度器已停止")
            return True
        except Exception as e:
            logger.error(f"停止推荐调度器失败: {str(e)}")
            return False

    def run_immediate_check(self) -> Optional[List[Dict[str, Any]]]:
        """立即执行一次推荐检查（用于测试或手动触发）"""
        logger.info("执行立即推荐检查...")
        return self.generate_recommendations_job()

    def get_status(self) -> Dict[str, Any]:
        """获取调度器状态"""
        return {
            "is_running": self.is_running,
            "thread_alive": self.scheduler_thread.is_alive() if self.scheduler_thread else False,
            "next_job": str(schedule.next_run()) if schedule.jobs else None,
            "total_jobs": len(schedule.jobs)
        }


# 测试用的手动运行函数
if __name__ == "__main__":
    print("启动推荐调度器测试...")
    service = RecommendationService()
    service.start_scheduler()

    try:
        # 运行10分钟用于测试
        time.sleep(600)
    except KeyboardInterrupt:
        print("收到中断信号，停止调度器...")
    finally:
        service.stop_scheduler()
