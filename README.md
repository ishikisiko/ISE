# ISE

This project demonstrates a simple Retrieval-Augmented Generation (RAG) pipeline.

## Current Runtime Path

- The default production path is `LangChainOrchestrator -> ReactAgentOrchestrator -> ReactLoopGraphRunner`.
- The internal retrieval layer now uses a unified evidence model across `web`, `local`, and `domain` sources.
- `search_hits` and `retrieved_docs` are still returned for compatibility, but they are now projected from the internal unified evidence set instead of being maintained as separate internal truth sources.
- Every non-visual, non-small-talk query uses the same ReAct loop; there is no static-plan or fallback executor.

### ReAct Loop

The ReAct executor has one implementation: the explicit LangGraph
`act -> observe -> evaluate` state machine. Every candidate answer calls the
same deterministic termination critic. `termination` is the
single budget/judge/threshold block (`max_iterations`, `judge_interval`,
`repeat_threshold`, `no_progress_threshold`, `tool_error_threshold`,
`new_evidence_min_ratio`, per-tool budgets, and `judge`). A positive judge cannot clear
deterministic evidence gaps or extend a budget; a negative judge may veto an
otherwise complete candidate. A missing LangGraph dependency fails closed
instead of selecting a second stopping implementation.

## Unified Evidence Layer

The default LangChain pipeline now normalizes first-class evidence into a shared model:

- `EvidenceSource`: retrieval interface for `web`, `local`, and `domain`
- `EvidenceItem`: normalized record containing content, reference, source identity, and metadata
- Unified fusion: retrieval, normalization, deduplication, ranking, and answer-context construction happen before answer generation

This is the loop's only evidence path:

- `local_docs` now reuses the unified local evidence source instead of calling legacy `LocalRAG` directly
- `search_recovery` reuses the same fusion pipeline as the default path
- registry skill evidence enters the same ledger as web and local evidence

## Response Metadata

In addition to legacy compatibility fields, responses may now include:

- `evidence_items`: normalized evidence records used internally by the pipeline
- `evidence_summary`: short text summary of the fused evidence set
- `evidence_sources_active`: first-class sources enabled for the run
- `evidence_sources_used`: first-class sources that actually contributed evidence
- `evidence_source_types_active` / `evidence_source_types_used`: source-type summaries for observability and tool-recovery reuse

## LangChain integration

- Local and hybrid RAG now run on LangChain primitives (FAISS + configurable embeddings) for chunking and retrieval.
- The default embedding configuration can point to an OpenAI-compatible API, while local HuggingFace embeddings remain available as a fallback option.
- Install the refreshed dependencies (`langchain`, `langchain-community`, `faiss-cpu`, `langchain-huggingface`, `langchain-openai`) via `pip install -r requirements.txt`.

## Installation

1.  Clone the repository:
    ```bash
    git clone https://github.com/ishikisiko/NLP_Project.git
    cd NLP_Project
    ```

2.  Create and activate the fixed `env1` environment:
    ```bash
    conda env create -f environment.yml
    conda activate env1
    ```

3.  Install or refresh the required Python dependencies inside `env1`:
    ```bash
    pip install -r requirements.txt
    ```

    If you want the exact tested dependency set instead of the looser runtime spec:
    ```bash
    pip install -r requirements-lock.txt
    ```

See [ENVIRONMENT.md](/root/code/NLP_Project/ENVIRONMENT.md) for the complete run and test environment guide.

## Configuration

1.  Create a `config.json` file in the root of the project.

    You can also keep the file elsewhere and point both CLI and server runs at it with `NLP_CONFIG_PATH=/full/path/config.json`.

