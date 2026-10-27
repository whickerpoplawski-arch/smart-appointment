# services/knowledge_service.py

import numpy as np
import faiss
from typing import List, Dict, Tuple, Optional
from db.db_router import DatabaseRouter
from .text_embedding import embed_input
import logging

logger = logging.getLogger(__name__)

class KnowledgeService:
    """知识库服务类 - 结合数据库存储和向量检索"""
    
    def __init__(self, db_path: str = 'sqlite:///data/doctor_appointment.db'):
        # 使用统一的DatabaseRouter，符合架构设计
        self.db_router = DatabaseRouter(db_path)
        self.db = self.db_router.knowledge  # 通过router访问knowledge repository
        self.index = None
        self.document_ids = []  # 维护文档ID与索引位置的映射
        self.initialized = False

        # 默认知识库：空库时写入的 8 条就诊须知，不写诊断或处方
        self.default_knowledge = [
            {
                "content": "预约步骤：请说明姓名、科室、就诊类型和方便的时间。系统只按您说出的科室匹配空闲医生并占档。只说症状、不说科室时，会请您从科室列表里选择，不会按症状分诊。",
                "category": "预约步骤",
                "keywords": ["预约", "步骤", "科室", "怎么约", "症状"]
            },
            {
                "content": "就诊类型只影响时长：初诊 30 分钟，复诊 15 分钟，咨询 20 分钟。认不出类型时按 30 分钟。就诊类型不参与医生筛选。",
                "category": "就诊类型",
                "keywords": ["初诊", "复诊", "咨询", "时长", "30", "15", "20"]
            },
            {
                "content": "互联网医院设备：请提前 5 分钟进入，确认网络、摄像头和麦克风可用。这是演示渠道，不是真实医院的远程诊疗。",
                "category": "互联网医院",
                "keywords": ["互联网医院", "视频", "摄像头", "麦克风", "网络"]
            },
            {
                "content": "门诊演示地址：请提前 15 分钟到达「演示园区」门诊登记。地址是虚构演示，不是真实医院。",
                "category": "门诊",
                "keywords": ["门诊", "演示园区", "地址", "提前", "登记"]
            },
            {
                "content": "携带物品但不入库：纸质材料请患者自行携带。系统只保存演示姓名、科室、就诊类型、渠道和档期，不建立病历。",
                "category": "携带物品",
                "keywords": ["携带", "材料", "不入库", "病历"]
            },
            {
                "content": "改期：请至少提前 24 小时说明。同一条预约最多改期一次。系统会在接下来两个工作日内最多给出两个可选时段。",
                "category": "改期",
                "keywords": ["改期", "取消", "24小时", "两个工作日", "两个"]
            },
            {
                "content": "到诊提醒只帮助回忆已经约好的档期，不是诊断，也不能代替前往医疗机构。",
                "category": "到诊提醒",
                "keywords": ["提醒", "到诊", "不是诊断", "档期"]
            },
            {
                "content": "本演示不收费，也没有支付工具。",
                "category": "费用",
                "keywords": ["收费", "费用", "支付", "免费"]
            }
        ]

    async def initialize(self):
        """初始化知识库服务"""
        try:
            # 检查数据库中是否已有数据
            existing_docs = self.db.get_all_documents()
            
            if not existing_docs:
                logger.info("数据库为空，初始化默认知识库")
                await self._create_default_knowledge()
            else:
                logger.info(f"从数据库加载了 {len(existing_docs)} 条知识")
            
            # 构建向量索引
            await self._build_vector_index()
            self.initialized = True
            logger.info("知识库服务初始化完成")
            
        except Exception as e:
            logger.error(f"知识库服务初始化失败: {e}")
            raise

    async def _create_default_knowledge(self):
        """创建默认知识库

        嵌入服务不可用时仍然写入文档本体（embedding 为空），保证 FAQ 内容可用；
        向量会在后续具备凭证时补算，不因为一次 Embedding 失败丢掉整批种子数据。
        """
        for knowledge in self.default_knowledge:
            embedding = None
            try:
                # 生成嵌入向量
                text_for_embedding = f"{knowledge['content']} {' '.join(knowledge['keywords'])}"
                embedding = embed_input(text_for_embedding)
            except Exception as e:
                logger.warning(f"默认知识嵌入生成失败，先写入文档正文: {e}")

            try:
                # 保存到数据库
                self.db.add_document(
                    content=knowledge['content'],
                    category=knowledge['category'],
                    keywords=knowledge['keywords'],
                    embedding=embedding
                )
                logger.debug(f"添加默认知识: {knowledge['content'][:50]}...")

            except Exception as e:
                logger.error(f"添加默认知识失败: {e}")

    async def _build_vector_index(self):
        """构建向量索引"""
        try:
            documents = self.db.get_all_documents()
            if not documents:
                logger.warning("没有文档可用于构建索引")
                return

            embeddings = []
            self.document_ids = []
            
            for doc in documents:
                if doc.get('embedding'):
                    embeddings.append(doc['embedding'])
                    self.document_ids.append(doc['id'])
                else:
                    # 没有嵌入向量时尝试补算；嵌入服务不可用则跳过该文档，
                    # 不让整轮索引构建失败（文档仍可在知识库管理页查看和编辑）
                    logger.warning(f"文档 {doc['id']} 缺少嵌入向量，正在生成...")
                    try:
                        text_for_embedding = f"{doc['content']} {' '.join(doc.get('keywords', []))}"
                        embedding = embed_input(text_for_embedding)
                    except Exception as e:
                        logger.warning(f"文档 {doc['id']} 嵌入生成失败，跳过索引: {e}")
                        continue

                    # 更新数据库
                    self.db.update_document(doc['id'], embedding=embedding)

                    embeddings.append(embedding)
                    self.document_ids.append(doc['id'])

            if embeddings:
                # 创建FAISS索引
                embeddings_array = np.array(embeddings).astype('float32')
                dimension = embeddings_array.shape[1]
                self.index = faiss.IndexFlatIP(dimension)  # 内积相似度
                self.index.add(embeddings_array)
                logger.info(f"构建向量索引完成，包含 {len(embeddings)} 个向量")
            else:
                logger.warning("没有有效的嵌入向量，无法构建索引")

        except Exception as e:
            logger.error(f"构建向量索引失败: {e}")
            raise

    async def search(self, query: str, top_k: int = 3, category: str = None) -> List[Dict]:
        """搜索相关文档"""
        if not self.initialized or self.index is None:
            logger.warning("知识库服务未初始化或索引不可用")
            return []

        try:
            # 生成查询的嵌入向量
            query_embedding = embed_input(query)
            query_array = np.array([query_embedding]).astype('float32')
            
            # 向量搜索
            scores, indices = self.index.search(query_array, min(top_k * 2, len(self.document_ids)))  # 多检索一些候选
            
            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx < len(self.document_ids):
                    doc_id = self.document_ids[idx]
                    doc = self.db.get_document(doc_id)
                    
                    if doc:
                        # 如果指定了分类过滤
                        if category and doc.get('category') != category:
                            continue
                            
                        doc['score'] = float(score)
                        doc['rank'] = len(results) + 1
                        results.append(doc)
                        
                        # 达到所需数量就停止
                        if len(results) >= top_k:
                            break
            
            return results
            
        except Exception as e:
            logger.error(f"搜索知识库失败: {e}")
            return []

    async def add_document(self, content: str, category: str, keywords: List[str] = None) -> bool:
        """添加新文档"""
        try:
            if keywords is None:
                keywords = []
            
            # 生成嵌入向量
            text_for_embedding = f"{content} {' '.join(keywords)}"
            embedding = embed_input(text_for_embedding)
            
            # 保存到数据库
            doc_id = self.db.add_document(content, category, keywords, embedding)
            
            # 重建索引
            await self._build_vector_index()
            
            logger.info(f"成功添加文档 {doc_id}: {content[:50]}...")
            return True
            
        except Exception as e:
            logger.error(f"添加文档失败: {e}")
            return False

    async def update_document(self, doc_id: int, content: str = None, category: str = None, keywords: List[str] = None) -> bool:
        """更新文档"""
        try:
            # 如果更新了内容或关键词，需要重新生成嵌入向量
            embedding = None
            if content is not None or keywords is not None:
                # 获取当前文档信息
                current_doc = self.db.get_document(doc_id)
                if not current_doc:
                    return False
                
                # 使用新值或保持原值
                final_content = content if content is not None else current_doc['content']
                final_keywords = keywords if keywords is not None else current_doc.get('keywords', [])
                
                # 生成新的嵌入向量
                text_for_embedding = f"{final_content} {' '.join(final_keywords)}"
                embedding = embed_input(text_for_embedding)
            
            # 更新数据库
            success = self.db.update_document(doc_id, content, category, keywords, embedding)
            
            if success and embedding is not None:
                # 重建索引
                await self._build_vector_index()
            
            return success
            
        except Exception as e:
            logger.error(f"更新文档失败: {e}")
            return False

    async def delete_document(self, doc_id: int, soft_delete: bool = True) -> bool:
        """删除文档"""
        try:
            success = self.db.delete_document(doc_id, soft_delete)
            
            if success:
                # 重建索引
                await self._build_vector_index()
            
            return success
            
        except Exception as e:
            logger.error(f"删除文档失败: {e}")
            return False

    def get_all_documents(self, include_inactive: bool = False) -> List[Dict]:
        """获取所有文档"""
        return self.db.get_all_documents(include_inactive)

    def get_document(self, doc_id: int) -> Dict:
        """获取指定文档"""
        return self.db.get_document(doc_id)

    def get_all_categories(self) -> List[str]:
        """获取所有分类"""
        return self.db.get_all_categories()

    def get_documents_count(self) -> int:
        """获取文档总数"""
        return self.db.get_documents_count()

    def search_by_category(self, category: str) -> List[Dict]:
        """按分类搜索文档"""
        return self.db.search_documents_by_category(category)

    def search_by_keywords(self, keywords: List[str]) -> List[Dict]:
        """按关键词搜索文档"""
        return self.db.search_documents_by_keywords(keywords)
