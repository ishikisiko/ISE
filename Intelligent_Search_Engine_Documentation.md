# 智能搜索引擎系统技术文档

## 摘要

本系统是一个基于检索增强生成（Retrieval-Augmented Generation, RAG）技术的智能搜索引擎，旨在提供精确、上下文感知的答案。系统把本地知识库、网络搜索、结构化 skill 和高级重排作为独立工具交给唯一的 Agentic Loop；模型选择工具，确定性 preflight、证据策略、critic 和预算负责裁决与收口，并支持多模态输入。

## 系统架构

### 高级架构图

```text
用户查询（CLI / Flask）
        |
        v
LangChainOrchestrator
  时间解析 + QueryAnalysis
  闲聊 / 视觉 / 关键歧义短路
        |
        v
LangGraph Agentic Loop: act -> observe -> evaluate
  |-- web_search / search_recovery
  |-- local_docs -> FAISS
  `-- registry skills -> provider APIs
        |
        v
EvidenceLedger
  归一化、来源等级、去重、provenance、保留决策
        |
        v
统一 critic + 可选语义 judge + 全局/每工具预算
        |
        v
答案 + 引用 + QueryExecutionTrace + durable audit
```

### 技术栈

#### 大型语言模型 (LLM)
- **OpenCode Go**: 多模型聚合入口 (DeepSeek, GLM, Kimi, Qwen, MiniMax 等)
- **Anthropic**: Claude-3 (Sonnet, Haiku, Opus)
- **智谱AI**: GLM-4.6, GLM-4V
- **MiniMax**: MiniMax-M2 (支持思考模式)
- **OpenRouter**: 多模型聚合平台
- **通义千问**: Qwen系列 (含重排序模型)

#### 向量数据库与嵌入
- **FAISS**: 高效相似性搜索库，提供CPU/GPU加速
- **HuggingFace Embeddings**: all-MiniLM-L6-v2 及多种开源嵌入模型
- **LangChain VectorStore**: 统一向量存储接口

#### 后端框架
- **Flask**: 轻量级Web服务器框架
- **Python 3.x**: 核心开发语言
- **LangChain**: RAG管线编排框架 (LCEL表达式语言)

#### 搜索与数据源
- **SerpAPI**: Google搜索结果API
- **Yahoo Finance API**: 金融数据接口
- **Google Cloud Vision API**: 图像识别与视觉检索

#### 文档处理
- **PyPDF2/pypdf**: PDF文档解析
- **Unstructured**: 多格式文档处理
- **Sentence-Transformers**: 文档向量化

## 方法论与实施细节

### 1. Registry Skill 路由

结构化能力是独立 skill，不再先经过统一领域分类器。`SkillRegistry` 只注册依赖和配置满足的
skill；各 handler 用确定性 preflight 判断是否接受查询，拒绝或 provider 无数据时回落 Web。

#### 1.1 Skill 工具面
```python
# 模型可见的是独立工具，而不是一个统一 domain router
tools = {
    "weather_conditions",
    "nearby_places",
    "route_directions",
    "finance_market_data",
    "sports_schedule",
}
```

**实现机制**:
- **可用性门**: 按 Python 依赖、配置 key 和 disabled 配置生成实际工具面
- **确定性 preflight**: 要求显式实体、地点或起终点，不猜用户位置或标的
- **统一执行**: 唯一的 LangGraph loop 使用 registry 派生的工具面

#### 1.2 API数据源集成
```python
# LangGraph loop 的 registry 工具执行入口
result = skill_registry.execute(
    "weather",
    {"query": query},
    options=retrieval_options,
)
```

**特性**:
- **循环收敛**: skill 证据满足约束时 critic 允许直接结束，否则模型可继续选择其他工具
- **统一证据**: provider 结果归一化为带 skill/tool-call provenance 的 `EvidenceItem`
- **错误处理**: API调用失败时自动回退到搜索模式

#### 1.3 统一终止 critic

LangGraph loop 把当前证据、约束、进度和预算归一化为 `TerminationContext`，并且只调用
`evaluate_termination` 作出继续、澄清、证据不足、预算终止或返回决定。
顶层 `termination` 是轮数、停滞/错误阈值、judge 频率和 judge 模型的唯一配置块。确定性规则和预算
优先于语义 judge：judge 可以否决规则通过，但不能清除证据缺口、逆转 hard stop 或扩充预算。
每次 verdict 的 `action`、`rule_hits`、`deterministic_pass` 与 `hard_stop` 都进入 trace。

#### 1.4 多模态查询处理
```python
# 视觉检索流程
def _perform_visual_retrieval(self, images: List[Dict]) -> Optional[Dict]:
    """
    1. 接收用户上传的图片
    2. 使用Google Cloud Vision API进行web检测
    3. 提取最佳猜测标签和关联实体
    4. 生成视觉线索作为上下文
    """
    # Base64编码 → Vision API → 标签提取
    # 生成提示词：结合图片和搜索到的元数据