2.  Configure your preferred LLM provider and add your API keys:
    ```json
    {
        "LLM_PROVIDER": "glm",
        "RERANK_PROVIDER": "qwen3-rerank",
        "brightDataSearch": {
            "api_token": "YOUR_BRIGHTDATA_API_TOKEN_HERE",
            "zone": "serp_api1",
            "base_url": "https://api.brightdata.com/request"
        },
        "braveSearch": {
            "primary_api_key": "YOUR_BRAVE_PRIMARY_API_KEY_HERE",
            "secondary_api_key": "YOUR_BRAVE_SECONDARY_API_KEY_HERE",
            "base_url": "https://api.search.brave.com/res/v1/web/search",
            "rps": 1,
            "monthly_limit": 2000,
            "usage_log_path": "runtime/brave_search_usage.jsonl"
        },
        "providers": {
            "openai": {
                "api_key": "YOUR_OPENAI_API_KEY_HERE",
                "model": "gpt-3.5-turbo",
                "base_url": "https://api.openai.com/v1"
            },
            "anthropic": {
                "api_key": "YOUR_ANTHROPIC_API_KEY_HERE",
                "model": "claude-3-sonnet-20240229",
                "base_url": "https://api.anthropic.com/v1"
            },
            "google": {
                "api_key": "YOUR_GOOGLE_API_KEY_HERE",
                "model": "gemini-pro",
                "base_url": "https://generativelanguage.googleapis.com/v1beta"
            },
            "hkgai": {
                "api_key": "YOUR_HKGAI_API_KEY_HERE",
                "model": "HKGAI-V1",
                "base_url": "https://oneapi.hkgai.net/v1"
            },
            "glm": {
                "api_key": "YOUR_GLM_API_KEY_HERE",
                "model": "glm-4.6",
                "base_url": "https://open.bigmodel.cn/api/anthropic"
            Notes:
            - `minimax` provider now points to Minimax's Anthropic-compatible endpoint at `https://api.minimax.io/anthropic` and the example model is `MiniMax-M2`.
            - `zai` provider uses Anthropic-compatible URL `https://open.bigmodel.cn/api/anthropic` by default. The application detects Anthropic-compatible base_urls (containing `/anthropic`) and uses Anthropic-style headers and message endpoints.

            }
        },
        "rerank": {
            "min_score": 0.0,
            "max_per_domain": 1,
            "providers": {
                "qwen": {
                    "api_key": "YOUR_DASHSCOPE_API_KEY_HERE",
                    "model": "qwen3-rerank",
                    "base_url": "https://dashscope.aliyuncs.com/api/v1/services/rerank",
                    "timeout": 15
                }
            }
        }
    }
    ```

### Supported LLM Providers

- **OpenAI**: Set `LLM_PROVIDER` to `"openai"` and provide your OpenAI API key
- **Anthropic Claude**: Set `LLM_PROVIDER` to `"anthropic"` and provide your Anthropic API key  
- **Google Gemini**: Set `LLM_PROVIDER` to `"google"` and provide your Google API key
- **GLM (智谱AI)**: Set `LLM_PROVIDER` to `"glm"` and provide your GLM API key. Supports GLM-4.6 and other models. (Default)
- **HKGAI**: Set `LLM_PROVIDER` to `"hkgai"` and provide your HKGAI API key
- **MiniMax**: Set `LLM_PROVIDER` to `"minimax"` and provide your MiniMax API key. Supports MiniMax-M2 model with thinking mode.

#### MiniMax Thinking Mode

MiniMax M2 model supports a **thinking mode** that allows the model to show its reasoning process. You can configure this in `config.json`:

```json
"minimax": {
    "api_key": "YOUR_MINIMAX_API_KEY_HERE",
    "model": "MiniMax-M2",
    "base_url": "https://api.minimaxi.com/anthropic/v1",
    "thinking": {
        "enabled": true,
        "display_in_response": false
    }
}
```

- `thinking.enabled`: (boolean) Enable/disable the thinking mode. When `true`, the model will generate reasoning process internally.
- `thinking.display_in_response`: (boolean) When `true`, the thinking process will be included in the response text prefixed with `[思考过程]`. When `false`, only the final answer is returned.

**Note**: Thinking mode is only available for MiniMax M2 model via the Anthropic-compatible API endpoint.

### Required Configuration

- `LLM_PROVIDER`: Choose your preferred LLM provider (`openai`, `anthropic`, `google`, `glm`, `hkgai`)
- `brightDataSearch.api_token` and `brightDataSearch.zone`: Bright Data SERP credentials for the Bright Data search provider
- `braveSearch.primary_api_key`: Primary Brave Search key for default general web search
- `braveSearch.secondary_api_key`: Optional fallback Brave Search key
- `braveSearch.rps`: Request-per-second cap for Brave Search. This project expects `1`.
- `braveSearch.usage_log_path`: Backend JSONL log used to track Brave quota consumption across restarts
- Provider-specific API keys in the `providers` section
- `RERANK_PROVIDER`: Optional reranking backend (`qwen3-rerank`). Provide the corresponding credentials under `rerank.providers`

### Search Providers

- **Brave Search**: Default first-choice provider for general web search. The backend records Brave requests to the configured JSONL log so monthly quota usage can be audited.
- **Bright Data SERP**: Google-style fallback search provider implemented through Bright Data's request API.
- **Google Custom Search**: Optional additional general web search provider that can be used when configured.

## Usage

### Web Interface

Run the web server:
```bash
python server.py
```

Then open your browser to `http://localhost:8000`.

