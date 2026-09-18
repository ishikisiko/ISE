# Jev requires_evidence experiment  (2026-09-18 21:30)
queries: 95  ok: 95  errors: 0  wall: 30.9s  concurrency: 1
latency ms  p50 305  p95 472  min 259  max 754
input tokens/query mean 864

## requires_evidence  (all rows: gold 65 + hard 30)
| rule | t | P | R | F1 | acc | FP | FN |
|---|---|---|---|---|---|---|---|
| type_only | 0.0 | 0.871 | 0.831 | 0.85 | 0.8 | 8 | 11 |
| risk_only | 0.3 | 0.778 | 0.969 | 0.863 | 0.789 | 18 | 2 |
| risk_only | 0.5 | 0.795 | 0.892 | 0.841 | 0.768 | 15 | 7 |
| risk_only | 0.7 | 0.809 | 0.846 | 0.827 | 0.758 | 13 | 10 |
| type_or_risk | 0.3 | 0.768 | 0.969 | 0.857 | 0.779 | 19 | 2 |
| type_or_risk | 0.5 | 0.787 | 0.908 | 0.843 | 0.768 | 16 | 6 |
| type_or_risk | 0.7 | 0.803 | 0.877 | 0.838 | 0.768 | 14 | 8 |
| conservative | 0.3 | 0.768 | 0.969 | 0.857 | 0.779 | 19 | 2 |
| conservative | 0.5 | 0.795 | 0.954 | 0.867 | 0.8 | 16 | 3 |
| conservative | 0.7 | 0.811 | 0.923 | 0.863 | 0.8 | 14 | 5 |
| revised | 0.3 | 0.968 | 0.938 | 0.953 | 0.937 | 2 | 4 |
| revised | 0.5 | 0.983 | 0.877 | 0.927 | 0.905 | 1 | 8 |
| revised | 0.7 | 0.981 | 0.815 | 0.891 | 0.863 | 1 | 12 |
| revised+comparison | 0.3 | 0.926 | 0.969 | 0.947 | 0.926 | 5 | 2 |
| revised+comparison | 0.5 | 0.939 | 0.954 | 0.947 | 0.926 | 4 | 3 |
| revised+comparison | 0.7 | 0.938 | 0.923 | 0.93 | 0.905 | 4 | 5 |

fewest-missed rule: revised+comparison t=0.3  errors:
- qa027 FN(missed-evidence): OpenAI 是哪一年成立的
- qa048 FP(over-search): Apple 苹果 是水果还是公司？
- qa054 FP(over-search): How are you doing today?
- qa056 FP(over-search): 这个项目的核心模块有哪些
- h26 FP(over-search): What is the difference between a process and a thread?
- h27 FP(over-search): Python 里列表和元组有什么区别
- h30 FN(missed-evidence): What is the boiling point of tungsten?

## gold only  revised t=0.3: P 0.958 R 0.939 F1 0.948 acc 0.923
- qa004 FN(missed-evidence): 比较一下 PostgreSQL、MySQL 和 SQLite 的适用场景
- qa020 FN(missed-evidence): MongoDB 与 PostgreSQL 谁更适合 CMS 工作负载？先说结论再说理由
- qa027 FN(missed-evidence): OpenAI 是哪一年成立的
- qa054 FP(over-search): How are you doing today?
- qa056 FP(over-search): 这个项目的核心模块有哪些

## hard only  revised t=0.3: P 1.0 R 0.938 F1 0.968 acc 0.967
- h30 FN(missed-evidence): What is the boiling point of tungsten?

## all minus debatable historical/obscure-stable  revised t=0.3: P 0.968 R 0.968 F1 0.968 acc 0.956
- qa004 FN(missed-evidence): 比较一下 PostgreSQL、MySQL 和 SQLite 的适用场景
- qa020 FN(missed-evidence): MongoDB 与 PostgreSQL 谁更适合 CMS 工作负载？先说结论再说理由
- qa054 FP(over-search): How are you doing today?
- qa056 FP(over-search): 这个项目的核心模块有哪些