```

**多模态策略**:
- **视觉模型检测**: 自动识别LLM是否支持视觉理解（GPT-4V, Claude-3, Gemini, GLM-4V）
- **元数据增强**: 即使模型不支持视觉，也通过Google Vision提取图像线索
- **混合提示**: 结合图像内容和外部元数据生成提示

### 2. 本地RAG实施 (Local RAG Implementation)

#### 2.1 索引策略

**分块参数**:
```python
chunk_size: int = 1000      # 分块大小
chunk_overlap: int = 200    # 重叠大小
embedding_model: str = "all-MiniLM-L6-v2"
```

**索引流程**:
```python
# 1. 文件读取
reader = FileReader(data_path)
documents = reader.load()  # 支持 .txt, .md, .pdf

# 2. 向量化
vector_store = LangChainVectorStore(model_name=embedding_model)
chunk_count = vector_store.index(documents)

# 3. 存储优化
# - FAISS索引存储在内存中
# - 文档元数据（来源、路径）持久化
```

**支持的文件格式**:
- PDF (通过PyPDF2解析)
- Markdown (.md)
- 纯文本 (.txt)
- HTML (通过unstructured处理)

#### 2.2 检索逻辑

**查询处理流程**:
```python
def answer(self, query: str, **kwargs) -> Dict:
    # 1. 向量相似性搜索
    retrieved_docs = self.vector_store.search(query, k=num_retrieved_docs)

    # 2. 上下文构建
    context = "\n".join([doc.content for doc in retrieved_docs])

    # 3. LLM调用
    response = self.llm_client.chat(
        system_prompt="你是一个有用的助手...",
        user_prompt=f"Context:\n{context}\n\nQuestion: {query}"
    )

    # 4. 响应增强
    answer = response.content
    if retrieved_docs:
        answer += "\n\n**本地文档来源：**\n"
        for idx, doc in enumerate(retrieved_docs, 1):
            answer += f"{idx}. {doc.source}\n"
```

**检索优化**:
- **Top-K检索**: 默认返回5个最相似文档
- **余弦相似性**: 使用向量余弦相似度
- **源追踪**: 保留文档来源信息，便于引用

### 3. 排名调整与筛选 (Ranking & Filtering)

#### 3.1 Qwen3重排序器

**架构**:
```python
class Qwen3Reranker(BaseReranker):
    """
    使用阿里云DashScope的Qwen3-rerank模型
    专门针对查询-文档相关性进行精细排序
    """
```

**实现机制**:
```python
def rerank(self, query: str, hits: List[SearchHit]) -> List[RerankedHit]:
    # 1. 文档预处理
    doc_texts = []
    for hit in hits:
        text = f"{title}\n{url}\n{snippet}".strip()
        doc_texts.append(text)

    # 2. API调用
    payload = {
        "model": "qwen3-rerank",
        "input": {
            "query": query,
            "documents": doc_texts,
        },
        "parameters": {
            "return_documents": True,
            "top_n": len(doc_texts),
        }
    }

    # 3. 分数解析与排序
    scores = [result['relevance_score'] for result in results]
    reranked = sorted(zip(hits, scores), key=lambda x: x[1], reverse=True)
```

**重排序参数**:
```python
min_rerank_score: float = 0.0    # 最小分数阈值
max_per_domain: int = 1          # 每个域名最大结果数
```

#### 3.2 相关性过滤

**过滤策略**:
- **分数阈值**: 过滤掉低于`min_rerank_score`的候选结果
- **域名去重**: 每个域名最多保留`max_per_domain`个结果
- **内容质量检查**: 跳过空摘要或无效URL

### 4. 代理工作流程 (Agent Workflow for Complex Queries)

#### 4.1 多步推理实现

**决策流程图**:
```text
用户查询
    │
    ▼
[时间约束解析 + QueryAnalysis]
    │
    ▼
[小对话检测] ───→ 是 ──→ 直接LLM响应
    │
    否
    ▼
[LangGraph act：模型选择当前可用工具或提议最终答案]
    │
    ▼
[observe：preflight + provider + EvidenceItem]
    │
    ▼
[EvidenceLedger：来源等级、去重、provenance、保留决策]
    │
    ▼
[evaluate：critic + 可选 judge + 预算]
    ├── continue ──→ 下一轮 act
    └── terminal ──→ 返回答案/不足说明 + trace