The web interface allows you to:
-   Toggle live search on or off.
-   Pick active search providers and optionally force a search pass.
-   Upload local files that can be used either with search disabled or alongside live search.
-   Configure the LLM provider and other parameters.

### Command-Line Interface

#### Default Query Path

Run the main script with your query (uses GLM by default):
```bash
python main.py "your query here"
```

#### Local Documents Only

Disable live search and point the pipeline at your local document directory:
```bash
python main.py "your query here" --search off --data-path ./uploads
```

#### Search + Local Documents

Keep search enabled and pass a local document directory so the pipeline can use both:
```bash
python main.py "your query here" --data-path ./uploads
```

### Override LLM Provider

You can temporarily override the LLM provider using the `--provider` flag:
```bash
# Use OpenAI
python main.py "your query here" --provider openai

# Use Anthropic Claude
python main.py "your query here" --provider anthropic

# Use Google Gemini
python main.py "your query here" --provider google

# Use HKGAI
python main.py "your query here" --provider hkgai

# Use GLM (智谱AI)
python main.py "your query here" --provider glm
```

### Additional Options

```bash
python main.py "your query here" \
    --max-tokens 1000 \
    --temperature 0.7 \
    --num-results 10 \
    --disable-rerank \
    --pretty
```

- `--max-tokens`: Maximum number of tokens for the LLM response
- `--temperature`: Sampling temperature (0.0-1.0)
- `--num-results`: Number of search results to include
- `--pretty`: Pretty print the JSON response
- `--disable-rerank`: Skip reranking even if configured
- `--search off`: Disable live web/domain search and rely on local documents only

Both `python main.py ...` and `python server.py` honor `NLP_CONFIG_PATH=/full/path/config.json` when you want to run against a non-default config file.

### Multi-turn Conversation (Follow-up Refinement)

The agent supports multi-turn refinement: after an answer is delivered, you can ask it to adjust ("精简一点", "展开第二节", "那竞争对手呢") and it resumes from the previous turn's state (answer, evidence pool, and tool history) rather than starting from scratch.

In the web UI this is automatic — every turn carries a `conversation_id`, and the **新会话** pill starts a fresh conversation. Conversations are persisted under `checkpoints/conversations.sqlite` (configurable via the `conversation` block in `config.json`); delete that directory to reset all sessions.

From the CLI, omit `--conversation-id` to start a new conversation (the generated id is printed for reuse), then pass it back to continue:

```bash
# First turn prints a [conversation_id]; reuse it to refine the previous answer
python main.py "苹果和微软的区别"
python main.py "精简一点，只要三个要点" --conversation-id <printed-id>
```

Set `conversation.enabled=false` in `config.json` to fall back to stateless single-turn behaviour.

### Durable Server Logs

Enable both blocks below when a web deployment must retain every observable
answer request and process message. A value of `0` disables the audit record
size and file-count limits.

```json
{
  "audit": {
    "enabled": true,
    "dir": "runtime/audit",
    "include_answer": true,
    "include_full_result": true,
    "max_files": 0,
    "max_bytes_per_record": 0
  },
  "server_logging": {
    "enabled": true,
    "dir": "runtime/server",
    "capture_stdio": true,
    "include_request_payload": true,
    "include_response_payload": true
  }
}
```

