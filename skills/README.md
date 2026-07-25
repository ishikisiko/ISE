# Skills

Skill 包在 **M2** 才正式建立（契约、registry、handler、preflight），见
[docs/agentic_loop_roadmap.md](../docs/agentic_loop_roadmap.md)。

目前这里只有 M0 抢救下来的 eval 用例——`harden-finance-domain-routing` 中被回退的分类知识，
以数据形态先落到目的地，避免随代码回退一起丢失。M2 建 finance skill 时补齐
`skill.yaml` / `SKILL.md` / `handler.py`，并把这些用例接入 pytest。

在 M2 之前，这些 `.jsonl` 不被任何代码消费。
