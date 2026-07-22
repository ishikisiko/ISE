# 智能搜索引擎系统技术文档

## 摘要

本系统是一个基于检索增强生成（Retrieval-Augmented Generation, RAG）技术的智能搜索引擎，旨在提供精确、上下文感知的答案。该系统通过智能选择数据源、结合本地知识库与网络搜索、实施高级重排算法，并支持多模态输入，实现了对复杂查询的高质量响应。系统采用智能编排器模式，能够自动决策是否需要实时搜索，并根据查询类型选择最适合的处理流程。

## 系统架构

### 高级架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        用户查询                                   │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │   图片输入       │  │   文本查询       │  │   混合输入       │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   SmartSearchOrchestrator                        │
│                   智能编排控制器                                  │
│  ┌───────────────┐ ┌───────────────┐ ┌─────────────────────────┐ │
│  │ 时间约束解析   │ │ 领域分类器     │ │ 小对话检测             │ │
│  └───────────────┘ └───────────────┘ └─────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │              IntelligentSourceSelector                      │ │
│  │                智能源头选择器                               │ │
│  │   [天气] [交通] [金融] [体育] [通用]                         │ │
│  └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              │
                ┌─────────────┴─────────────┐
                │                           │
                ▼                           ▼
┌──────────────────────────┐    ┌──────────────────────────┐
│       决策引擎            │    │      强制搜索            │
│   ┌───────────────┐      │    │   (force_search模式)      │
│   │ 是否需要搜索？ │      │    └──────────────────────────┘
│   └───────────────┘      │
│       │  │               │
│       ▼  ▼               ▼
│   ┌──────────┐ ┌──────────────────┐
│   │ 不需要搜索│ │   需要搜索        │
│   └──────────┘ └──────────────────┘
└──────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      管道选择层                                   │
│  ┌────────────────┐ ┌────────────────┐ ┌──────────────────────┐ │
│  │   LocalRAG     │ │  SearchRAG     │ │   SearchRAG (Hybrid) │ │
│  │   本地RAG      │ │  搜索RAG       │ │    混合RAG           │ │
│  └────────────────┘ └────────────────┘ └──────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                │           │                     │
                ▼           ▼                     ▼
┌──────────────────┐ ┌───────────────┐ ┌──────────────────────────┐
│   向量存储       │ │  搜索客户端    │ │   搜索客户端 + 本地文档   │
│  (FAISS索引)    │ │  (SerpAPI等)   │ │   + 向量存储            │
└──────────────────┘ └───────────────┘ └──────────────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │  重排序器       │
                    │ (Qwen3 Rerank) │
                    └─────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      LLM生成层                                   │
│  ┌────────────────┐ ┌────────────────┐ ┌──────────────────────┐ │
│  │   OpenAI      │ │   Anthropic    │ │     GLM/智谱AI        │ │
│  │   Claude      │ │   Gemini       │ │    MiniMax等        │ │
│  └────────────────┘ └────────────────┘ └──────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      响应结果                                    │
│  ┌───────────────┐ ┌───────────────┐ ┌─────────────────────────┐ │
│  │   答案内容     │ │   来源引用     │ │    搜索元数据          │ │
│  └───────────────┘ └───────────────┘ └─────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### 技术栈

#### 大型语言模型 (LLM)
- **OpenAI**: GPT-3.5-turbo, GPT-4 系列
- **Anthropic**: Claude-3 (Sonnet, Haiku, Opus)
- **Google**: Gemini-pro, Gemini-pro-vision
- **智谱AI**: GLM-4.6, GLM-4V
- **MiniMax**: MiniMax-M2 (支持思考模式)
- **HKGAI**: HKGAI-V1
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

### 1. 智能源头选择 (Intelligent Source Selection)

系统采用多层次决策机制来选择最合适的数据源：

#### 1.1 领域检测与路由
```python
# 支持的领域分类
domains = {
    "weather": "天气查询",
    "transportation": "交通路线",
    "finance": "股票金融",
    "sports": "体育赛事",
    "general": "通用查询"
}
```

**实现机制**:
- **正则表达式**: 检测显式时间限制（如"昨天"、"最近一周"、"上个月"）
- **LLM隐式检测**: 当正则未检测到时间限制时，LLM辅助识别隐含的时间敏感查询
- **上下文增强**: 将当前日期注入查询，提供实时上下文

#### 1.2 API数据源集成
```python
# 领域特定API调用示例
def fetch_domain_data(self, query: str, domain: str) -> Dict:
    if domain == "weather":
        # 调用天气API
        return self._call_weather_api(query)
    elif domain == "finance":
        # 调用金融API (yfinance)
        return self._fetch_stock_data(query)
    elif domain == "transportation":
        # 调用路线规划API
        return self._get_route_info(query)
```

**特性**:
- **智能跳过**: 如果API已能完全回答查询，则跳过搜索流程
- **上下文注入**: 将API数据作为额外上下文传递给搜索流程
- **错误处理**: API调用失败时自动回退到搜索模式

#### 1.3 多模态查询处理
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
```
用户查询
    │
    ▼
[时间约束解析]
    │
    ▼
[小对话检测] ───→ 是 ──→ 直接LLM响应
    │
    否
    ▼
[领域分类与API调用]
    │
    ▼
[是否需要继续搜索?] ──── 否 ──→ 返回API结果
    │
    是
    ▼
[LLM决策引擎] ───→ 不需要搜索 ──→ 直接回答
    │
    需要搜索
    ▼
[关键词生成] ──── 英文/中文双语关键词
    │
    ▼
[搜索管道] (SearchRAG/Hybrid)
    │
    ▼
[重排序与过滤]
    │
    ▼
[LLM生成最终答案]
```

**复杂查询示例**: *"英伟达最新财报对股价的影响"*

**执行步骤**:
1. **时间解析**: 识别"最新" → 注入当前日期
2. **领域分类**: finance (金融)
3. **API预检**: 调用yfinance获取NVDA股票数据
4. **关键词生成**: `["NVIDIA earnings", "NVDA stock price", "财报", "股价", "最新"]`
5. **搜索执行**: 使用关键词进行web搜索
6. **重排序**: 使用Qwen3对结果排序
7. **答案生成**: 结合股票数据和搜索结果生成分析

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
def _ensure_search_rag_pipeline(self, snapshot: Optional[tuple]):
    # 缓存检查: 基于search_client ID和文档快照
    search_signature = (id(self.search_client), snapshot)
    if self._search_rag_pipeline is None or \
       self._search_rag_signature != search_signature:
        # 重建管道
        self._search_rag_pipeline = SearchRAG(...)
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

#### 6.3 温度控制

**任务特定温度**:
```python
temperature_config = {
    "direct_answer": 0.3,
    "search_decision": 0.0,  # 决策需要确定性
    "keyword_generation": 0.2,
    "time_detection": 0.1
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
        {"label": "search_decision", "duration": "200ms"},
        {"label": "keyword_generation", "duration": "150ms"},
        {"label": "final_answer", "duration": "800ms"}
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
- `decision_llm_error`: 决策LLM调用失败
- `decision_parse_error`: JSON解析失败
- `domain_api_error`: 领域API调用失败
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
