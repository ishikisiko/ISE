# analysis  (65/65 ok)
latency ms p50 305 p95 517 max 769 (concurrency 4)

## claim_classes micro baseline: P 0.875 R 0.651 F1 0.747   per-class F1: comparison 0.792, numeric 0.645, pricing 0.8, current 0.875, compliance 0.0, temporal 0.632, historical 0.889
## claim_classes micro jev t=0.5: P 0.84 R 0.919 F1 0.878   per-class F1: comparison 0.947, numeric 0.762, pricing 0.968, current 0.737, compliance 1.0, temporal 0.824, historical 1.0
## claim_classes micro jev t=0.7: P 0.882 R 0.872 F1 0.877   per-class F1: comparison 0.909, numeric 0.737, pricing 1.0, current 0.824, compliance 1.0, temporal 0.824, historical 1.0
jev t=0.5 claim errors:
- qa012 gold=['comparison', 'numeric', 'pricing'] jev=['comparison', 'pricing'] base=[]: Stripe 和 Adyen 的手续费谁更低
- qa015 gold=['comparison', 'numeric', 'pricing'] jev=['comparison', 'pricing'] base=['comparison', 'numeric', 'pricing']: Compare the three largest cloud providers on egress pricing
- qa017 gold=['comparison', 'temporal'] jev=['comparison', 'current', 'temporal'] base=['comparison']: Vite versus Webpack build speed in 2026
- qa024 gold=['historical', 'temporal'] jev=['historical', 'numeric', 'temporal'] base=['historical', 'temporal']: 最近三年 AI 芯片出货量的变化趋势
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
  - qa056 gold=0 p=0.97: 这个项目的核心模块有哪些
  - qa058 gold=0 p=0.63: 我上传的文件里有没有提到 XXX
  - qa059 gold=0 p=0.59: Summarize the uploaded PDF in three bullet points
## existence baseline: P 0.6 R 0.75 F1 0.667  (tp 3 fp 2 fn 1)
## existence jev t=0.5: P 0.5 R 0.75 F1 0.6  (tp 3 fp 3 fn 1)
  - qa029 gold=0 p=0.9: 过去一周 GitHub Copilot 有哪些更新
  - qa033 gold=0 p=0.58: What changed in React 19 compared with React 18?
  - qa038 gold=0 p=0.97: 历年诺贝尔物理学奖得主名单
  - qa058 gold=1 p=0.08: 我上传的文件里有没有提到 XXX
## time_scope accuracy baseline 0.908  jev 0.969
  - qa029 gold=window jev=recent conf=0.99 base=window: 过去一周 GitHub Copilot 有哪些更新
  - qa054 gold=none jev=recent conf=0.99 base=recent: How are you doing today?
## comparison members baseline: P 0.867 R 0.605 F1 0.712 noise_member_rate 0.133 (4/30)
## comparison members jev-filtered t=0.5: P 0.707 R 0.953 F1 0.812 noise_member_rate 0.086 (5/58)
  - qa002 gold=['AWS Fargate', 'Cloud Run'] pred=['AWS Fargate', 'Cloud Run for cold-start tolerance', 'Fargate', 'Run'] noise=['Run']
  - qa012 gold=['Stripe', 'Adyen'] pred=['Adyen'] noise=[]
  - qa015 gold=[] pred=['egress'] noise=['egress']
  - qa019 gold=['Rust', 'Go', 'Zig'] pred=['Go', 'Zig for systems programming', 'Zig'] noise=['Zig for systems programming']
  - qa022 gold=[] pred=['前者', '后者'] noise=['前者', '后者']
## comparison members jev-filtered t=0.7: P 0.774 R 0.953 F1 0.854 noise_member_rate 0.057 (3/53)
  - qa012 gold=['Stripe', 'Adyen'] pred=['Adyen'] noise=[]
  - qa019 gold=['Rust', 'Go', 'Zig'] pred=['Go', 'Zig for systems programming', 'Zig'] noise=['Zig for systems programming']
  - qa022 gold=[] pred=['前者', '后者'] noise=['前者', '后者']

candidate probabilities (comparison rows):
- qa001 {'GLM-5.2': 0.98, 'K2.7': 0.87, 'Kimi': 0.36}
- qa002 {'AWS Fargate': 0.98, 'Cloud Run for cold-start tolerance': 0.9, 'AWS': 0.26, 'Fargate': 0.93, 'Cloud': 0.23, 'Run': 0.54}
- qa003 {'Redis': 0.99, 'Milvus 有什么': 0.82, '注意不要只看官方宣传要抓到实际差异': 0.11, 'Milvus': 0.98}
- qa004 {'一下 PostgreSQL': 0.69, 'MySQL': 0.99, 'SQLite 的适用场景': 0.93, 'PostgreSQL': 0.98, 'SQLite': 0.95}
- qa005 {'苹果': 0.99, '微软': 0.99}
- qa006 {'Tavily': 0.99, 'Firecrawl': 0.99, 'BrightData': 0.99}
- qa007 {'iPhone 17': 0.96, 'Pixel 10 的相机': 0.97, 'Pixel': 0.31}
- qa008 {'Fable': 0.95, 'Opus': 0.97}
- qa009 {'Qwen 3.7 pricing per million tokens': 0.88, 'DeepSeek V4': 0.98, 'V4': 0.53, 'DeepSeek': 0.69, 'Qwen': 0.85, 'million': 0.29, 'tokens': 0.34}
- qa010 {'FAISS': 0.99, 'Milvus': 0.99}
- qa011 {'LangChain': 0.99, 'LlamaIndex 在 RAG 场景下': 0.96, '是什么？请给出官方文档依据': 0.05, 'LlamaIndex': 0.98, 'RAG': 0.2}
- qa012 {'Adyen': 0.97}
- qa013 {'managed Kubernetes for a regulated fintech': 0.97, 'Kubernetes on-prem': 0.98, 'Kubernetes': 0.34}
- qa014 {'Brave': 0.94, 'Search': 0.27, 'Tavily': 0.98}
- qa015 {'the three largest cloud providers on egress': 0.43, 'three': 0.17, 'largest': 0.17, 'cloud': 0.19, 'providers': 0.44, 'egress': 0.58}
- qa016 {'GPT-5': 0.98, 'Claude': 0.99}
- qa017 {'Webpack build speed in 2026': 0.79, 'Vite': 0.99, 'Webpack': 0.98}
- qa018 {'Tesla Model 3': 0.99, 'BYD Seal 的续航做个': 0.82, '不要引用二手评测': 0.05, 'Tesla': 0.75, 'BYD': 0.88, 'Seal': 0.9}
- qa019 {'Go': 0.99, 'Zig for systems programming': 0.8, 'Zig': 0.98}
- qa020 {'MongoDB': 0.98, 'PostgreSQL': 0.98, 'CMS': 0.09}
- qa021 {'Obsidian: which respects privacy more under GDPR': 0.47, 'Notion': 0.97, 'Obsidian': 0.96, 'GDPR': 0.05}
- qa022 {'前者': 0.97, '后者': 0.97}