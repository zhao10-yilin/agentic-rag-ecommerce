## 简历项目描述

**项目名称**：生产级 PDF 非标数据解析流水线（PDF Parser Pipeline）

**技术栈**：Python 3.13、Pydantic、Tenacity、FastAPI、Celery、Redis、psutil、SQLite WAL

**项目背景**：基于开源 MinerU 工具链构建企业级 PDF 解析系统，处理扫描件/版式文档向 Markdown 的转换，支撑下游 RAG 知识库与文档智能场景。

**核心职责**：独立负责系统从"可运行脚本"到"生产级架构"的完整设计与实现。

**关键成果**：
- 设计并实现 4 层可组合文本清洗管道（正则去噪、n-gram 重复过滤、OCR 校正、段落修复），将下游可用率从"需要人工清洗"提升至"直接入库"
- 构建 OOM 硬杀防护体系：worker 自监控看门狗 + 进程池自动重建 + 全局内存门控，彻底消除解析大型 PDF 时的系统级雪崩风险
- 完成服务化改造：CLI 批量模式 / FastAPI 异步接口 / Celery 分布式 Worker 三种部署形态并存，解决高并发下的资源排队与隔离问题
- 设计 SQLite WAL 事务性 Checkpoint + SHA-256 文件完整性校验 + 产物 Manifest 校验的三层崩溃恢复机制
