## MODIFIED Requirements

### Requirement: ReactAgentOrchestrator 接口
系统 SHALL 提供 `ReactAgentOrchestrator` 类，对外暴露与现有编排器一致的 `answer()` 接口。`answer()` SHALL 接受可选关键字参数 `conversation_id`：提供时按 conversation-resume 能力在该会话的 checkpoint 上续跑或新建线程；缺省时行为与无状态单轮完全一致。

#### Scenario: 基本回答流程
- **WHEN** 用户调用 `orchestrator.answer(query)`
- **THEN** Orchestrator SHALL 使用 ReAct Agent 处理查询
- **AND** 返回包含 `answer`、`control`、`search_hits` 的兼容字典
- **AND** 返回结构 SHALL 允许附带统一 evidence 元数据

#### Scenario: 返回结构兼容
- **WHEN** 任意编排器返回结果
- **THEN** `answer` 字段包含最终回答文本
- **AND** `control` 字段包含元数据（search_performed、decision 等）
- **AND** `search_hits` 字段包含搜索结果（如有）
- **AND** 如存在 fallback 上下文中的统一证据元数据，返回结构 SHALL 保留 `evidence_items` 或 `evidence_sources_*` 语义

#### Scenario: 携带会话标识续跑
- **WHEN** 用户调用 `orchestrator.answer(query, conversation_id="abc")` 且该会话存在 checkpoint
- **THEN** Orchestrator SHALL 以 `thread_id="abc"` 恢复图状态并注入本轮反馈续跑
- **AND** 返回结构的 `control` SHALL 标记本轮为会话续跑（如 `conversation_resumed=true`）

#### Scenario: 会话标识缺省保持无状态
- **WHEN** 用户调用 `orchestrator.answer(query)` 且未提供 `conversation_id`
- **THEN** Orchestrator SHALL NOT 读写任何 checkpoint
- **AND** 行为与无状态单轮完全一致
