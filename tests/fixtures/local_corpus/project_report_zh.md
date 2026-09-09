# NLP 项目：智能搜索引擎系统技术报告

> **状态：迁移前快照，非当前架构。** 本报告描述 agentic loop 迁移（roadmap M0–M5）之前的系统：
> 领域分类器、智能源头选择器、`SmartSearchOrchestrator` 与静态查询计划。这些组件均已删除。
> 当前架构见 [System_Architecture.md](System_Architecture.md) 与
> [docs/agentic_loop_roadmap.md](docs/agentic_loop_roadmap.md)。保留本文作为历史记录，不再更新。

## 1. 项目的整体结构和目的

### 1.1 项目定位
本项目是一个基于检索增强生成（Retrieval-Augmented Generation, RAG）技术的智能搜索引擎系统。它旨在从简单的关键词匹配升级到能够深度理解用户意图、动态选择数据源、编排复杂工作流的智能问答系统。

### 1.2 项目目标
- 提供精确、上下文感知的答案
- 支持多种数据源选择（网络搜索、本地知识库、特定领域API）
- 实现高级重排和过滤算法
- 支持多模态输入（文本、图片、文档）
- 提供域名专业化能力（天气、交通、金融、体育等）
- 实现快速响应和高质量的检索结果

### 1.3 项目来源
这是 ML for NLP 课程的学期项目（Capstone Project），项目代号为 Project ISE（Intelligent Search Engine）。

## 2. 主要模块和它们的功能

### 2.1 项目目录结构
```
NLP_Project/
├── frontend/              # 前端Web界面（HTML/CSS/JS）
├── langchain/             # LangChain集成模块
│   ├── langchain_llm.py       # LLM客户端封装
│   ├── langchain_orchestrator.py # 智能编排器
│   ├── langchain_rag.py       # RAG实现
│   ├── langchain_rerank.py    # 重排序器
│   ├── langchain_support.py   # 辅助功能
│   └── langchain_tools.py     # 工具函数
├── llm/                   # LLM API客户端
│   └── api.py
├── orchestrators/         # 智能编排器
│   └── smart_orchestrator.py
├── rag/                   # RAG实现
│   ├── local_rag.py          # 本地知识库RAG
│   └── search_rag.py         # 搜索RAG
├── search/                # 搜索模块
│   ├── search.py             # 搜索客户端
│   ├── rerank.py             # 重排序
│   ├── source_selector.py    # 智能源选择
│   └── sports_api.py         # 体育API
├── utils/                 # 工具模块
│   ├── timing_utils.py       # 时间记录
│   ├── current_time.py       # 当前时间
│   ├── temperature_config.py # 温度配置
│   ├── time_parser.py        # 时间解析
│   └── query_config.py       # 查询配置
├── tests/                 # 测试模块
├── uploads/               # 上传文件目录
├── logs/                  # 日志目录
├── results/               # 结果目录
├── main.py                # 命令行入口
├── server.py              # Web服务器入口
├── config.example.json    # 配置文件示例
├── config.json            # 实际配置文件（Git忽略）
└── requirements.txt       # Python依赖
```

### 2.2 核心模块功能详解

#### 2.2.1 前端模块 (frontend/)
- 提供Web用户界面
- 支持文件上传
- 支持联网搜索开关、搜索源选择和强制搜索
- 配置参数调整

#### 2.2.2 LangChain集成模块 (langchain/)
- **langchain_llm.py**: 封装多种LLM提供商的接口
- **langchain_orchestrator.py**: 基于LangChain的智能编排器，负责流程控制
- **langchain_rag.py**: RAG实现，支持本地和混合模式
- **langchain_rerank.py**: 重排序功能集成
- **langchain_support.py**: 辅助功能函数
- **langchain_tools.py**: 工具函数集合

#### 2.2.3 智能编排器 (orchestrators/smart_orchestrator.py)
- 智能决策：判断查询是否需要搜索
- 领域分类：自动识别查询领域（天气、金融、体育等）
- 时间约束解析：检测和处理时间敏感查询
- 数据源选择：根据查询类型选择合适的数据源
- 工作流编排：协调各模块执行复杂任务

#### 2.2.4 搜索模块 (search/)
- **search.py**: 支持多种搜索API（Brave Search、Bright Data SERP、Google Custom Search）
- **rerank.py**: 重排序实现（支持Qwen3重排序模型）
- **source_selector.py**: 智能源选择器
- **sports_api.py**: 体育数据API集成

#### 2.2.5 RAG模块 (rag/)
- **local_rag.py**: 本地知识库RAG，支持文档索引和检索
- **search_rag.py**: 搜索RAG，结合网络搜索结果

#### 2.2.6 LLM模块 (llm/api.py)
- 封装多种LLM提供商的API
- 支持OpenAI、Anthropic、Google、智谱AI、MiniMax等
- 提供统一的调用接口

