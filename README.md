# ISE

Intelligent Search Engine — a ReAct agentic search assistant with a unified
evidence layer across **web**, **local**, and **domain** sources.

- **Web UI**: `python server.py` → http://localhost:8000
- **CLI**: `python main.py "your query"`

---

## Architecture

The default runtime path is:

```
LangChainOrchestrator → ReactAgentOrchestrator → ReactLoopGraphRunner
```

Every non-visual, non-small-talk query runs the **same** ReAct loop — there is
no static-plan or fallback executor. Retrieval is normalized into a single
unified evidence model; `search_hits` / `retrieved_docs` are kept only as
compatibility projections of that internal evidence set.

### The Agent Loop

A LangGraph state machine with three nodes cycling `act → observe → evaluate`
(`orchestrators/react_loop_graph.py`):

```
                      ┌────── entry ──────┐
                      │ reset 工具预算      │
                      │ 过滤 active_tools   │
                      │ (allow_search=False │
                      │  时摘掉 web_search/ │
                      │  fetch_url/         │
                      │  search_recovery)   │
                      └─────────┬──────────┘
                                ▼
      ┌────────────────►   act (LLM 决策)   ◄────────────────┐
      │                     │                                  │
      │            tool_calls? ───no────►──────────┐          │
      │                   │ yes                     │          │
      │                   ▼                         ▼          │
      │                observe (执行工具)                       │
      │     web_search / fetch_url / search_recovery            │
      │     / skill / local_docs (各自 max_calls 预算)           │
      │                   │                                    │
      │      观测→evidence_pool   证据→evidence_records           │
      │                   ▼                                    │
      │                evaluate (统一 critic + 可选 judge LLM)    │
      │                   │                                     │
      │       termination_reason? ──no──►─────────────────────┘
      │                            yes
      └──────── (continue)          ▼
                                    END
              succeeded / exhausted / stagnated /
              evidence_insufficient / unrecoverable
```

**`act`** — `_act` (`react_loop_graph.py:609`)
The LLM is invoked with all tools bound (`TOOL_CALLING_SYSTEM_PROMPT` +
history). It either emits `tool_calls` (→ `observe`) or a text answer
(→ `evaluate`). Invalid tool markup and narration-only responses are caught
and bounced back with a retry hint.

**`observe`** — `_observe` (`react_loop_graph.py:~686`)
Executes each tool call. Each tool enforces its **own** `max_calls_per_query`
budget — once exhausted it returns a structured `budget_exhausted` result and
the underlying API is not called again. Two channels are produced: the tool
output text is fed back to the model as the observation, and
`get_last_evidence_records()` is merged into `evidence_records` so the critic
sees the same evidence. Streaks (`no_progress`, `tool_error`) and the
new-evidence ratio are updated here.

**`evaluate`** — `_evaluate` (`react_loop_graph.py:924`)
The unified deterministic critic checks constraint coverage against the
initial checklist, evidence sufficiency, budgets, and streaks. An optional
semantic judge LLM runs every `judge_interval` rounds — a positive judge
**cannot** clear deterministic evidence gaps or extend a budget; a negative
judge may veto an otherwise complete candidate. A `termination_reason` routes
to `END`; otherwise the loop returns to `act`.

### Termination

| reason | trigger |
| --- | --- |
| `succeeded` | critic approves the candidate |
| `exhausted` | `iteration ≥ max_iterations` |
| `stagnated` | `no_progress_streak` or repeat threshold hit |
| `unrecoverable` | `tool_error_streak` threshold hit |
| `evidence_insufficient` | RETURN_INSUFFICIENT |
| `clarification_required` | CLARIFY |

### Tool budgets

Independent per-tool budgets, reset per query (`config.json → termination.tool_budgets`):

| tool | budget | purpose |
| --- | --- | --- |
| `web_search` | 6 | snippet search (Brave / Bright Data / Google) |
| `fetch_url` | 3 | **full-page fetch** of a URL the agent already found (official docs, API refs) — uses the zero-key `DirectFetchClient` first, then Firecrawl/Tavily/Parallel extractors as fallback |
| `search_recovery` | 3 | high-level RAG-chain recovery |
| skill tools | 2–3 each | finance / weather / sports / places / routes |
| `local_docs` | 2 | local document retrieval |

Global ceiling: `max_iterations = 8`.

> **Why `fetch_url` exists:** snippet-only search gets stuck on API-documentation
> questions (the critic keeps flagging `semantic_sufficiency` /
> `unsupported_specific_detail`). After `web_search` surfaces an official-docs
> URL, the agent calls `fetch_url` to read the full page — the text enters both
> the model context and the evidence pool, so the next evaluation can pass on
> real documentation rather than shallow snippets.

