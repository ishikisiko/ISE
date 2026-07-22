# query-postcheck-fallback Specification

## Purpose
Define the default pipeline post-check stage and the rules for escalating to ReAct fallback with compatible evidence context and fallback metadata.
## Requirements
### Requirement: Default pipeline SHALL run post-check before returning search answers
系统 SHALL 在统一默认搜索主链路生成首答后执行统一的 post-check 阶段，再决定是直接返回首答还是升级到补救路径。

#### Scenario: Unified default pipeline search answer passes post-check
- **WHEN** 统一默认搜索主链路生成首答且 post-check 判定该回答满足需求
- **THEN** 系统 SHALL 直接返回首答
- **AND** 返回结果 SHALL 记录 post-check verdict 和最终执行路径为默认主链路

#### Scenario: Non-search paths can skip post-check
- **WHEN** 查询走 small talk、直接回答、纯领域 API 直出或其他未进入统一搜索主链路的路径
- **THEN** 系统 SHALL 支持跳过或短路 post-check
- **AND** 返回结果 SHALL 明确记录未执行完整 post-check 的原因

### Requirement: Post-check SHALL use rule-based screening before LLM judging
系统 SHALL 先使用规则筛选识别明显失败或高风险回答，再决定是否调用 LLM judge。

#### Scenario: Hard rule failure triggers judge candidate state
- **WHEN** 回答存在时间约束未覆盖、比较对象缺失、未支撑的具体数字、明显证据不足或其他预定义硬信号
- **THEN** 系统 SHALL 将该回答标记为 post-check 高风险候选
- **AND** 系统 SHALL 将命中的规则和对应原因写入 post-check 元数据

#### Scenario: No high-risk rule hit skips judge
- **WHEN** 回答未命中任何需要进一步审查的规则
- **THEN** 系统 SHALL 允许跳过 LLM judge
- **AND** 系统 SHALL 直接将该回答视为通过 post-check

### Requirement: LLM judge SHALL return a structured verdict
系统 SHALL 使用结构化输出的 LLM judge 判断回答是否满足 query 的最低完成条件，并决定是否适合 fallback 到 ReAct。

#### Scenario: Judge reports satisfiable answer
- **WHEN** LLM judge 认为回答已覆盖关键约束且证据充分
- **THEN** judge 输出 SHALL 包含 `passes_postcheck=true`
- **AND** judge 输出 SHALL 包含 failure types 为空或明确为无

#### Scenario: Judge reports fallback-worthy failure
- **WHEN** LLM judge 认为回答未满足需求且失败类型适合多步工具补救
- **THEN** judge 输出 SHALL 包含 `passes_postcheck=false`
- **AND** judge 输出 SHALL 包含 `should_fallback_to_react=true`
- **AND** judge 输出 SHALL 标明 failure types、缺失约束和简要原因

### Requirement: System SHALL trigger ReAct fallback only for recoverable failures
系统 SHALL 在两类情形下将执行升级至 ReAct 路径：（a）统一默认搜索主链路的 post-check 失败且失败类型适合多步工具补救；（b）会话续跑中用户反馈被判定为延续，此时人类反馈充当裁判角色。两类情形 SHALL 复用同一 fallback 上下文构造机制，上下文来源（机器 verdict 或人类反馈）SHALL 在元数据中可区分。

#### Scenario: Recoverable unified-pipeline failure triggers ReAct fallback
- **WHEN** post-check 识别出统一默认搜索主链路的回答存在可恢复失败，如约束覆盖缺失、需要补证据或需要多跳综合
- **THEN** 系统 SHALL 调用 `ReactAgentOrchestrator` 作为 fallback-only 执行器
- **AND** 返回结果 SHALL 标记 fallback 已触发以及触发原因

#### Scenario: Human feedback triggers ReAct continuation
- **WHEN** 携带 `conversation_id` 的反馈轮被判定为延续
- **THEN** 系统 SHALL 构造包含上一轮答案、用户反馈与继承约束的续跑上下文
- **AND** 调用 ReAct 路径在该会话 checkpoint 上续跑
- **AND** 返回元数据 SHALL 标记本轮裁判来源为人类反馈

#### Scenario: Non-recoverable failure does not trigger ReAct
- **WHEN** post-check 失败原因是搜索不可用、外部 API 不可用、数据源缺失或首答已明确承认无足够信息
- **THEN** 系统 SHALL NOT 自动触发 ReAct fallback
- **AND** 系统 SHALL 返回首答或现有错误信息并保留 post-check verdict

### Requirement: Response metadata SHALL expose post-check and fallback outcomes
系统 SHALL 在返回结构中暴露 post-check 判定和 fallback 执行结果，以支持调试、评测和后续调参。

#### Scenario: Passed without fallback
- **WHEN** 首答通过 post-check 且未触发 fallback
- **THEN** `control` SHALL 包含 post-check verdict、命中规则摘要和 `final_executor=default_pipeline`

#### Scenario: Returned from ReAct fallback
- **WHEN** 系统触发 ReAct fallback 并返回 fallback 结果
- **THEN** `control` SHALL 包含 post-check verdict、fallback reason 和 `final_executor=react_fallback`