`runtime/server/` contains `server.log`, `stdout.log`, `stderr.log`,
`access.jsonl`, and a complete event stream for each answer request under
`requests/<request-id>.jsonl`. Each request stream records the received
payload, normalized context, every workflow event, final response, and error
tracebacks. `runtime/audit/<conversation-id>.jsonl` retains the final
per-conversation record. Both directories are gitignored. Credential-like
fields and URL query strings are redacted before JSONL records are written.

## Search Quality Evaluation

You can evaluate retrieval quality in two steps:

The repository includes:
- `tests/search_quality_minimal_dataset.csv`: 20-query grouped starter dataset
- `tests/search_quality_minimal_queries.txt`: full query list in dataset order
- `tests/search_quality_minimal_search_queries.txt`: search-oriented subset for `collect`
- `tests/search_quality_local_chunk_template.csv`: local-RAG chunk annotation template

You can inspect a single category or a single sample directly:

```bash
env1/bin/python tests/search_quality_pipeline.py dataset --list-categories
env1/bin/python tests/search_quality_pipeline.py dataset --category local_rag
env1/bin/python tests/search_quality_pipeline.py dataset --query-id Q018
env1/bin/python tests/search_quality_pipeline.py dataset --category web_search_fulltext --queries-only
```

You can also merge layered CSV benchmarks under `dataset/` into one unified file:

```bash
env1/bin/python tests/search_quality_pipeline.py map-external \
  --dataset-dir dataset \
  --output-file tests/search_quality_external_merged.csv \
  --queries-output-file tests/search_quality_external_search_queries.txt
```

1. Collect top search results for a query set:
   ```bash
   env1/bin/python tests/search_quality_pipeline.py collect \
     --queries-file tests/search_quality_minimal_search_queries.txt \
     --output-file tests/search_quality_annotations.json \
     --num-results 5 \
     --force-search
   ```

2. Open the generated JSON and fill the `judgment` field for each query.

Detailed mode:
- Set `annotation_complete` to `true`
- Keep `judgment_mode` as `"detailed"`
- Fill `relevant_ranks` and/or `relevant_urls`

Lightweight mode:
- Set `annotation_complete` to `true`
- Set `judgment_mode` to `"top3_only"`
- Fill `top3_has_answer_evidence`

Then compute metrics:

```bash
env1/bin/python tests/search_quality_pipeline.py evaluate \
  --annotations-file tests/search_quality_annotations.json \
  --output-file tests/search_quality_report.json
```

The report includes:
- `route_correct`
- `fulltext_decision_correct`
- `Hit@3`
- `Hit@5`
- `chunk_hit_at_5`
- `MRR`
- `avg_unique_useful_results`
- `answer_correctness`
- `answer_completeness`
- `answer_groundedness`
- `abstention_quality`

### Quick Regression Runs

Use the same `search_quality_pipeline.py` entrypoint for mixed-route regression and search-only judging.

Mixed-route regression across small talk, domain APIs, web search, and local RAG:

```bash
env1/bin/python tests/search_quality_pipeline.py collect \
  --queries-file tests/search_quality_minimal_queries.txt \
  --output-file tests/search_quality_regression_run.json \
  --num-results 5 \
  --show-timings
```

Search-focused judging set with forced retrieval:

```bash
env1/bin/python tests/search_quality_pipeline.py collect \
  --queries-file tests/search_quality_minimal_search_queries.txt \
  --output-file tests/search_quality_annotations.json \
  --num-results 5 \
  --force-search \
  --show-timings
```

For larger web-heavy regression, reuse the merged external query list:

```bash
env1/bin/python tests/search_quality_pipeline.py collect \
  --queries-file tests/search_quality_external_search_queries.txt \
  --output-file tests/search_quality_external_run.json \
  --num-results 5 \
  --force-search
```

## Testing

Run the automated tests from the activated `env1` environment:

```bash
env1/bin/pytest -q
```
