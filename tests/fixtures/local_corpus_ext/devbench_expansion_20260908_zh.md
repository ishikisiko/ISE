# 任务二交付：T04 / T03 扩题

日期：2026-09-08。任务一[两轮真实自主度评测报告](../reports/autonomy_evaluation_20260908/report.md)已先交付，本任务随后启动；没有把题库参考补丁加进 ISE 产品，也没有为扩题再启动真实开发模型考试。

## 结论与范围

两题适合扩展：T04 补充跨入口策略与隔离交付能力，T03 补充在既有架构内添加能力并完成证据链的交付能力。它们不依赖彼此，均从历史起点 `2a1445cda0c50ec36829daa8f10909cf5d4253d6` 制作，避免本次自主度实验改变试卷难度。

不是无条件采用原设想：T04 按公开工具身份而非 provider 主机限制，只考普通请求/会话续问，不将模型澄清后的授权续传混进来。T03 使用合成文献服务，冻结输入域、协议、鉴权与预算；必须接入实际 registry、loop、En 台账、`search_hits` 和 JSONL 审计，不只验证 HTTP handler。

制题契约和逐项边界见[适用性分析](/home/ubuntu/.local/share/ise-devbench/controller/docs/expansion-20260908-plan.md)，完整技术交付见[控制器报告](/home/ubuntu/.local/share/ise-devbench/controller/docs/expansion-20260908.md)。

## 已交付

| 内容 | T04 | T03 |
|---|---|---|
| 公开任务包 | 参数、九个工具名、CLI/HTTP/SSE、示例 | 服务协议、字段、输入/预算、示例与可执行开发 mock |
| 参考与替代 | ContextVar / 显式参数传递 | RuntimeSkillHandler / EvidenceSource + Session |
| 私有行为裁判 | 六项；真实入口、工具面、恶意调用、受控并发 | 六项；随机 mock、真实 registry/loop、来源/引用/审计 |
| 错误对照 | 只改提示词、遗漏 SSE、共享集合、续问解除限制 | 脱离 registry、未进 loop、非法参数发请求、预算无效 |
| 新任务版本 | 1.0.0 | 1.0.0 |

四题版为 [ise-v1@1.1.0 清单](/home/ubuntu/.local/share/ise-devbench/controller/suites/ise-v1-expanded.yaml)，本地标签 `suite/ise-v1.1.0`。旧 T01 1.0.1 / T02 1.0.2 entry 原样复用，旧两题清单、题包、冻结锁/标签和成绩不改写。

控制器本地发布提交：`4bd633f97ce46b445247094c35ac561944b00526`；标签已实测指向该提交，提交中的四题清单与工作区一致。suite canonical 摘要为 `sha256:652c598011ea64417ee5279547994eb51c0f55381d50355465453a591534815e`。控制器工作区干净；ISE 的评测代码与交付文档留在工作区，未提交或推送。

新增 `prepare --suite`；批次登记将完整 suite 固定在 `suite.json`，后续启动/发布核验并使用它。T03/T04 额外绑定 `public_task_digest`，覆盖题书与附件，避免只锁代码未锁需求。开发 mock 交付在 `task/`，不污染候选代码提交。

## 资格验收

最终每题 7 棵树 × 3 次独立重建，共 **42 次树级容器验收**；同题三次判定、分数、失败项与代码内容清单摘要完全一致。容器非 root、无外网、代码与裁判只读、墙钟冻结。

| 题目 | base | 参考 | 替代实现 | 四类错误实现 |
|---|---:|---:|---:|---|
| T04 | FAIL 15 | PASS 100 | PASS 100 | FAIL 50 / 80 / 90 / 90 |
| T03 | FAIL 15 | PASS 100 | PASS 100 | FAIL 15 / 80 / 70 / 70 |

分数是 60 功能 + 25 边界 + 15 回归；必过项失败即 FAIL，不以剩余高分判通过。证据入口：

