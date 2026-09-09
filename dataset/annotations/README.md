# dataset/annotations/

人工标注与勘误的版本化位置（计划 §0.3）。文件不放 `tests/`。

- `gold_errata.json` — gold 勘误（先于看答案固定；原 CSV 不改写）。
- `answer_template.csv` / `loop_template.csv` — 答案标注与循环判定抽样的列模板，复制为 `answer_<date>.csv` / `loop_<date>.csv`。
- `search_<date>.json` — 由 `search_quality_pipeline.py collect` 产出并填好 `judgment` 的文件。

标注判据见 [docs/guides/quality_annotation_guide.md](../../docs/guides/quality_annotation_guide.md)。
