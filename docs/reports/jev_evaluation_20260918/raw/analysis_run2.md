# analysis  (65/65 ok)
latency ms p50 309 p95 567 max 835 (concurrency 4)

## claim_classes micro baseline: P 0.875 R 0.651 F1 0.747   per-class F1: comparison 0.792, numeric 0.645, pricing 0.8, current 0.875, compliance 0.0, temporal 0.632, historical 0.889
## claim_classes micro jev t=0.5: P 0.849 R 0.919 F1 0.883   per-class F1: comparison 0.947, numeric 0.78, pricing 0.968, current 0.737, compliance 1.0, temporal 0.824, historical 1.0
## claim_classes micro jev t=0.7: P 0.893 R 0.872 F1 0.882   per-class F1: comparison 0.909, numeric 0.778, pricing 1.0, current 0.778, compliance 1.0, temporal 0.824, historical 1.0
jev t=0.5 claim errors:
- qa012 gold=['comparison', 'numeric', 'pricing'] jev=['comparison', 'pricing'] base=[]: Stripe 和 Adyen 的手续费谁更低
- qa015 gold=['comparison', 'numeric', 'pricing'] jev=['comparison', 'pricing'] base=['comparison', 'numeric', 'pricing']: Compare the three largest cloud providers on egress pricing
- qa017 gold=['comparison', 'temporal'] jev=['comparison', 'current', 'temporal'] base=['comparison']: Vite versus Webpack build speed in 2026
- qa027 gold=[] jev=['numeric'] base=['temporal']: OpenAI 是哪一年成立的
- qa028 gold=['current', 'numeric'] jev=['current', 'numeric', 'temporal'] base=['current']: 今天纳斯达克收盘多少点
- qa029 gold=['temporal'] jev=['current', 'temporal'] base=['temporal']: 过去一周 GitHub Copilot 有哪些更新
- qa030 gold=['historical', 'numeric', 'pricing', 'temporal'] jev=['historical', 'pricing', 'temporal'] base=['historical', 'numeric', 'pricing', 'temporal']: Bitcoin price history from 2017 through 2021
- qa032 gold=['historical', 'temporal'] jev=['historical', 'numeric', 'temporal'] base=['historical', 'temporal']: 近五年上海常住人口变化
- qa035 gold=['current'] jev=['temporal'] base=[]: 昨天英超有哪些比赛结果
- qa037 gold=['numeric', 'pricing', 'temporal'] jev=['current', 'numeric', 'pricing', 'temporal'] base=['numeric', 'pricing', 'temporal']: 最近一个月 Firecrawl 的价格有没有调整
- qa039 gold=['current', 'numeric', 'pricing'] jev=['current', 'numeric', 'pricing', 'temporal'] base=['current', 'temporal']: 当前 Brave Search API 每月免费配额是多少
- qa042 gold=['comparison'] jev=[] base=['comparison']: 帮我对比一下
- qa043 gold=['comparison', 'numeric', 'pricing'] jev=['comparison', 'pricing'] base=[]: Which one is cheaper?
- qa044 gold=['numeric'] jev=['numeric', 'pricing'] base=[]: 那个 API 的速率限制是多少
- qa046 gold=[] jev=['comparison'] base=[]: 把前者换成后者会更快吗
- qa048 gold=[] jev=['comparison'] base=[]: Apple 苹果 是水果还是公司？
- qa050 gold=['comparison', 'numeric', 'pricing'] jev=['comparison', 'pricing'] base=[]: 它们哪个更便宜
- qa052 gold=[] jev=['numeric'] base=[]: 给我一句鼓励的话
- qa054 gold=[] jev=['current'] base=['current']: How are you doing today?
- qa059 gold=[] jev=['numeric'] base=[]: Summarize the uploaded PDF in three bullet points
## critical_ambiguity baseline: P 0.8 R 0.727 F1 0.762  (tp 8 fp 2 fn 3)
## critical_ambiguity jev t=0.5: P 0.786 R 1.0 F1 0.88  (tp 11 fp 3 fn 0)
## critical_ambiguity jev t=0.5 & not local_context: P 1.0 R 1.0 F1 1.0  (tp 11 fp 0 fn 0)
## existence baseline: P 0.6 R 0.75 F1 0.667  (tp 3 fp 2 fn 1)
## existence jev t=0.5: P 1.0 R 0.5 F1 0.667  (tp 2 fp 0 fn 2)
  - qa035 gold=1 p=0.15: 昨天英超有哪些比赛结果
  - qa058 gold=1 p=0.09: 我上传的文件里有没有提到 XXX
## existence jev t=0.5 & not local_context: P 1.0 R 0.25 F1 0.4  (tp 1 fp 0 fn 3)
  - qa035 gold=1 p=0.15: 昨天英超有哪些比赛结果
  - qa056 gold=1 p=0.95: 这个项目的核心模块有哪些
  - qa058 gold=1 p=0.09: 我上传的文件里有没有提到 XXX
## time_scope accuracy baseline 0.908  jev 0.969
  - qa029 gold=window jev=recent conf=0.99 base=window: 过去一周 GitHub Copilot 有哪些更新
  - qa054 gold=none jev=recent conf=1.0 base=recent: How are you doing today?