- [T04 资格报告](/home/ubuntu/.local/share/ise-devbench/prep/T04/qualification/report.md)、[T03 资格报告](/home/ubuntu/.local/share/ise-devbench/prep/T03/qualification/report.md)。
- [冻结身份与三次结果](/home/ubuntu/.local/share/ise-devbench/artifacts/qualification/expansion-20260908-freeze.json)：运行 ID、报告/提交摘要、公开投影扫描与 Git 起点隔离。
- [无模型接线与容器可见性检查](/home/ubuntu/.local/share/ise-devbench/artifacts/qualification/expansion-20260908-smoke.json)：合成配置生成四题 × 三次的 12 条计划；两题均实际 prepare，新版 suite 身份与附件正确；开发容器可读公开题书、不可见控制区/原 ISE/裁判路径。没有执行这 12 条计划。
- [控制器测试结果](/home/ubuntu/.local/share/ise-devbench/artifacts/qualification/expansion-20260908-pytest.xml)：493 项通过；7 条既有 tarfile 弃用警告。此处与任务一 ISE 的 543 项产品测试分开。
- [发布前补丁重放检查](/home/ubuntu/.local/share/ise-devbench/artifacts/qualification/expansion-20260908-release-check.json)：12 份补丁各自应用到新建 base 副本，内容摘要均与资格树相同；旧清单字节、旧锁值和旧任务 entry 未变。

公开代码与题包扫描无阻断项；T03 `examples.json` 的开发 mock 合成 key 有一个明确记录的例外，仅在先确认字面值且公开摘要匹配后放行，不修改全局扫描规则。历史 base/public bundle 实际内容与原冻结摘要匹配，开发起点重新初始化单提交 Git，不交付源仓库对象库或参考补丁。

## 制题中发现的问题与证据限制

初始 T03 参考能取回元数据却未接上来源/引用，被裁判 F3 拒绝；已修正通用证据登记与来源标记。T04 初始 CLI 测试夹具打错实际导入位置、提示词错误对照在初始化前读属性，均已修正，原诊断保留。

T04 容器并发项曾误报超时：冻结 CLOCK_REALTIME 使 Python timed lock 在约 0.13 秒即返回超时。增加内部等待没有解决，最终改为无内部限时屏障/等待，保留外部单项/容器 watchdog 和全部隔离断言。最终资格仅采用 v3 r1–r3，旧 v1/v2 不计入；其中 v2 运行跨越制题修订，不能当成单一裁判身份的稳定性证据。T03 最终采用 v1 r1–r3。

冻结时还区分了代码内容清单摘要与归档提交摘要：现有 gzip 包含生成时间戳，三次归档摘要不同。每份归档均按现有算法实算校验；稳定性比较使用相同的内容清单摘要，不要求不同时间生成的压缩包字节相同。本次没有改动历史提交归档算法。

边界必须保留：

- 行为裁判独立于候选 tests 与参考内部结构；替代实现与错误对照验证了这一点。但制题和复核均由同一执行者完成，**没有第二位独立评审者签字**，没有真人主观分。
- scripted LLM 和合成 HTTP 证明集成行为，不证明真实模型会选择工具、真实文献服务质量或开发模型排名；T03 没有全面重跑所有既有 skill 的有效网络成功路径。
- 操作台仍默认旧两题；新增四题版通过 CLI 显式 `--suite suites/ise-v1-expanded.yaml` 选择，当前不宣称网页已完整支持新版套题。
- 新 suite 仅作本地冻结发布，没有推送；ISE 工作区的两轮评测修改保留，未代用户提交。

## 使用

```bash
cd /home/ubuntu/.local/share/ise-devbench/controller
DB_PY=/home/ubuntu/miniforge3/envs/env1/bin/python
"$DB_PY" -m devbench task validate --suite suites/ise-v1-expanded.yaml --root .
```

随后按[使用文档](usage.md)登记参测配置，在 `batch register` 加上述 `--suite`；T03/T04 版本均为 `1.0.0`。实际开发模型比较、模型/CLI/重复数与授权仍在组织该批次时确定，不复用本次合成诊断计划。