```

**复杂查询示例**: *"英伟达最新财报对股价的影响"*

**执行步骤**:
1. **时间解析**: 识别"最新" → 注入当前日期
2. **工具选择**: 模型选择 `finance_market_data`
3. **确定性预检**: 解析 NVDA 与 quote/history 时间窗后再调用 provider
4. **观察与评估**: finance 证据进入 ledger，critic 检查权威性、时间和回答覆盖
5. **补充检索**: 如仍缺定性影响证据，模型可调用 `web_search` 或 `search_recovery`
6. **终止**: 候选答案通过同一 critic 后返回，或在预算上限给出明确不足说明

#### 4.2 并行处理能力

**搜索源并行**:
```python
# 支持多个搜索源并行
active_sources = ["brave", "google", "mcp"]
# 每个源返回结果后统一进行重排序和合并
```

### 5. 多模态支持 (Multimodal Support)

#### 5.1 图像输入处理

**上传格式**:
```python
images: List[Dict[str, str]] = [
    {
        "filename": "image.jpg",
        "content_type": "image/jpeg",
        "base64": "data:image/jpeg;base64,/9j/4AAQ..."
    }
]
```

**处理流程**:
```
1. Base64解码
2. Google Vision API调用
   - Web Detection (网络图像匹配)
   - Label Detection (标签识别)
   - Object Localization (对象定位)
3. 提取元数据:
   - bestGuessLabels: 最佳猜测标签
   - webEntities: 关联实体
   - similarImages: 相似图像
4. 提示词构建:
   - 视觉模型: 提供图像 + 元数据
   - 非视觉模型: 仅提供元数据
5. LLM响应生成
```

**支持的多模态模型**:
- GPT-4V, GPT-4o
- Claude-3 (Sonnet, Haiku) + Vision
- Gemini-pro-vision
- GLM-4V, GLM-4.5V
- MiniMax (多模态版本)

#### 5.2 PDF文档处理

**解析流程**:
```python
# PyPDF2解析
import PyPDF2

with open(pdf_path, 'rb') as file:
    reader = PyPDF2.PdfReader(file)
    text = ""
    for page in reader.pages:
        text += page.extract_text()

# LangChain文档加载
from langchain.document_loaders import PyPDFLoader
loader = PyPDFLoader(pdf_path)
pages = loader.load()
```

**多模态RAG**:
- 检索包含图表、表格的PDF段落
- 结合图像OCR结果增强上下文
- 支持混合内容（文本+图像）查询

### 6. 性能优化策略

#### 6.1 缓存机制

**管道缓存**:
```python
class ReActSearchRecoveryTool:
    def _get_chain(self):
        # 同一工具实例懒加载并复用统一 SearchRAGChain；每轮调用预算单独重置
        if self._rag_chain is None:
            self._rag_chain = SearchRAGChain(...)
        return self._rag_chain
```

**文档快照**:
```python
def _snapshot_local_docs(self) -> Optional[tuple]:
    # 基于文件路径和修改时间的快照
    records = []
    for file in files:
        if file.endswith((".txt", ".md", ".pdf")):
            records.append((full_path, os.path.getmtime(full_path)))
    return tuple(sorted(records))
```

#### 6.2 超时与重试

**LLM客户端重试策略**:
```python
retry_strategy = Retry(
    total=max_retries,           # 默认3次
    status_forcelist=[429, 500, 502, 503, 504],
    backoff_factor=backoff_factor,  # 退避因子
    allowed_methods=["HEAD", "GET", "POST", ...]
)
```

**超时配置**:
```python
LLM请求: timeout=60秒
搜索API: timeout=15秒
重排序API: timeout=15秒
```

#### 6.3 循环与工具预算

**统一配置**:
```python
termination = {
    "max_iterations": 5,
    "judge_interval": 2,
    "tool_budgets": {
        "web_search": 3,
        "search_recovery": 2,
        "local_docs": 2,
    },
}
```

### 7. 监控与日志

#### 7.1 时间记录器

**TimingRecorder指标**:
```python
{
    "总响应时间": "1500ms",
    "LLM调用次数": 3,
    "LLM调用时间": [
        {"label": "loop_act", "duration": "800ms"},
        {"label": "termination_judge", "duration": "500ms"}
    ],
    "工具调用时间": [
        {"tool": "google_vision", "duration": "350ms"},
        {"tool": "serpapi_search", "duration": "500ms"}
    ],
    "领域智能类型": "finance"
}
```

#### 7.2 错误处理

**错误类型**:
- `invalid_tool_request`: 模型工具调用格式无效
- `tool_errors_unrecoverable`: 工具错误达到配置上限且没有成功观察
- `skill_error`: registered skill 调用失败
- `search_unavailable`: 搜索不可用

**回退策略**:
```python
# API失败 → 搜索模式
# 搜索失败 → 本地RAG模式
# 本地RAG失败 → 直接LLM回答
```

---

## 总结

本智能搜索引擎系统通过集成多种先进技术（LLM、RAG、重排序、多模态处理），实现了从简单关键词匹配到智能问答的技术跨越。系统的核心优势在于：

1. **智能决策**: 自动判断是否需要搜索，避免不必要的API调用
2. **多模态融合**: 支持文本、图像、PDF等多种输入格式
3. **领域专业化**: 针对天气、交通、金融等垂直领域提供优化
4. **高质量检索**: 结合向量搜索与重排序算法
5. **可扩展架构**: 模块化设计，易于添加新的数据源和模型

该系统可广泛应用于智能客服、知识问答、文档分析等场景，为用户提供准确、快速的智能检索体验。