## comparison members baseline: P 0.867 R 0.605 F1 0.712 noise_member_rate 0.133 (4/30)
## comparison members jev-filtered t=0.5: P 0.707 R 0.953 F1 0.812 noise_member_rate 0.086 (5/58)
  - qa002 gold=['AWS Fargate', 'Cloud Run'] pred=['AWS Fargate', 'Cloud Run for cold-start tolerance', 'Fargate', 'Run'] noise=['Run']
  - qa012 gold=['Stripe', 'Adyen'] pred=['Adyen'] noise=[]
  - qa015 gold=[] pred=['egress'] noise=['egress']
  - qa019 gold=['Rust', 'Go', 'Zig'] pred=['Go', 'Zig for systems programming', 'Zig'] noise=['Zig for systems programming']
  - qa022 gold=[] pred=['前者', '后者'] noise=['前者', '后者']
## comparison members jev-filtered+clean+dedupe t=0.5: P 0.881 R 0.86 F1 0.871 noise_member_rate 0.095 (4/42)
  - qa002 gold=['AWS Fargate', 'Cloud Run'] pred=['AWS Fargate', 'Run'] noise=['Run']
  - qa007 gold=['iPhone 17', 'Pixel 10'] pred=['iPhone 17'] noise=[]
  - qa012 gold=['Stripe', 'Adyen'] pred=['Adyen'] noise=[]
  - qa013 gold=['on-prem Kubernetes', 'managed Kubernetes'] pred=[] noise=[]
  - qa015 gold=[] pred=['egress'] noise=['egress']
  - qa019 gold=['Rust', 'Go', 'Zig'] pred=['Go', 'Zig'] noise=[]
  - qa022 gold=[] pred=['前者', '后者'] noise=['前者', '后者']
## comparison members jev-filtered+clean+dedupe t=0.7: P 0.925 R 0.86 F1 0.892 noise_member_rate 0.050 (2/40)
  - qa002 gold=['AWS Fargate', 'Cloud Run'] pred=['AWS Fargate'] noise=[]
  - qa007 gold=['iPhone 17', 'Pixel 10'] pred=['iPhone 17'] noise=[]
  - qa012 gold=['Stripe', 'Adyen'] pred=['Adyen'] noise=[]
  - qa013 gold=['on-prem Kubernetes', 'managed Kubernetes'] pred=[] noise=[]
  - qa019 gold=['Rust', 'Go', 'Zig'] pred=['Go', 'Zig'] noise=[]
  - qa022 gold=[] pred=['前者', '后者'] noise=['前者', '后者']

candidate probabilities compared/clean (comparison rows):
- qa001 GLM-5.2=0.98/0.96, K2.7=0.82/0.84, Kimi=0.38/0.96
- qa002 AWS Fargate=0.98/0.96, Cloud Run for cold-start tolerance=0.94/0.11, AWS=0.25/0.93, Fargate=0.94/0.97, Cloud=0.21/0.69, Run=0.55/0.78
- qa003 Redis=0.99/0.98, Milvus 有什么=0.8/0.06, 注意不要只看官方宣传要抓到实际差异=0.1/0.01, Milvus=0.98/0.97
- qa004 一下 PostgreSQL=0.68/0.05, MySQL=0.99/0.97, SQLite 的适用场景=0.94/0.05, PostgreSQL=0.98/0.97, SQLite=0.95/0.97
- qa005 苹果=0.99/0.98, 微软=0.99/0.98
- qa006 Tavily=0.99/0.98, Firecrawl=0.99/0.97, BrightData=0.99/0.97
- qa007 iPhone 17=0.94/0.96, Pixel 10 的相机=0.97/0.3, Pixel=0.32/0.96
- qa008 Fable=0.97/0.82, Opus=0.96/0.88
- qa009 Qwen 3.7 pricing per million tokens=0.9/0.05, DeepSeek V4=0.98/0.91, V4=0.5/0.74, DeepSeek=0.67/0.93, Qwen=0.85/0.94, million=0.31/0.15, tokens=0.37/0.38
- qa010 FAISS=0.99/0.97, Milvus=0.98/0.97
- qa011 LangChain=0.99/0.97, LlamaIndex 在 RAG 场景下=0.96/0.05, 是什么？请给出官方文档依据=0.05/0.01, LlamaIndex=0.98/0.97, RAG=0.2/0.9
- qa012 Adyen=0.97/0.96
- qa013 managed Kubernetes for a regulated fintech=0.97/0.12, Kubernetes on-prem=0.98/0.28, Kubernetes=0.3/0.97
- qa014 Brave=0.91/0.95, Search=0.34/0.87, Tavily=0.98/0.97
- qa015 the three largest cloud providers on egress=0.44/0.06, three=0.12/0.1, largest=0.15/0.08, cloud=0.19/0.61, providers=0.39/0.41, egress=0.58/0.64
- qa016 GPT-5=0.99/0.97, Claude=0.99/0.97
- qa017 Webpack build speed in 2026=0.72/0.08, Vite=0.99/0.98, Webpack=0.98/0.97
- qa018 Tesla Model 3=0.99/0.94, BYD Seal 的续航做个=0.82/0.02, 不要引用二手评测=0.05/0.02, Tesla=0.75/0.96, BYD=0.86/0.96, Seal=0.9/0.95
- qa019 Go=0.98/0.96, Zig for systems programming=0.76/0.03, Zig=0.98/0.97
- qa020 MongoDB=0.98/0.97, PostgreSQL=0.98/0.97, CMS=0.11/0.91
- qa021 Obsidian: which respects privacy more under GDPR=0.49/0.04, Notion=0.97/0.97, Obsidian=0.95/0.97, GDPR=0.05/0.64
- qa022 前者=0.97/0.58, 后者=0.97/0.64