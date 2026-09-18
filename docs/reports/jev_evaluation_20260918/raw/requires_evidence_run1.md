# Jev requires_evidence experiment  (2026-09-18 21:28)
queries: 95  ok: 95  errors: 0  wall: 30.5s  concurrency: 1
latency ms  p50 301  p95 425  min 256  max 794
input tokens/query mean 828

## requires_evidence  (all rows: gold 65 + hard 30)
| rule | t | P | R | F1 | acc | FP | FN |
|---|---|---|---|---|---|---|---|
| type_only | 0.0 | 0.871 | 0.831 | 0.85 | 0.8 | 8 | 11 |
| risk_only | 0.3 | 0.778 | 0.969 | 0.863 | 0.789 | 18 | 2 |
| risk_only | 0.5 | 0.792 | 0.877 | 0.832 | 0.758 | 15 | 8 |
| risk_only | 0.7 | 0.809 | 0.846 | 0.827 | 0.758 | 13 | 10 |
| type_or_risk | 0.3 | 0.768 | 0.969 | 0.857 | 0.779 | 19 | 2 |
| type_or_risk | 0.5 | 0.784 | 0.892 | 0.835 | 0.758 | 16 | 7 |
| type_or_risk | 0.7 | 0.803 | 0.877 | 0.838 | 0.768 | 14 | 8 |
| conservative | 0.3 | 0.768 | 0.969 | 0.857 | 0.779 | 19 | 2 |
| conservative | 0.5 | 0.792 | 0.938 | 0.859 | 0.789 | 16 | 4 |
| conservative | 0.7 | 0.811 | 0.923 | 0.863 | 0.8 | 14 | 5 |

fewest-missed rule: risk_only t=0.3  errors:
- qa004 FN(missed-evidence): 比较一下 PostgreSQL、MySQL 和 SQLite 的适用场景
- qa020 FN(missed-evidence): MongoDB 与 PostgreSQL 谁更适合 CMS 工作负载？先说结论再说理由
- qa053 FP(over-search): 用一句话解释什么是 RAG
- qa054 FP(over-search): How are you doing today?
- qa056 FP(over-search): 这个项目的核心模块有哪些
- qa057 FP(over-search): 文档里对 rerank 的作用是怎么描述的？
- qa058 FP(over-search): 我上传的文件里有没有提到 XXX
- qa059 FP(over-search): Summarize the uploaded PDF in three bullet points
- qa061 FP(over-search): Convert 10 miles to kilometers
- qa062 FP(over-search): Translate “good morning” to Spanish
- qa063 FP(over-search): 文档中对本地 RAG 的 chunk size 和 overlap 是怎么设置的？
- qa065 FP(over-search): Calculate 5 factorial
- h16 FP(over-search): 光速是多少
- h17 FP(over-search): What is the capital of Australia?
- h18 FP(over-search): 第二次世界大战是哪一年结束的
- h21 FP(over-search): 水的化学式是什么
- h22 FP(over-search): Who wrote Pride and Prejudice?
- h25 FP(over-search): 1 GB 等于多少 MB
- h28 FP(over-search): 圆周率前五位是多少
- h29 FP(over-search): Java 8 是哪一年发布的

## gold only  type_or_risk t=0.5: P 0.84 R 0.857 F1 0.848 acc 0.769
- qa003 FN(missed-evidence): Redis 和 Milvus 有什么区别，注意不要只看官方宣传要抓到实际差异
- qa004 FN(missed-evidence): 比较一下 PostgreSQL、MySQL 和 SQLite 的适用场景
- qa011 FN(missed-evidence): LangChain 与 LlamaIndex 在 RAG 场景下的区别是什么？请给出官方文档依据
- qa019 FN(missed-evidence): Rust vs Go vs Zig for systems programming
- qa020 FN(missed-evidence): MongoDB 与 PostgreSQL 谁更适合 CMS 工作负载？先说结论再说理由
- qa046 FN(missed-evidence): 把前者换成后者会更快吗
- qa048 FP(over-search): Apple 苹果 是水果还是公司？
- qa049 FN(missed-evidence): Compare these two
- qa054 FP(over-search): How are you doing today?
- qa056 FP(over-search): 这个项目的核心模块有哪些
- qa058 FP(over-search): 我上传的文件里有没有提到 XXX
- qa061 FP(over-search): Convert 10 miles to kilometers
- qa062 FP(over-search): Translate “good morning” to Spanish
- qa063 FP(over-search): 文档中对本地 RAG 的 chunk size 和 overlap 是怎么设置的？
- qa065 FP(over-search): Calculate 5 factorial

