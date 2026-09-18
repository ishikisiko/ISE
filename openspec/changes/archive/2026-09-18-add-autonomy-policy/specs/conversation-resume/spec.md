## ADDED Requirements

### Requirement: 跨轮自主度变更不续跑同一轨迹
系统 SHALL 记录每个会话最近一轮生效的自主度。当新一轮请求的生效自主度与该记录不同时，系统 SHALL NOT 续跑既有 loop 轨迹，SHALL 以新一轮初始状态执行，并 SHALL 在控制元数据中说明发生了自主度切换导致的轨迹重置。

#### Scenario: 同一会话中切换自主度
- **WHEN** 会话存在 checkpoint 且新一轮请求的生效自主度与上一轮不同
- **THEN** 系统 SHALL 以初始状态执行本轮而非续跑
- **AND** 控制元数据 SHALL 标明本轮因自主度切换而重置

#### Scenario: 自主度不变时正常续跑
- **WHEN** 新一轮请求的生效自主度与上一轮相同且被判定为延续
- **THEN** 续跑语义 SHALL 与引入本能力之前一致
- **AND** 证据池与历史 verdicts SHALL 照常从 checkpoint 保留

#### Scenario: 会话首轮
- **WHEN** 会话尚无自主度记录
- **THEN** 本轮 SHALL 正常执行并记录其生效自主度
- **AND** SHALL NOT 被判定为切换

### Requirement: 澄清等待状态的续跑
当上一轮以模型发起的澄清结束时，系统 SHALL 把该会话的下一轮用户输入视为对该澄清的答复并续跑保留的 loop 状态，SHALL NOT 将其判定为话题重置或新问题。

#### Scenario: 用户答复澄清
- **WHEN** 会话上一轮处于澄清等待状态且用户提供了新输入
- **THEN** 系统 SHALL 以追加语义写入该输入并续跑同一轨迹
- **AND** 澄清前已获取的证据与已注入的 advisory 缺口集合 SHALL 保留

#### Scenario: 用户改问别的问题
- **WHEN** 会话处于澄清等待状态而用户显式重置会话
- **THEN** 系统 SHALL 丢弃该等待状态并按新问题执行
- **AND** 被放弃的澄清轮次 SHALL 已有完整 audit 记录

#### Scenario: 澄清等待不重置循环控制字段
- **WHEN** 系统构造澄清答复的续跑输入
- **THEN** `iteration` 与工具预算计数 SHALL 延续澄清发生时的取值而非按新一轮重置
- **AND** 该次澄清往返 SHALL NOT 使执行绕开预算上界