### Deeper references

- [System_Architecture.md](System_Architecture.md) — full mermaid architecture diagrams
- [docs/query_execution_paths.md](docs/query_execution_paths.md) — contract boundary & path map
- [docs/agentic_loop_roadmap.md](docs/agentic_loop_roadmap.md) — loop evolution & target architecture

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/ishikisiko/NLP_Project.git
   cd NLP_Project
   ```

2. Create and activate the fixed `env1` environment:
   ```bash
   conda env create -f environment.yml
   conda activate env1
   ```

3. Install the Python dependencies:
   ```bash
   pip install -r requirements.txt          # loose runtime spec
   # or, for the exact tested set:
   pip install -r requirements-lock.txt
   ```

See [ENVIRONMENT.md](ENVIRONMENT.md) for the complete run/test environment guide.

## Configuration

1. Copy the example config and edit it (the runtime reads `config.json`, or
   `NLP_CONFIG_PATH` if set):
   ```bash
   cp config.example.json config.json
   ```

2. Set `LLM_PROVIDER` and add your API keys. The full, up-to-date schema —
   every search provider, the `termination` budget/judge block, rerank,
   conversation, audit, embeddings, and the MiniMax thinking-mode sub-block —
   lives in **[config.example.json](config.example.json)** (annotated). Prefer
   editing that file over transcribing snippets from this README.

### Supported LLM providers

- **OpenAI** — `LLM_PROVIDER: "openai"`
- **Anthropic Claude** — `LLM_PROVIDER: "anthropic"`
- **Google Gemini** — `LLM_PROVIDER: "google"`
- **GLM (智谱AI)** — `LLM_PROVIDER: "glm"` (default), GLM-4.6
- **HKGAI** — `LLM_PROVIDER: "hkgai"`
- **MiniMax** — `LLM_PROVIDER: "minimax"`, MiniMax-M2 with optional
  [thinking mode](config.example.json) (`thinking.enabled` /
  `thinking.display_in_response`)

### Required keys

- `LLM_PROVIDER` + the matching `providers.<name>.api_key`
- `braveSearch.primary_api_key` (default web search), optional `secondary_api_key`
- `brightDataSearch.api_token` + `.zone` (Google-SERP fallback)
- `RERANK_PROVIDER` + `rerank.providers.<name>` (optional reranking)

### Search providers

- **Brave Search** — default first choice; requests are logged to
  `runtime/brave_search_usage.jsonl` for monthly quota auditing.
- **Bright Data SERP** — Google-style fallback via Bright Data's request API.
- **Google Custom Search** — optional additional provider.

## Usage

### Web interface

```bash
python server.py     # http://localhost:8000
```

Toggle live search, pick active search providers, upload local files, and
configure the LLM provider — all from the UI.

### Command-line interface

```bash
# default (GLM)
python main.py "your query here"

# local documents only
python main.py "your query here" --search off --data-path ./uploads

# search + local documents
python main.py "your query here" --data-path ./uploads

# override the LLM provider for one run
python main.py "your query here" --provider openai
```

Common flags: `--max-tokens`, `--temperature`, `--num-results`, `--disable-rerank`,
`--pretty` (pretty-print JSON), `--search off`.

Both entrypoints honor `NLP_CONFIG_PATH=/full/path/config.json`.

### Multi-turn conversation

The agent resumes from the previous turn's state (answer, evidence pool, tool
history) rather than starting from scratch. In the web UI this is automatic —
every turn carries a `conversation_id`, and the **新会话** pill starts a fresh
conversation.

From the CLI, omit `--conversation-id` to start a new conversation (the
generated id is printed for reuse):

```bash
python main.py "苹果和微软的区别"
python main.py "精简一点，只要三个要点" --conversation-id <printed-id>
```

Conversations persist under `checkpoints/conversations.sqlite` (see the
`conversation` block in `config.json`; set `conversation.enabled=false` for
stateless single-turn behaviour).

## Observability

Durable server logs and audit trails are configurable via the `audit` and
`server_logging` blocks — see **[docs/server_logging.md](docs/server_logging.md)**
for the schema and the `runtime/` file layout.

## Evaluation

Retrieval-quality metrics (Hit@k, MRR, answer groundedness, …) are computed
via `tests/search_quality_pipeline.py` — see
**[docs/search_quality_evaluation.md](docs/search_quality_evaluation.md)** for
the collect → annotate → evaluate workflow and regression scripts.

## Testing

```bash
env1/bin/pytest -q
```
