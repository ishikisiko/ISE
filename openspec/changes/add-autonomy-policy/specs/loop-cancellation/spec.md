## ADDED Requirements

### Requirement: 请求级取消入口
系统 SHALL 为流式回答请求提供中途取消能力。取消信号 SHALL 与具体请求绑定，SHALL NOT 影响同一会话的其他请求或其他会话。

#### Scenario: 客户端取消进行中的请求
- **WHEN** 客户端在流式回答进行中发出取消
- **THEN** 该请求的循环 SHALL 在下一个节点边界停止推进
- **AND** 其他并发请求 SHALL NOT 受影响

#### Scenario: 取消已结束的请求
- **WHEN** 取消到达时该请求已经结束
- **THEN** 系统 SHALL 安全忽略该取消
- **AND** SHALL NOT 产生错误响应或重复记录

### Requirement: 取消在节点边界生效
取消 SHALL 在 loop 的节点边界被检查并生效，SHALL NOT 中断已经发出的单次工具调用或模型调用。已完成的观察 SHALL 照常进入证据账本。

#### Scenario: 取消发生在工具执行期间
- **WHEN** 取消信号在某次工具调用返回之前到达
- **THEN** 系统 SHALL 等待该次调用自然结束或超时
- **AND** 其结果 SHALL 照常登记后再停止推进

#### Scenario: 取消后不再发起新调用
- **WHEN** 取消已生效
- **THEN** 系统 SHALL NOT 发起新的工具调用或模型调用
- **AND** SHALL NOT 进入新一轮 act

### Requirement: 取消后的证据收口
取消 SHALL NOT 丢弃已获取的证据。系统 SHALL 依据已保留的证据返回一个明确标注为「已取消」的部分结果，SHALL NOT 把部分结果表述为完整答案。

#### Scenario: 已有证据时取消
- **WHEN** 取消生效且账本中存在已保留证据
- **THEN** 响应 SHALL 返回基于该证据的部分结果
- **AND** 响应 SHALL 明确说明执行被用户取消、结果可能不完整

#### Scenario: 无证据时取消
- **WHEN** 取消生效且尚未获得任何证据
- **THEN** 响应 SHALL 返回取消说明而非编造内容

### Requirement: 取消状态与终止原因可区分
取消 SHALL 在控制元数据与执行轨迹中表示为独立状态，SHALL NOT 与 `exhausted`、`stagnated`、`unrecoverable`、`evidence_insufficient` 或 `clarification_required` 混淆。

#### Scenario: 取消状态回显
- **WHEN** 请求被取消
- **THEN** 控制元数据 SHALL 暴露取消状态与取消发生时的迭代序号
- **AND** SHALL NOT 报告任何预算或证据类终止原因

### Requirement: 取消路径的留痕完整
被取消的执行 SHALL 与正常结束的执行一样写出完整的 audit 与执行轨迹记录，包括取消前发生的全部工具调用、verdict 与账本决策。

#### Scenario: 取消不跳过留痕
- **WHEN** 一次执行因取消而结束
- **THEN** audit 记录 SHALL 被完整写出
- **AND** 执行轨迹 SHALL 包含取消前实际发生的每一次工具调用

#### Scenario: 取消与会话状态
- **WHEN** 被取消的执行属于一个持久化会话
- **THEN** 会话状态 SHALL 保持一致且可用于后续轮次
- **AND** SHALL NOT 留下无响应的工具调用记录
