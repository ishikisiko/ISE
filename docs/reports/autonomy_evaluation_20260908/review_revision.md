# 自主度评测辅助评审修订记录

本记录不改动正在运行的 ISE 基线、问题、评分维度或核心事实参考。

`glm-5.2` 接入 smoke 可以返回 JSON，但正式评审 v1（未指定 reasoning）出现思考耗尽 4096 输出额度、空回答或非 JSON 回答。原始 v1 评审记录保留在每轮 `reviews/`；其中有效记录也不与修订后结果混合使用。原评审代码保存为研究根目录 `autonomy_review-v1.py`。

provider 对 `thinking=disabled` 返回 HTTP 400，说明该模型为 thinking-only；同一接口使用 `reasoning_effort=low` 返回 HTTP 200、有效文本，预检查使用量 input 20 / output 14 / total 34。这是接口兼容与输出可靠性修正，不根据得分选择裁判。

第二版 `v2-low`：请求模型仍为 `opencode-go/glm-5.2`，仅明确 `reasoning=low`；同一 system prompt、核心事实、开放题四维度、temperature 0、max_tokens 4096、每条最多两次结构化输出尝试。记录放入 `reviews-v2-low/`，对应冻结的 `review-manifest-v2-low.json`。v1 用量作为额外评测开销列出；中断时尚未返回的请求没有完整用量，因此这部分仅有已捕获下界。

第三版 `v3-evidence` 是最终统一评分口径。核对证据视图发现 `evidence_items` 丢掉了 ledger 的 `metadata.eid`，评审因此无法把有效的 `[En]` 标记对应到证据。改为优先读取同一结果中的原始 `evidence_records`，将 `metadata.eid` 显式映射成 `citation_id=En`；证据正文仍最多前 12 项、每字段 1800 字符，不额外联网取证。新增回归测试验证真实编号映射，原 system prompt、模型与上述推理参数全部不变。两轮全量只使用 `reviews-v3-evidence/` 和同一个 `review-manifest-v3-evidence.json`，不混用或择优选取旧评分。v2 源码保存为 `autonomy_review-v2-low.py`，旧评分和已捕获用量保留为额外评测开销；被中断请求同样只能记已知下界。

评分仍为模型辅助，不是真人评分。provider 的错误说明提到 GLM-5.3，而请求 ID 为 glm-5.2；报告只承诺记录了请求模型 ID，不能证明上游权重版本固定。