#### 2.2.7 工具模块 (utils/)
- **timing_utils.py**: 性能计时工具
- **temperature_config.py**: 温度参数配置
- **time_parser.py**: 时间解析工具
- **current_time.py**: 当前时间获取

## 3. 关键技术栈和依赖

### 3.1 核心编程语言
- **Python 3.x**: 主要开发语言

### 3.2 大型语言模型 (LLM)
- **OpenCode Go**: 多模型聚合入口 (DeepSeek, GLM, Kimi, Qwen, MiniMax 等)
- **Anthropic**: Claude-3 (Sonnet, Haiku, Opus)
- **智谱AI**: GLM-4.6, GLM-4V, GLM-4.5-air
- **MiniMax**: MiniMax-M2 (支持思考模式)
- **通义千问**: Qwen系列（含重排序模型）
- **OpenRouter**: 多模型聚合平台

### 3.3 后端框架
- **Flask**: 轻量级Web服务器
- **LangChain**: RAG管线编排框架

### 3.4 向量数据库与嵌入
- **FAISS**: 高效相似性搜索库
- **HuggingFace Embeddings**: all-MiniLM-L6-v2等开源嵌入模型
- **Sentence-Transformers**: 文档向量化

### 3.5 搜索与数据源
- **Brave Search**: 默认优先使用的通用网页搜索提供商
- **Bright Data SERP**: Google风格 SERP 的后备搜索提供商
- **Google Custom Search JSON API**: 自定义搜索
- **Yahoo Finance API (yfinance)**: 金融数据接口
- **Finnhub**: 金融数据API
- **API-Sports**: 体育数据API

### 3.6 文档处理
- **PyPDF2/pypdf**: PDF文档解析
- **Unstructured**: 多格式文档处理
- **tiktoken**: 分词工具

### 3.7 其他依赖
- **requests**: HTTP请求库
- **pydantic**: 数据验证
- **PyPDF2**: PDF处理

## 4. 配置文件和重要设置

### 4.1 配置文件结构 (config.json)
主要配置项包括：

#### 4.1.1 LLM提供商配置
```json
{
    "LLM_PROVIDER": "zai",
    "providers": {
        "openai": {
            "api_key": "...",
            "model": "gpt-3.5-turbo",
            "base_url": "..."
        },
        "zai": {
            "api_key": "...",
            "model": "glm-4.6",
            "base_url": "https://open.bigmodel.cn/api/anthropic",
            "available_models": ["glm-4.6", "glm-4.5-air"]
        },
        "minimax": {
            "thinking": {
                "enabled": true,
                "display_in_response": false
            }
        }
    }
}
```

#### 4.1.2 搜索配置
```json
{
    "SERPAPI_API_KEY": "...",
    "GOOGLE_API_KEY": "...",
    "GOOGLE_CX": "...",
    "googleSearch": {
        "lr": "lang_zh-CN|lang_zh-TW|lang_en",
        "safe": "medium",
        "timeout": 15
    }
}
```

#### 4.1.3 重排序配置
```json
{
    "rerank": {
        "enabled": true,
        "min_score": 0.0,
        "max_per_domain": 1,
        "providers": {
            "qwen": {
                "api_key": "...",
                "model": "qwen3-rerank",
                "base_url": "https://dashscope.aliyuncs.com/api/v1/services/rerank",
                "timeout": 15
            }
        }
    }
}
```

#### 4.1.4 领域分类器配置
```json
{
    "domainClassifier": {
        "provider": "zai",
        "model": "glm-4.5-air"
    },
    "routingAndKeywords": {
        "provider": "zai",
        "model": "glm-4.5-air"
    }
}
```

### 4.2 环境变量
- `NLP_CONFIG_PATH`: 配置文件路径
- `PORT`: Web服务器端口
- `FINNHUB_API_KEY`: Finnhub API密钥
- 各LLM提供商的API密钥也可通过环境变量配置

## 5. 测试和部署方式

### 5.1 运行方式

#### 5.1.1 命令行界面 (CLI)
```bash
# 默认统一主链路（开启联网搜索）
python main.py "你的查询"

# 仅使用本地文档
python main.py "你的查询" --search off --data-path ./uploads

# 联网搜索 + 本地文档
python main.py "你的查询" --data-path ./uploads

# 覆盖LLM提供商
python main.py "你的查询" --provider openai

# 其他选项
python main.py "你的查询" \
    --max-tokens 1000 \
    --temperature 0.7 \
    --num-results 10 \
    --disable-rerank \
    --pretty
```

#### 5.1.2 Web界面
```bash
# 启动Web服务器
python server.py
```
然后访问 `http://localhost:8000`，通过联网搜索开关、搜索源选择和文件上传控制运行方式。

### 5.2 测试方式

