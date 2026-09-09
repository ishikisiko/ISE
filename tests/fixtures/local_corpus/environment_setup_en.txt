# Environment Setup

This project is expected to run in an environment named `env1`.

## Fixed Environment

The reproducible environment definition lives in [environment.yml](/root/code/NLP_Project/environment.yml).
The exact tested Python package snapshot lives in [requirements-lock.txt](/root/code/NLP_Project/requirements-lock.txt).

Recommended setup:

```bash
conda env create -f environment.yml
conda activate env1
```

If you need to match the tested Python package set exactly after activation:

```bash
pip install -r requirements-lock.txt
```

If `env1` already exists and you want to refresh it:

```bash
conda env update -f environment.yml --prune
conda activate env1
```

## Dependencies

The Python package list is maintained in [requirements.txt](/root/code/NLP_Project/requirements.txt).
Use [requirements-lock.txt](/root/code/NLP_Project/requirements-lock.txt) when you need the exact tested package versions instead of the looser runtime spec.

Important runtime packages:

- `langchain-huggingface` is required for local embedding support via `HuggingFaceEmbeddings`
- `faiss-cpu` is required for local vector search
- `pytest` is required for the committed test suite

## Run Commands

After activating `env1`:

```bash
python server.py
```

```bash
python main.py "sanity check" --pretty
```

```bash
python main.py "your query" --search off --data-path ./uploads
```

```bash
python main.py "your query" --data-path ./uploads
```

```bash
NLP_CONFIG_PATH=/full/path/config.json python main.py "your query"
```

## Test Commands

Run the automated tests from the activated `env1` environment:

```bash
env1/bin/pytest -q
```

If you want a more targeted run:

```bash
env1/bin/pytest tests/test_search_provider_migration.py -q
```

## Notes

- The project imports LangChain-based RAG modules at startup, so missing `langchain-huggingface` can break even search-oriented entrypoints during import.
- Runtime data such as Brave quota logs is written under `runtime/`.
- Secrets should stay in `config.json` or be provided through `NLP_CONFIG_PATH`.
- Use `--search off --data-path ./uploads` for local-doc-only runs; keep search enabled and pass `--data-path` to combine web/domain evidence with local docs.