## intent_shape (bonus)
accuracy 93/95 = 0.979
- qa046 gold=information_request pred=comparison p_cmp=1.0: 把前者换成后者会更快吗
- qa048 gold=information_request pred=comparison p_cmp=0.5: Apple 苹果 是水果还是公司？

## per-query
| qid | gold | type | conf | tv | ex | rc | lt | loc | ws | ms | query |
|---|---|---|---|---|---|---|---|---|---|---|---|
| qa001 | 1 | factual_lookup | 0.91 | 0.98 | 0.86 | 0.66 | 0.69 | 0.02 | 0.03 | 754 | 对比 GLM-5.2 和 Kimi K2.7 的 API 价格 |
| qa002 | 1 | factual_lookup | 0.79 | 0.75 | 0.09 | 0.2 | 0.37 | 0.02 | 0.03 | 293 | Compare AWS Fargate vs Cloud Run for cold-start tolerance |
| qa003 | 1 | concept_explanation | 0.79 | 0.26 | 0.07 | 0.09 | 0.31 | 0.02 | 0.75 | 276 | Redis 和 Milvus 有什么区别，注意不要只看官方宣传要抓到实际差异 |
| qa004 | 1 | concept_explanation | 0.88 | 0.14 | 0.07 | 0.06 | 0.06 | 0.02 | 0.02 | 259 | 比较一下 PostgreSQL、MySQL 和 SQLite 的适用场景 |
| qa005 | 1 | factual_lookup | 0.75 | 0.49 | 0.07 | 0.1 | 0.03 | 0.02 | 0.02 | 286 | 苹果和微软的区别 |
| qa006 | 1 | factual_lookup | 0.68 | 0.61 | 0.11 | 0.19 | 0.8 | 0.02 | 0.03 | 318 | Tavily、Firecrawl 与 BrightData 对应的用来获取网页内容的 API 有什么不同 |
| qa007 | 1 | factual_lookup | 0.95 | 0.9 | 0.33 | 0.82 | 0.3 | 0.02 | 0.02 | 337 | 帮我对比 iPhone 17 与 Pixel 10 的相机 |
| qa008 | 1 | creative_or_opinion | 0.84 | 0.85 | 0.4 | 0.61 | 0.76 | 0.03 | 0.02 | 312 | Claude Fable 5.1 和 Opus 5 哪个更适合写代码 |
| qa009 | 1 | factual_lookup | 0.97 | 0.97 | 0.93 | 0.76 | 0.73 | 0.02 | 0.03 | 302 | DeepSeek V4 vs Qwen 3.7 pricing per million tokens |
| qa010 | 1 | factual_lookup | 0.98 | 0.83 | 0.61 | 0.23 | 0.52 | 0.02 | 0.96 | 283 | Which is faster for vector search: FAISS or Milvus? Please c |
| qa011 | 1 | concept_explanation | 0.58 | 0.46 | 0.15 | 0.19 | 0.46 | 0.03 | 0.97 | 315 | LangChain 与 LlamaIndex 在 RAG 场景下的区别是什么？请给出官方文档依据 |
| qa012 | 1 | factual_lookup | 0.99 | 0.95 | 0.51 | 0.37 | 0.38 | 0.02 | 0.02 | 295 | Stripe 和 Adyen 的手续费谁更低 |
| qa013 | 1 | creative_or_opinion | 0.55 | 0.71 | 0.06 | 0.16 | 0.17 | 0.04 | 0.03 | 272 | Kubernetes on-prem versus managed Kubernetes for a regulated |
| qa014 | 1 | factual_lookup | 0.94 | 0.96 | 0.95 | 0.55 | 0.84 | 0.02 | 0.03 | 361 | Brave Search API 和 Tavily 的免费额度各是多少 |
| qa015 | 1 | factual_lookup | 0.89 | 0.98 | 0.69 | 0.44 | 0.19 | 0.02 | 0.03 | 283 | Compare the three largest cloud providers on egress pricing |
| qa016 | 1 | factual_lookup | 0.92 | 0.96 | 0.95 | 0.79 | 0.4 | 0.02 | 0.02 | 282 | GPT-5 和 Claude 的上下文窗口分别多大，输出成表格 |
| qa017 | 1 | current_state | 0.95 | 0.95 | 0.2 | 0.85 | 0.23 | 0.03 | 0.03 | 578 | Vite versus Webpack build speed in 2026 |
| qa018 | 1 | factual_lookup | 1.0 | 0.93 | 0.65 | 0.58 | 0.21 | 0.03 | 0.91 | 361 | 把 Tesla Model 3 和 BYD Seal 的续航做个对比，不要引用二手评测 |
| qa019 | 1 | creative_or_opinion | 0.71 | 0.5 | 0.08 | 0.22 | 0.19 | 0.02 | 0.02 | 286 | Rust vs Go vs Zig for systems programming |
| qa020 | 1 | creative_or_opinion | 0.94 | 0.14 | 0.19 | 0.11 | 0.09 | 0.02 | 0.02 | 274 | MongoDB 与 PostgreSQL 谁更适合 CMS 工作负载？先说结论再说理由 |
| qa021 | 1 | factual_lookup | 0.7 | 0.82 | 0.18 | 0.2 | 0.26 | 0.07 | 0.03 | 310 | Notion vs Obsidian: which respects privacy more under GDPR? |
| qa022 | 1 | computation_or_code | 0.53 | 0.91 | 0.39 | 0.11 | 0.34 | 0.23 | 0.02 | 340 | 对比前者和后者的价格 |
| qa023 | 1 | current_state | 1.0 | 0.98 | 0.38 | 0.98 | 0.05 | 0.01 | 0.01 | 284 | 现在北京天气怎么样 |
| qa024 | 1 | current_state | 0.86 | 0.96 | 0.34 | 0.96 | 0.29 | 0.02 | 0.03 | 276 | 最近三年 AI 芯片出货量的变化趋势 |
| qa025 | 1 | factual_lookup | 0.99 | 0.55 | 0.73 | 0.46 | 0.08 | 0.03 | 0.03 | 305 | 2020 到 2024 年比特币价格走势 |
| qa026 | 1 | current_state | 0.97 | 0.99 | 0.95 | 0.99 | 0.06 | 0.01 | 0.02 | 323 | What is the latest version of Python? |
| qa027 | 1 | factual_lookup | 1.0 | 0.04 | 0.97 | 0.07 | 0.07 | 0.01 | 0.02 | 298 | OpenAI 是哪一年成立的 |
| qa028 | 1 | current_state | 0.99 | 0.99 | 0.98 | 0.98 | 0.12 | 0.01 | 0.02 | 280 | 今天纳斯达克收盘多少点 |
| qa029 | 1 | current_state | 0.99 | 0.98 | 0.81 | 0.98 | 0.48 | 0.02 | 0.03 | 328 | 过去一周 GitHub Copilot 有哪些更新 |
| qa030 | 1 | factual_lookup | 1.0 | 0.33 | 0.86 | 0.1 | 0.06 | 0.02 | 0.03 | 340 | Bitcoin price history from 2017 through 2021 |
| qa031 | 1 | current_state | 0.7 | 0.98 | 0.93 | 0.98 | 0.56 | 0.02 | 0.02 | 374 | 目前 DeepSeek 最新模型的定价是多少 |
| qa032 | 1 | factual_lookup | 0.53 | 0.96 | 0.75 | 0.86 | 0.11 | 0.02 | 0.03 | 269 | 近五年上海常住人口变化 |
| qa033 | 1 | factual_lookup | 0.84 | 0.69 | 0.39 | 0.93 | 0.21 | 0.02 | 0.02 | 302 | What changed in React 19 compared with React 18? |
| qa034 | 1 | current_state | 0.98 | 0.96 | 0.89 | 0.96 | 0.48 | 0.02 | 0.03 | 338 | 现在有哪些 VS Code 插件支持 Copilot |
| qa035 | 1 | current_state | 0.92 | 0.98 | 0.96 | 0.96 | 0.07 | 0.01 | 0.02 | 330 | 昨天英超有哪些比赛结果 |
| qa036 | 1 | current_state | 0.94 | 0.98 | 0.78 | 0.95 | 0.37 | 0.02 | 0.03 | 284 | Is Kubernetes 1.31 still supported today? |
| qa037 | 1 | current_state | 0.64 | 0.98 | 0.46 | 0.98 | 0.88 | 0.03 | 0.03 | 311 | 最近一个月 Firecrawl 的价格有没有调整 |
| qa038 | 1 | factual_lookup | 1.0 | 0.9 | 0.97 | 0.09 | 0.07 | 0.02 | 0.03 | 300 | 历年诺贝尔物理学奖得主名单 |
| qa039 | 1 | current_state | 0.47 | 0.96 | 0.95 | 0.96 | 0.78 | 0.02 | 0.03 | 353 | 当前 Brave Search API 每月免费配额是多少 |
| qa040 | 1 | factual_lookup | 0.98 | 0.95 | 0.89 | 0.32 | 0.57 | 0.13 | 0.01 | 292 | 它的价格是多少 |
| qa041 | 1 | about_assistant | 0.97 | 0.67 | 0.06 | 0.26 | 0.43 | 0.16 | 0.02 | 294 | 这个模型支持多模态吗 |
| qa042 | 1 | factual_lookup | 0.35 | 0.5 | 0.1 | 0.1 | 0.15 | 0.17 | 0.02 | 280 | 帮我对比一下 |
| qa043 | 1 | factual_lookup | 0.85 | 0.91 | 0.7 | 0.1 | 0.24 | 0.12 | 0.01 | 285 | Which one is cheaper? |
| qa044 | 1 | factual_lookup | 0.97 | 0.86 | 0.92 | 0.32 | 0.74 | 0.07 | 0.02 | 362 | 那个 API 的速率限制是多少 |
| qa045 | 1 | current_state | 0.57 | 0.88 | 0.08 | 0.48 | 0.47 | 0.33 | 0.02 | 287 | How does it compare to the previous version? |
| qa046 | 1 | creative_or_opinion | 0.31 | 0.42 | 0.1 | 0.12 | 0.49 | 0.26 | 0.02 | 305 | 把前者换成后者会更快吗 |
| qa047 | 1 | factual_lookup | 0.99 | 0.96 | 0.92 | 0.67 | 0.8 | 0.03 | 0.02 | 326 | GLM-5.2 的价格和它的上下文长度 |
| qa048 | 0 | factual_lookup | 0.76 | 0.05 | 0.17 | 0.06 | 0.04 | 0.02 | 0.02 | 515 | Apple 苹果 是水果还是公司？ |
| qa049 | 1 | creative_or_opinion | 0.44 | 0.34 | 0.09 | 0.08 | 0.24 | 0.29 | 0.02 | 288 | Compare these two |
| qa050 | 1 | factual_lookup | 0.95 | 0.91 | 0.55 | 0.13 | 0.48 | 0.13 | 0.01 | 294 | 它们哪个更便宜 |
| qa051 | 0 | about_assistant | 1.0 | 0.17 | 0.26 | 0.13 | 0.08 | 0.02 | 0.01 | 354 | 你是谁 |
| qa052 | 0 | creative_or_opinion | 1.0 | 0.03 | 0.04 | 0.05 | 0.02 | 0.01 | 0.01 | 324 | 给我一句鼓励的话 |
| qa053 | 0 | concept_explanation | 1.0 | 0.05 | 0.04 | 0.17 | 0.35 | 0.04 | 0.02 | 315 | 用一句话解释什么是 RAG |
| qa054 | 0 | chit_chat | 1.0 | 0.13 | 0.02 | 0.93 | 0.02 | 0.02 | 0.01 | 330 | How are you doing today? |
| qa055 | 0 | creative_or_opinion | 1.0 | 0.03 | 0.03 | 0.04 | 0.03 | 0.01 | 0.01 | 301 | Tell me a joke |
| qa056 | 0 | local_documents | 1.0 | 0.36 | 0.71 | 0.12 | 0.6 | 0.77 | 0.02 | 281 | 这个项目的核心模块有哪些 |
| qa057 | 0 | local_documents | 1.0 | 0.1 | 0.1 | 0.07 | 0.45 | 0.73 | 0.07 | 297 | 文档里对 rerank 的作用是怎么描述的？ |
| qa058 | 0 | local_documents | 1.0 | 0.13 | 0.45 | 0.13 | 0.51 | 0.95 | 0.07 | 313 | 我上传的文件里有没有提到 XXX |
| qa059 | 0 | local_documents | 1.0 | 0.11 | 0.36 | 0.06 | 0.1 | 0.92 | 0.02 | 296 | Summarize the uploaded PDF in three bullet points |
| qa060 | 0 | computation_or_code | 1.0 | 0.02 | 0.05 | 0.03 | 0.02 | 0.02 | 0.01 | 389 | Write a Python function to reverse a string |
| qa061 | 0 | computation_or_code | 1.0 | 0.02 | 0.96 | 0.03 | 0.02 | 0.01 | 0.01 | 277 | Convert 10 miles to kilometers |
| qa062 | 0 | translation | 1.0 | 0.02 | 0.56 | 0.05 | 0.02 | 0.01 | 0.01 | 303 | Translate “good morning” to Spanish |
| qa063 | 0 | local_documents | 1.0 | 0.24 | 0.87 | 0.13 | 0.57 | 0.7 | 0.07 | 263 | 文档中对本地 RAG 的 chunk size 和 overlap 是怎么设置的？ |
| qa064 | 0 | concept_explanation | 1.0 | 0.02 | 0.05 | 0.05 | 0.02 | 0.01 | 0.01 | 283 | What is photosynthesis? |
| qa065 | 0 | computation_or_code | 1.0 | 0.02 | 0.97 | 0.02 | 0.03 | 0.01 | 0.01 | 472 | Calculate 5 factorial |
| h01 | 1 | current_state | 0.56 | 0.98 | 0.97 | 0.97 | 0.19 | 0.01 | 0.02 | 538 | OpenAI 现在的 CTO 是谁 |
| h02 | 1 | current_state | 0.6 | 0.98 | 0.97 | 0.98 | 0.07 | 0.01 | 0.02 | 332 | Who is the current CEO of Intel? |
| h03 | 1 | factual_lookup | 0.94 | 0.76 | 0.91 | 0.89 | 0.51 | 0.01 | 0.03 | 315 | Python 3.13 移除了哪些标准库模块 |
| h04 | 1 | factual_lookup | 0.97 | 0.97 | 0.94 | 0.71 | 0.04 | 0.01 | 0.02 | 337 | 东京的人口是多少 |
| h05 | 1 | factual_lookup | 0.94 | 0.83 | 0.94 | 0.59 | 0.04 | 0.01 | 0.02 | 391 | What is the tallest building in the world? |
| h06 | 1 | current_state | 0.66 | 0.94 | 0.93 | 0.98 | 0.28 | 0.02 | 0.03 | 283 | Redis 最新版本的许可证是什么 |
| h07 | 1 | factual_lookup | 0.88 | 0.98 | 0.94 | 0.81 | 0.44 | 0.02 | 0.02 | 304 | How many employees does Anthropic have? |
| h08 | 1 | factual_lookup | 1.0 | 0.95 | 0.51 | 0.39 | 0.58 | 0.03 | 0.03 | 311 | Vercel 的 Hobby 计划有什么限制 |
| h09 | 1 | factual_lookup | 1.0 | 0.43 | 0.31 | 0.4 | 0.77 | 0.03 | 0.02 | 304 | What does the company Cognition Labs build? |
| h10 | 1 | current_state | 0.91 | 0.98 | 0.97 | 0.98 | 0.08 | 0.01 | 0.02 | 318 | Who won the most recent Ballon d'Or? |
| h11 | 1 | current_state | 0.91 | 0.95 | 0.9 | 0.94 | 0.78 | 0.14 | 0.03 | 522 | llama.cpp 目前支持哪些量化格式 |
| h12 | 1 | factual_lookup | 0.99 | 0.93 | 0.15 | 0.66 | 0.18 | 0.02 | 0.02 | 334 | Is Docker Desktop free for companies? |
| h13 | 1 | factual_lookup | 1.0 | 0.88 | 0.88 | 0.29 | 0.09 | 0.01 | 0.02 | 355 | 深圳到广州坐高铁最快要多久 |
| h14 | 1 | factual_lookup | 0.99 | 0.48 | 0.96 | 0.22 | 0.78 | 0.02 | 0.02 | 301 | Kubernetes 默认的 pod 驱逐宽限期是多少秒 |
| h15 | 1 | current_state | 0.63 | 0.99 | 0.95 | 0.86 | 0.05 | 0.02 | 0.02 | 262 | What is the market cap of Nvidia? |
| h16 | 0 | factual_lookup | 0.99 | 0.03 | 0.93 | 0.05 | 0.04 | 0.01 | 0.02 | 268 | 光速是多少 |
| h17 | 0 | factual_lookup | 1.0 | 0.08 | 0.97 | 0.12 | 0.04 | 0.01 | 0.01 | 332 | What is the capital of Australia? |
| h18 | 0 | factual_lookup | 1.0 | 0.03 | 0.97 | 0.02 | 0.02 | 0.01 | 0.01 | 271 | 第二次世界大战是哪一年结束的 |
| h19 | 0 | concept_explanation | 1.0 | 0.03 | 0.05 | 0.05 | 0.11 | 0.02 | 0.02 | 297 | Explain the CAP theorem |
| h20 | 0 | concept_explanation | 1.0 | 0.05 | 0.13 | 0.07 | 0.28 | 0.02 | 0.02 | 289 | Python 的 GIL 是什么 |
| h21 | 0 | factual_lookup | 0.99 | 0.02 | 0.94 | 0.03 | 0.03 | 0.01 | 0.01 | 334 | 水的化学式是什么 |
| h22 | 0 | factual_lookup | 1.0 | 0.02 | 0.97 | 0.03 | 0.03 | 0.01 | 0.02 | 326 | Who wrote Pride and Prejudice? |
| h23 | 0 | concept_explanation | 1.0 | 0.03 | 0.04 | 0.05 | 0.28 | 0.02 | 0.02 | 276 | 什么是布隆过滤器，简单解释一下 |
| h24 | 0 | concept_explanation | 1.0 | 0.03 | 0.07 | 0.04 | 0.05 | 0.01 | 0.01 | 298 | How does the TCP three-way handshake work? |
| h25 | 0 | computation_or_code | 1.0 | 0.09 | 0.96 | 0.03 | 0.03 | 0.02 | 0.01 | 317 | 1 GB 等于多少 MB |
| h26 | 0 | concept_explanation | 1.0 | 0.03 | 0.04 | 0.04 | 0.03 | 0.01 | 0.01 | 323 | What is the difference between a process and a thread? |
| h27 | 0 | concept_explanation | 0.91 | 0.04 | 0.03 | 0.04 | 0.04 | 0.01 | 0.01 | 319 | Python 里列表和元组有什么区别 |
| h28 | 0 | factual_lookup | 0.96 | 0.02 | 0.97 | 0.03 | 0.03 | 0.01 | 0.02 | 374 | 圆周率前五位是多少 |
| h29 | 0 | factual_lookup | 1.0 | 0.06 | 0.96 | 0.05 | 0.12 | 0.01 | 0.02 | 302 | Java 8 是哪一年发布的 |
| h30 | 1 | factual_lookup | 1.0 | 0.03 | 0.96 | 0.04 | 0.4 | 0.01 | 0.02 | 358 | What is the boiling point of tungsten? |