#### 5.2.1 回归评测
```bash
# 混合路由回归（small talk / domain API / web / local RAG）
env1/bin/python tests/search_quality_pipeline.py collect \
  --queries-file tests/search_quality_minimal_queries.txt \
  --output-file tests/search_quality_regression_run.json \
  --num-results 5 \
  --show-timings

# 搜索质量标注集（强制搜索，便于人工判断检索结果）
env1/bin/python tests/search_quality_pipeline.py collect \
  --queries-file tests/search_quality_minimal_search_queries.txt \
  --output-file tests/search_quality_annotations.json \
  --num-results 5 \
  --force-search \
  --show-timings
```

### 5.3 部署方式

#### 5.3.1 本地部署
1. 克隆项目：`git clone https://github.com/ishikisiko/NLP_Project.git`
2. 安装依赖：`pip install -r requirements.txt`
3. 配置：复制 `config.example.json` 为 `config.json` 并填入API密钥
4. 运行：使用CLI或Web界面

#### 5.3.2 Docker部署（未在仓库中发现）
项目目前未提供Dockerfile或docker-compose.yml，但可以根据需要自行创建。

## 6. 代码组织和架构模式

### 6.1 系统架构
系统采用分层架构设计：

```
用户查询
    │
    ▼
┌─────────────────────────────────┐
│   SmartSearchOrchestrator      │
│   (智能编排控制器)              │
│  - 时间约束解析                 │
│  - 领域分类器                   │
│  - 智能源头选择器               │
└─────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────┐
│      决策引擎                   │
│   - 是否需要搜索？               │
└─────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────┐
│     管道选择层                  │
│  ┌──────────┐ ┌──────────────┐ │
│  │ LocalRAG │ │  SearchRAG   │ │
│  └──────────┘ └──────────────┘ │
│  ┌───────────────────────────┐ │
│  │  SearchRAG (Hybrid)       │ │
│  └───────────────────────────┘ │
└─────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────┐
│      重排序器                   │
│   (Qwen3 Rerank)               │
└─────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────┐
│      LLM生成层                  │
│  (多种LLM提供商支持)            │
└─────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────┐
│      响应结果                   │
└─────────────────────────────────┘
```

### 6.2 架构模式
1. **模块化设计**: 功能分离，各模块职责明确
2. **插件化架构**: 支持多种LLM提供商、搜索API和重排序模型
3. **编排器模式**: 由中央编排器协调各模块工作
4. **缓存机制**: 对文档索引和管道进行缓存以提高性能
5. **容错设计**: 多层回退机制（API失败→搜索模式→本地RAG→直接LLM回答）

### 6.3 核心流程
1. **查询接收**: 接收用户文本或多模态输入
2. **智能决策**: 分析查询，决定处理策略
3. **数据源选择**: 根据查询类型选择合适的数据源
4. **信息检索**: 从选中的数据源检索相关信息
5. **重排序**: 对检索结果进行重排序
6. **答案生成**: 使用LLM生成最终答案
7. **响应返回**: 返回答案及相关元数据

## 7. 项目亮点与特色功能

### 7.1 智能源头选择
- 自动识别查询领域（天气、金融、体育、交通等）
- 根据领域选择特定API或搜索策略
- 时间约束解析，自动注入当前日期上下文

### 7.2 多种LLM提供商支持
- 统一的接口封装多种LLM提供商
- 支持为不同任务（领域分类、关键词生成、直接回答）配置不同模型
- MiniMax M2模型支持思考模式

### 7.3 高级重排序
- 集成Qwen3重排序模型
- 支持配置最小分数阈值和域名去重

### 7.4 本地知识库
- 支持PDF、Markdown、纯文本等多种格式
- 使用FAISS向量数据库进行高效检索
- 分块策略：chunk_size=1000, chunk_overlap=200

### 7.5 多模态支持
- 支持图像输入（通过Google Vision API）
- 支持视觉模型（GPT-4V、Claude-3、Gemini等）
- 即使在非视觉模型上，也能通过图像元数据增强上下文

### 7.6 性能优化
- 文档快照缓存机制
- 超时和重试策略
- 任务特定温度配置

### 7.7 可观测性
- 详细的响应时间记录
- 各模块性能指标追踪
- 错误处理和回退机制

## 8. 项目发展历程

从Git历史可以看到项目的关键发展节点：
- 实现了基于LangChain的智能编排器
- 增强了金融相关查询处理能力
- 将时间相关查询逻辑提取到配置文件
- 添加了系统架构文档
- 移除了MCP Web搜索支持及相关代码

## 9. 总结与展望

本项目成功实现了一个功能完整的智能搜索引擎系统，具有以下优势：
1. 模块化架构，易于扩展和维护
2. 支持多种LLM和数据源
3. 智能决策和工作流编排
4. 丰富的配置选项
5. 提供CLI和Web两种界面

未来可进一步优化的方向：
- 添加Docker支持，简化部署
- 完善测试覆盖
- 增加更多领域API集成
- 优化性能和响应时间
- 实现更复杂的多步推理工作流
