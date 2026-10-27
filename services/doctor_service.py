"""
医生服务层

职责：
1. 管理医生数据
2. 幂等初始化内置的 10 位医生
3. 提供医生、方向与可用性查询
"""

from typing import List, Dict, Any, Optional
from db.db_router import DatabaseRouter
import logging

logger = logging.getLogger(__name__)


class DoctorService:
    """医生服务类 - 管理医生数据和默认初始化"""

    def __init__(self, db_path: str = 'sqlite:///data/doctor_appointment.db'):
        self.db = DatabaseRouter(db_path)

        # 10 个科室，每科 2 名虚构医生。简介只用于同科相似度，不写成真实医师。
        self.default_doctors = [
            {"name": name, "department": department, "bio": f"演示人设，非真实医师。匹配用专长词：{department}。"}
            for department, names in (
                ("内科", ("陈安", "林夏")),
                ("外科", ("吴凯", "郑朗")),
                ("骨科", ("苏晚", "何川")),
                ("儿科", ("许清", "马宁")),
                ("皮肤科", ("丁一", "周宁")),
                ("眼科", ("沈知", "宋年")),
                ("耳鼻喉科", ("冯宁", "韩舒")),
                ("口腔科", ("曹白", "彭年")),
                ("妇产科", ("姚安", "崔宁")),
                ("中医科", ("邓和", "潘宁")),
            )
            for name in names
        ]

    def initialize_default_doctors(self) -> bool:
        """
        幂等初始化默认医生数据

        已存在的医生按姓名跳过，因此重复启动、部分初始化后重启都能补齐到 10 位。
        """
        try:
            existing_names = {
                doctor['name'] for doctor in self.db.doctors.get_all_doctors()
            }

            if len(existing_names) >= len(self.default_doctors) and all(
                item['name'] in existing_names for item in self.default_doctors
            ):
                logger.info(f"数据库中已有 {len(existing_names)} 位医生，跳过初始化")
                return True

            logger.info("开始补齐默认医生")

            for doctor_data in self.default_doctors:
                if doctor_data['name'] in existing_names:
                    continue

                try:
                    doctor_id = self.db.doctors.add_doctor(
                        name=doctor_data['name'],
                        department=doctor_data['department'],
                        bio=doctor_data['bio']
                    )
                    existing_names.add(doctor_data['name'])
                    logger.debug(f"添加医生: {doctor_data['name']} (ID: {doctor_id})")

                except Exception as e:
                    # 并发下可能已被其他进程插入，再查一次确认
                    if self.db.doctors.get_doctor_by_name(doctor_data['name']):
                        existing_names.add(doctor_data['name'])
                        continue
                    logger.error(f"添加医生 {doctor_data['name']} 失败: {e}")
                    return False

            # 验证初始化结果：10 位默认医生必须全部存在
            missing = [
                item['name'] for item in self.default_doctors
                if item['name'] not in existing_names
            ]
            if missing:
                logger.error(f"医生初始化不完整，缺少: {missing}")
                return False

            logger.info(f"医生初始化完成，共 {len(existing_names)} 位")
            return True

        except Exception as e:
            logger.error(f"医生初始化失败: {e}")
            return False

    def get_all_doctors(self) -> List[Dict[str, Any]]:
        """获取所有医生信息"""
        return self.db.doctors.get_all_doctors()

    def get_doctor_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """根据姓名获取医生信息"""
        return self.db.doctors.get_doctor_by_name(name)

    def get_doctor_by_id(self, doctor_id: int) -> Optional[Dict[str, Any]]:
        """根据ID获取医生信息"""
        return self.db.doctors.get_doctor_by_id(doctor_id)

    def get_doctors_by_department(self, department: str) -> List[Dict[str, Any]]:
        """根据科室获取医生"""
        return self.db.doctors.get_doctors_by_department(department)

    def get_all_departments(self) -> List[str]:
        """获取所有科室"""
        return self.db.doctors.get_all_departments()

    def get_doctor_schedules(self, doctor_id: int, date) -> List[Dict[str, Any]]:
        """获取医生指定日期的日程信息"""
        return self.db.doctors.get_doctor_schedules(doctor_id, date)

    def is_doctor_available(self, doctor_id: int, start_time, end_time) -> bool:
        """检查医生在指定时间段是否可用"""
        return self.db.doctors.is_doctor_available(doctor_id, start_time, end_time)

    def add_doctor(self, name: str, department: str = None, bio: str = None) -> int:
        """添加新医生"""
        return self.db.doctors.add_doctor(name, department, bio)

    def get_doctors_count(self) -> int:
        """获取医生总数"""
        return len(self.db.doctors.get_all_doctors())