## hard only  type_or_risk t=0.5: P 0.667 R 1.0 F1 0.8 acc 0.733
- h16 FP(over-search): 光速是多少
- h17 FP(over-search): What is the capital of Australia?
- h18 FP(over-search): 第二次世界大战是哪一年结束的
- h21 FP(over-search): 水的化学式是什么
- h22 FP(over-search): Who wrote Pride and Prejudice?
- h25 FP(over-search): 1 GB 等于多少 MB
- h28 FP(over-search): 圆周率前五位是多少
- h29 FP(over-search): Java 8 是哪一年发布的

## intent_shape (bonus)
accuracy 93/95 = 0.979
- qa046 gold=information_request pred=comparison p_cmp=1.0: 把前者换成后者会更快吗
- qa048 gold=information_request pred=comparison p_cmp=0.51: Apple 苹果 是水果还是公司？

## per-query
| qid | gold | type | conf | tv | ex | rc | lt | loc | ms | query |
|---|---|---|---|---|---|---|---|---|---|---|
| qa001 | 1 | factual_lookup | 0.94 | 0.98 | 0.85 | 0.61 | 0.67 | 0.02 | 794 | 对比 GLM-5.2 和 Kimi K2.7 的 API 价格 |
| qa002 | 1 | factual_lookup | 0.77 | 0.74 | 0.1 | 0.21 | 0.4 | 0.02 | 274 | Compare AWS Fargate vs Cloud Run for cold-start tolerance |
| qa003 | 1 | concept_explanation | 0.75 | 0.27 | 0.07 | 0.1 | 0.31 | 0.02 | 337 | Redis 和 Milvus 有什么区别，注意不要只看官方宣传要抓到实际差异 |
| qa004 | 1 | concept_explanation | 0.9 | 0.13 | 0.07 | 0.06 | 0.06 | 0.02 | 278 | 比较一下 PostgreSQL、MySQL 和 SQLite 的适用场景 |
| qa005 | 1 | factual_lookup | 0.71 | 0.49 | 0.07 | 0.09 | 0.03 | 0.02 | 318 | 苹果和微软的区别 |
| qa006 | 1 | factual_lookup | 0.65 | 0.56 | 0.11 | 0.19 | 0.82 | 0.02 | 275 | Tavily、Firecrawl 与 BrightData 对应的用来获取网页内容的 API 有什么不同 |
| qa007 | 1 | factual_lookup | 0.96 | 0.87 | 0.31 | 0.84 | 0.33 | 0.02 | 301 | 帮我对比 iPhone 17 与 Pixel 10 的相机 |
| qa008 | 1 | creative_or_opinion | 0.85 | 0.87 | 0.5 | 0.6 | 0.76 | 0.03 | 457 | Claude Fable 5.1 和 Opus 5 哪个更适合写代码 |
| qa009 | 1 | factual_lookup | 0.97 | 0.97 | 0.92 | 0.79 | 0.71 | 0.02 | 286 | DeepSeek V4 vs Qwen 3.7 pricing per million tokens |
| qa010 | 1 | factual_lookup | 0.98 | 0.83 | 0.59 | 0.23 | 0.5 | 0.02 | 318 | Which is faster for vector search: FAISS or Milvus? Please c |
| qa011 | 1 | concept_explanation | 0.61 | 0.48 | 0.17 | 0.18 | 0.46 | 0.03 | 268 | LangChain 与 LlamaIndex 在 RAG 场景下的区别是什么？请给出官方文档依据 |
| qa012 | 1 | factual_lookup | 0.99 | 0.95 | 0.51 | 0.43 | 0.38 | 0.02 | 456 | Stripe 和 Adyen 的手续费谁更低 |
| qa013 | 1 | creative_or_opinion | 0.56 | 0.72 | 0.06 | 0.16 | 0.16 | 0.03 | 334 | Kubernetes on-prem versus managed Kubernetes for a regulated |
| qa014 | 1 | factual_lookup | 0.95 | 0.96 | 0.95 | 0.55 | 0.82 | 0.02 | 287 | Brave Search API 和 Tavily 的免费额度各是多少 |
| qa015 | 1 | factual_lookup | 0.91 | 0.98 | 0.72 | 0.44 | 0.18 | 0.02 | 430 | Compare the three largest cloud providers on egress pricing |
| qa016 | 1 | factual_lookup | 0.95 | 0.96 | 0.95 | 0.8 | 0.38 | 0.02 | 283 | GPT-5 和 Claude 的上下文窗口分别多大，输出成表格 |
| qa017 | 1 | current_state | 0.95 | 0.94 | 0.18 | 0.85 | 0.23 | 0.03 | 329 | Vite versus Webpack build speed in 2026 |
| qa018 | 1 | factual_lookup | 1.0 | 0.93 | 0.65 | 0.54 | 0.21 | 0.02 | 284 | 把 Tesla Model 3 和 BYD Seal 的续航做个对比，不要引用二手评测 |
| qa019 | 1 | creative_or_opinion | 0.69 | 0.44 | 0.08 | 0.21 | 0.2 | 0.02 | 276 | Rust vs Go vs Zig for systems programming |
| qa020 | 1 | creative_or_opinion | 0.94 | 0.14 | 0.17 | 0.09 | 0.09 | 0.02 | 316 | MongoDB 与 PostgreSQL 谁更适合 CMS 工作负载？先说结论再说理由 |
| qa021 | 1 | factual_lookup | 0.69 | 0.81 | 0.15 | 0.2 | 0.25 | 0.08 | 264 | Notion vs Obsidian: which respects privacy more under GDPR? |
| qa022 | 1 | computation_or_code | 0.48 | 0.91 | 0.44 | 0.1 | 0.32 | 0.23 | 369 | 对比前者和后者的价格 |
| qa023 | 1 | current_state | 1.0 | 0.97 | 0.39 | 0.98 | 0.05 | 0.01 | 370 | 现在北京天气怎么样 |
| qa024 | 1 | current_state | 0.84 | 0.97 | 0.31 | 0.96 | 0.3 | 0.02 | 256 | 最近三年 AI 芯片出货量的变化趋势 |
| qa025 | 1 | factual_lookup | 0.99 | 0.5 | 0.73 | 0.48 | 0.08 | 0.02 | 283 | 2020 到 2024 年比特币价格走势 |
| qa026 | 1 | current_state | 0.98 | 0.99 | 0.96 | 0.99 | 0.05 | 0.01 | 349 | What is the latest version of Python? |
| qa027 | 1 | factual_lookup | 1.0 | 0.05 | 0.97 | 0.07 | 0.07 | 0.02 | 263 | OpenAI 是哪一年成立的 |
| qa028 | 1 | current_state | 0.99 | 0.99 | 0.98 | 0.98 | 0.12 | 0.01 | 280 | 今天纳斯达克收盘多少点 |
| qa029 | 1 | current_state | 0.99 | 0.98 | 0.79 | 0.98 | 0.47 | 0.02 | 270 | 过去一周 GitHub Copilot 有哪些更新 |
| qa030 | 1 | factual_lookup | 1.0 | 0.31 | 0.83 | 0.09 | 0.06 | 0.03 | 329 | Bitcoin price history from 2017 through 2021 |
| qa031 | 1 | current_state | 0.71 | 0.98 | 0.93 | 0.98 | 0.57 | 0.02 | 346 | 目前 DeepSeek 最新模型的定价是多少 |
| qa032 | 1 | factual_lookup | 0.53 | 0.96 | 0.76 | 0.86 | 0.12 | 0.02 | 303 | 近五年上海常住人口变化 |
| qa033 | 1 | factual_lookup | 0.85 | 0.69 | 0.35 | 0.93 | 0.19 | 0.02 | 286 | What changed in React 19 compared with React 18? |
| qa034 | 1 | current_state | 0.99 | 0.96 | 0.88 | 0.96 | 0.49 | 0.02 | 285 | 现在有哪些 VS Code 插件支持 Copilot |
| qa035 | 1 | current_state | 0.93 | 0.98 | 0.96 | 0.96 | 0.07 | 0.01 | 269 | 昨天英超有哪些比赛结果 |
| qa036 | 1 | current_state | 0.95 | 0.97 | 0.78 | 0.94 | 0.37 | 0.02 | 296 | Is Kubernetes 1.31 still supported today? |
| qa037 | 1 | current_state | 0.64 | 0.98 | 0.47 | 0.97 | 0.87 | 0.03 | 275 | 最近一个月 Firecrawl 的价格有没有调整 |
| qa038 | 1 | factual_lookup | 1.0 | 0.89 | 0.97 | 0.09 | 0.07 | 0.02 | 299 | 历年诺贝尔物理学奖得主名单 |
| qa039 | 1 | factual_lookup | 0.44 | 0.96 | 0.95 | 0.96 | 0.78 | 0.02 | 308 | 当前 Brave Search API 每月免费配额是多少 |
| qa040 | 1 | factual_lookup | 0.98 | 0.95 | 0.89 | 0.35 | 0.56 | 0.13 | 462 | 它的价格是多少 |
| qa041 | 1 | about_assistant | 0.96 | 0.66 | 0.05 | 0.21 | 0.43 | 0.17 | 346 | 这个模型支持多模态吗 |
| qa042 | 1 | factual_lookup | 0.3 | 0.52 | 0.1 | 0.09 | 0.15 | 0.17 | 296 | 帮我对比一下 |
| qa043 | 1 | factual_lookup | 0.87 | 0.91 | 0.71 | 0.1 | 0.2 | 0.12 | 328 | Which one is cheaper? |
| qa044 | 1 | factual_lookup | 0.97 | 0.87 | 0.91 | 0.32 | 0.75 | 0.07 | 329 | 那个 API 的速率限制是多少 |
| qa045 | 1 | current_state | 0.56 | 0.89 | 0.08 | 0.47 | 0.47 | 0.31 | 420 | How does it compare to the previous version? |
| qa046 | 1 | creative_or_opinion | 0.32 | 0.4 | 0.1 | 0.12 | 0.48 | 0.26 | 302 | 把前者换成后者会更快吗 |
| qa047 | 1 | factual_lookup | 0.99 | 0.96 | 0.92 | 0.66 | 0.82 | 0.03 | 287 | GLM-5.2 的价格和它的上下文长度 |
| qa048 | 0 | factual_lookup | 0.82 | 0.05 | 0.19 | 0.06 | 0.04 | 0.02 | 351 | Apple 苹果 是水果还是公司？ |
| qa049 | 1 | creative_or_opinion | 0.37 | 0.36 | 0.08 | 0.08 | 0.22 | 0.27 | 281 | Compare these two |
| qa050 | 1 | factual_lookup | 0.94 | 0.92 | 0.59 | 0.12 | 0.42 | 0.14 | 351 | 它们哪个更便宜 |
| qa051 | 0 | about_assistant | 1.0 | 0.19 | 0.26 | 0.12 | 0.08 | 0.02 | 328 | 你是谁 |
| qa052 | 0 | creative_or_opinion | 1.0 | 0.03 | 0.04 | 0.06 | 0.02 | 0.02 | 282 | 给我一句鼓励的话 |
| qa053 | 0 | concept_explanation | 1.0 | 0.05 | 0.04 | 0.16 | 0.36 | 0.04 | 301 | 用一句话解释什么是 RAG |
| qa054 | 0 | chit_chat | 1.0 | 0.12 | 0.03 | 0.92 | 0.02 | 0.01 | 310 | How are you doing today? |
| qa055 | 0 | creative_or_opinion | 1.0 | 0.03 | 0.03 | 0.03 | 0.03 | 0.01 | 293 | Tell me a joke |
| qa056 | 0 | local_documents | 1.0 | 0.36 | 0.72 | 0.12 | 0.61 | 0.78 | 283 | 这个项目的核心模块有哪些 |
| qa057 | 0 | local_documents | 1.0 | 0.09 | 0.11 | 0.07 | 0.45 | 0.73 | 287 | 文档里对 rerank 的作用是怎么描述的？ |
| qa058 | 0 | local_documents | 1.0 | 0.11 | 0.45 | 0.12 | 0.51 | 0.95 | 276 | 我上传的文件里有没有提到 XXX |
| qa059 | 0 | local_documents | 1.0 | 0.11 | 0.35 | 0.07 | 0.1 | 0.94 | 306 | Summarize the uploaded PDF in three bullet points |
| qa060 | 0 | computation_or_code | 1.0 | 0.02 | 0.04 | 0.03 | 0.02 | 0.02 | 284 | Write a Python function to reverse a string |
| qa061 | 0 | computation_or_code | 1.0 | 0.02 | 0.96 | 0.03 | 0.02 | 0.01 | 410 | Convert 10 miles to kilometers |
| qa062 | 0 | translation | 1.0 | 0.02 | 0.56 | 0.05 | 0.02 | 0.01 | 343 | Translate “good morning” to Spanish |
| qa063 | 0 | local_documents | 1.0 | 0.21 | 0.88 | 0.13 | 0.56 | 0.71 | 345 | 文档中对本地 RAG 的 chunk size 和 overlap 是怎么设置的？ |
| qa064 | 0 | concept_explanation | 1.0 | 0.03 | 0.04 | 0.05 | 0.02 | 0.01 | 306 | What is photosynthesis? |
| qa065 | 0 | computation_or_code | 1.0 | 0.02 | 0.97 | 0.02 | 0.03 | 0.01 | 336 | Calculate 5 factorial |
| h01 | 1 | current_state | 0.58 | 0.98 | 0.97 | 0.97 | 0.19 | 0.01 | 297 | OpenAI 现在的 CTO 是谁 |
| h02 | 1 | current_state | 0.61 | 0.98 | 0.97 | 0.98 | 0.08 | 0.01 | 295 | Who is the current CEO of Intel? |
| h03 | 1 | factual_lookup | 0.95 | 0.79 | 0.92 | 0.88 | 0.53 | 0.01 | 350 | Python 3.13 移除了哪些标准库模块 |
| h04 | 1 | factual_lookup | 0.97 | 0.97 | 0.93 | 0.66 | 0.04 | 0.01 | 308 | 东京的人口是多少 |
| h05 | 1 | factual_lookup | 0.95 | 0.83 | 0.94 | 0.57 | 0.04 | 0.01 | 287 | What is the tallest building in the world? |
| h06 | 1 | current_state | 0.67 | 0.89 | 0.91 | 0.98 | 0.28 | 0.02 | 338 | Redis 最新版本的许可证是什么 |
| h07 | 1 | factual_lookup | 0.91 | 0.98 | 0.95 | 0.8 | 0.46 | 0.02 | 277 | How many employees does Anthropic have? |
| h08 | 1 | factual_lookup | 1.0 | 0.95 | 0.45 | 0.37 | 0.62 | 0.03 | 396 | Vercel 的 Hobby 计划有什么限制 |
| h09 | 1 | factual_lookup | 1.0 | 0.33 | 0.34 | 0.36 | 0.75 | 0.03 | 298 | What does the company Cognition Labs build? |
| h10 | 1 | current_state | 0.9 | 0.98 | 0.97 | 0.98 | 0.07 | 0.01 | 330 | Who won the most recent Ballon d'Or? |
| h11 | 1 | current_state | 0.93 | 0.95 | 0.89 | 0.94 | 0.78 | 0.13 | 294 | llama.cpp 目前支持哪些量化格式 |
| h12 | 1 | factual_lookup | 0.99 | 0.93 | 0.12 | 0.64 | 0.18 | 0.02 | 269 | Is Docker Desktop free for companies? |
| h13 | 1 | factual_lookup | 0.99 | 0.86 | 0.88 | 0.32 | 0.09 | 0.01 | 272 | 深圳到广州坐高铁最快要多久 |
| h14 | 1 | factual_lookup | 0.99 | 0.45 | 0.96 | 0.23 | 0.76 | 0.02 | 270 | Kubernetes 默认的 pod 驱逐宽限期是多少秒 |
| h15 | 1 | current_state | 0.6 | 0.98 | 0.96 | 0.87 | 0.06 | 0.02 | 351 | What is the market cap of Nvidia? |
| h16 | 0 | factual_lookup | 0.99 | 0.03 | 0.93 | 0.05 | 0.04 | 0.01 | 279 | 光速是多少 |
| h17 | 0 | factual_lookup | 1.0 | 0.07 | 0.97 | 0.13 | 0.04 | 0.01 | 374 | What is the capital of Australia? |
| h18 | 0 | factual_lookup | 1.0 | 0.02 | 0.97 | 0.03 | 0.02 | 0.01 | 367 | 第二次世界大战是哪一年结束的 |
| h19 | 0 | concept_explanation | 1.0 | 0.03 | 0.04 | 0.05 | 0.1 | 0.02 | 265 | Explain the CAP theorem |
| h20 | 0 | concept_explanation | 1.0 | 0.05 | 0.11 | 0.06 | 0.28 | 0.01 | 334 | Python 的 GIL 是什么 |
| h21 | 0 | factual_lookup | 0.99 | 0.02 | 0.94 | 0.03 | 0.03 | 0.01 | 287 | 水的化学式是什么 |
| h22 | 0 | factual_lookup | 1.0 | 0.02 | 0.97 | 0.02 | 0.03 | 0.01 | 298 | Who wrote Pride and Prejudice? |
| h23 | 0 | concept_explanation | 1.0 | 0.03 | 0.04 | 0.05 | 0.27 | 0.02 | 363 | 什么是布隆过滤器，简单解释一下 |
| h24 | 0 | concept_explanation | 1.0 | 0.03 | 0.07 | 0.04 | 0.05 | 0.01 | 276 | How does the TCP three-way handshake work? |
| h25 | 0 | computation_or_code | 1.0 | 0.08 | 0.96 | 0.03 | 0.03 | 0.02 | 355 | 1 GB 等于多少 MB |
| h26 | 0 | concept_explanation | 1.0 | 0.03 | 0.05 | 0.03 | 0.03 | 0.02 | 275 | What is the difference between a process and a thread? |
| h27 | 0 | concept_explanation | 0.94 | 0.03 | 0.03 | 0.04 | 0.04 | 0.01 | 425 | Python 里列表和元组有什么区别 |
| h28 | 0 | factual_lookup | 0.95 | 0.02 | 0.97 | 0.03 | 0.03 | 0.01 | 360 | 圆周率前五位是多少 |
| h29 | 0 | factual_lookup | 1.0 | 0.06 | 0.96 | 0.05 | 0.13 | 0.01 | 271 | Java 8 是哪一年发布的 |
| h30 | 1 | factual_lookup | 1.0 | 0.03 | 0.96 | 0.04 | 0.42 | 0.01 | 273 | What is the boiling point of tungsten? |