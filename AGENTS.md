# Repository Guidelines
Project should run in conda environment "env1"

## Project Structure & Module Organization

The project is organized into the following directories:

```
NLP_Project/
├── main.py                  # CLI 入口，编排器构建
├── server.py                # Flask Web API 服务器
├── llm/                     # LLM 客户端模块
│   └── api.py              # LLMClient, HKGAIClient
├── search/                  # 搜索相关模块
│   ├── search.py           # 搜索客户端 (Brave, Bright Data, Google)
│   ├── rerank.py           # 重排序器 (Qwen3Reranker)
│   ├── source_selector.py  # 智能源选择器
│   └── sports_api.py       # 体育 API 客户端
├── rag/                     # RAG 管道模块
│   ├── local_rag.py        # 本地文档 RAG
│   └── search_rag.py       # 搜索增强 RAG
├── orchestrators/           # 编排器模块
│   └── smart_orchestrator.py  # 智能搜索编排器
├── langchain/               # LangChain 集成模块
│   ├── langchain_llm.py    # LangChain LLM 适配器
│   ├── langchain_orchestrator.py  # LangChain 编排器
│   ├── langchain_rag.py    # LangChain RAG 管道
│   ├── langchain_rerank.py # LangChain 重排序
│   ├── langchain_support.py # 文档加载和向量存储
│   └── langchain_tools.py  # LangChain 搜索工具
├── utils/                   # 工具类模块
│   ├── time_parser.py      # 时间约束解析器
│   ├── timing_utils.py     # 性能计时工具
│   └── current_time.py     # 当前时间工具
├── tests/                   # pytest 用例与搜索质量评测脚本
│   ├── test_*.py
│   └── search_quality_pipeline.py
├── frontend/                # 前端静态文件
│   ├── index.html
│   ├── script.js
│   └── styles.css
├── uploads/                 # 上传文档目录
├── config.json              # 配置文件
└── requirements.txt         # Python 依赖
```

The default runtime path is the LangChain-based orchestrator assembled by `main.py` and `server.py`, with legacy compatibility code still available behind explicit switches. Search clients live in `search/search.py`, the default orchestration and RAG execution live under `langchain/`, and persisted uploads land in `uploads/`; clean this directory when rotating documents. Configuration secrets belong in `config.json` (copy from `config.example.json`) or an alternate path exposed via `NLP_CONFIG_PATH`.

## Build, Test, and Development Commands
Install dependencies once per virtual environment: `pip install -r requirements.txt`. Launch the web app with `python server.py` and visit `http://localhost:8000`. The CLI entrypoint stays available through `python main.py "Your question"`. Use `python main.py "Your question" --search off --data-path ./uploads` for local-doc-only retrieval, or keep search enabled and pass `--data-path` to combine uploaded/local docs with live search. Use `--provider` to override the default LLM for a single run. When debugging configuration, set `NLP_CONFIG_PATH=/full/path/config.json` before invoking either entrypoint.

## Coding Style & Naming Conventions
Follow PEP 8 with four-space indentation and descriptive, snake_case names for Python modules, functions, and variables. Preserve type hints and concise docstrings where they already exist, and prefer early validation with informative errors. JavaScript in `frontend/script.js` is modern ES2015; keep functions arrow-based, use camelCase identifiers, and avoid introducing frameworks without discussion. Environment keys remain UPPER_SNAKE_CASE to match the existing config schema.

## Testing Guidelines
Automated pytest coverage is committed; validate changes by exercising both the CLI and Flask routes when behavior shifts. Run `python main.py "sanity check" --pretty` to inspect raw JSON, use `python main.py "local sanity" --search off --data-path ./uploads` for local-doc-only checks, and submit sample questions through the UI to confirm both search-enabled and search-disabled flows. When contributing test coverage, add tests to the `tests/` package using `pytest`, update `requirements.txt`, and ensure new tests can be invoked with `python -m pytest`. Document any manual validation steps in the pull request when the automated suite does not fully cover the change.

## Commit & Pull Request Guidelines
Keep commit subjects short, imperative, and aligned with the existing history (for example, "Add simple web UI and API server"). Describe noteworthy implementation details and configuration impacts in the body. Pull requests should summarize intent, call out configuration changes (`config.json`, env vars), list manual or automated tests run, and attach UI screenshots when frontend behavior shifts. Link to tracking issues or tasks where applicable so reviewers understand context rapidly.
