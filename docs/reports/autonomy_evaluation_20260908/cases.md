# 自主度评测逐题附表

由摘要绑定校验后的机器结果生成；辅助分不等于事实正确率，完整交付不等于循环 succeeded。
成本为观测到的 LLM HTTP total token；标记 ≥ 表示用量仅为下界。

## 事实题：质量与交付

单元格：辅助分 /100；完整交付；核心正确性 /2（仅事实题）；循环终态。

| 题号 | r1/guided | r1/autonomous | r2/guided | r2/autonomous |
|---|---|---|---|---|
| final001 | 100.00；是；2/2；evidence_insufficient | 100.00；是；2/2；succeeded | 100.00；是；2/2；evidence_insufficient | 100.00；是；2/2；succeeded |
| final002 | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded | 100.00；是；2/2；evidence_insufficient | 66.67；是；2/2；succeeded |
| final003 | 66.67；是；2/2；succeeded | 100.00；是；2/2；succeeded | 66.67；是；2/2；succeeded | 100.00；是；2/2；succeeded |
| final004 | 66.67；是；2/2；stagnated | 66.67；是；2/2；succeeded | 100.00；是；2/2；evidence_insufficient | 66.67；是；2/2；succeeded |
| final005 | 66.67；是；2/2；stagnated | 83.33；是；2/2；succeeded | 100.00；是；2/2；evidence_insufficient | 66.67；是；2/2；succeeded |
| final006 | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded |
| final007 | 83.33；是；2/2；stagnated | 83.33；是；2/2；succeeded | 66.67；是；2/2；stagnated | 83.33；是；2/2；succeeded |
| final008 | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded |
| final009 | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded |
| final010 | 66.67；是；2/2；stagnated | 66.67；是；2/2；succeeded | 100.00；是；2/2；evidence_insufficient | 66.67；是；2/2；succeeded |
| final011 | 0.00；否；0/2；stagnated | 100.00；是；2/2；succeeded | 100.00；是；2/2；evidence_insufficient | 100.00；是；2/2；succeeded |
| final012 | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded | 66.67；是；2/2；stagnated | 66.67；是；2/2；succeeded |
| final013 | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded |
| final014 | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded | 100.00；是；2/2；succeeded | 66.67；是；2/2；succeeded |
| final015 | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded |
| final016 | 66.67；是；2/2；succeeded | 66.67；是；0/2；succeeded | 100.00；是；2/2；evidence_insufficient | 50.00；是；0/2；succeeded |
| final017 | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded | 100.00；是；2/2；evidence_insufficient | 66.67；是；2/2；succeeded |
| final018 | 66.67；是；2/2；stagnated | 66.67；是；2/2；succeeded | 100.00；是；2/2；succeeded | 66.67；是；2/2；succeeded |
| final019 | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded | 66.67；是；2/2；succeeded | 100.00；是；2/2；succeeded |
| final020 | 0.00；否；0/2；evidence_insufficient | 100.00；是；2/2；succeeded | 100.00；是；2/2；evidence_insufficient | 100.00；是；2/2；succeeded |

## 事实题：成本与耗时

单元格：已知 token；墙钟秒。

| 题号 | r1/guided | r1/autonomous | r2/guided | r2/autonomous |
|---|---|---|---|---|
| final001 | 15,079；70.6 | 2,341；59.5 | 15,739；80.9 | 2,601；13.0 |
| final002 | 5,193；33.5 | 1,401；11.4 | 21,905；241.6 | 1,737；14.3 |
| final003 | 1,839；12.3 | 1,282；5.5 | 2,195；33.5 | 1,312；7.3 |
| final004 | 4,390；20.3 | 1,340；21.3 | 11,877；110.2 | 1,428；6.6 |
| final005 | 7,740；110.6 | 1,422；6.6 | 16,474；168.0 | 1,574；7.8 |
| final006 | 1,791；9.1 | 1,385；6.1 | 1,809；7.8 | 1,455；6.4 |
| final007 | 4,250；12.3 | 1,359；6.1 | 8,722；194.3 | 1,448；6.9 |
| final008 | 1,591；37.8 | 1,321；6.3 | 1,857；10.7 | 1,372；7.0 |
| final009 | 3,349；31.8 | 1,392；8.2 | 2,822；22.1 | 1,563；9.7 |
| final010 | 4,017；17.1 | 1,606；11.4 | 14,394；110.5 | 1,794；10.6 |
| final011 | 7,395；49.7 | 3,590；113.9 | 16,022；79.8 | 2,516；17.5 |
| final012 | 2,674；15.5 | 1,832；15.9 | 8,754；75.8 | 1,792；8.3 |
| final013 | 2,113；12.2 | 1,341；5.9 | 2,449；20.0 | 1,424；10.1 |
| final014 | 4,163；85.5 | 1,373；6.8 | 9,375；80.1 | 1,530；7.2 |
| final015 | 5,196；23.7 | 1,342；6.3 | 3,151；54.6 | 1,452；8.5 |
| final016 | 7,986；84.3 | 2,962；53.0 | 16,933；119.8 | 1,913；9.7 |
| final017 | 6,559；40.0 | 1,915；10.7 | 15,287；142.3 | 2,067；17.7 |
| final018 | 6,935；46.8 | 1,369；6.3 | 11,323；119.3 | 1,619；7.2 |
| final019 | 5,024；26.6 | 1,372；5.8 | 4,909；57.8 | 4,258；29.6 |
| final020 | 22,542；158.9 | 3,425；9.6 | 63,050；428.8 | 2,085；6.1 |

## 事实题：问题索引

- `final001`：What is the speed of Earth's rotation at the equator?
- `final002`：Which moon is the largest in our solar system?
- `final003`：What is the capital of France?
- `final004`：Who wrote the novel '1984'?
- `final005`：What is the boiling point of water at sea level?
- `final006`：On what date did the United States adopt its Declaration of Independence?
- `final007`：Name the seven continents.
- `final008`：What is the chemical symbol for gold?
- `final009`：Who discovered penicillin?
- `final010`：What is the speed of light?
- `final011`：How high is Mount Everest?
- `final012`：Why is Mars called the Red Planet?
- `final013`：What is the official currency of Japan?
- `final014`：When did the Titanic sink?
- `final015`：Who painted the Mona Lisa?
- `final016`：When was Google Inc. founded?
- `final017`：What is the longest river in the world?
- `final018`：Who was the first human to walk on the Moon?
- `final019`：Which planet is the largest in the solar system?
- `final020`：When is International Men’s Day celebrated?

## 开放题：质量与交付

单元格：辅助分 /100；完整交付；核心正确性 /2（仅事实题）；循环终态。

| 题号 | r1/guided | r1/autonomous | r2/guided | r2/autonomous |
|---|---|---|---|---|
| open001 | 87.50；是；succeeded | 87.50；是；succeeded | 100.00；是；evidence_insufficient | 100.00；是；succeeded |
| open002 | 0.00；否；harness_timeout | 100.00；是；succeeded | 100.00；是；evidence_insufficient | 100.00；是；succeeded |
| open003 | 100.00；是；evidence_insufficient | 100.00；是；succeeded | 100.00；是；succeeded | 100.00；是；succeeded |
| open004 | 87.50；是；evidence_insufficient | 0.00；否；succeeded | 100.00；是；evidence_insufficient | 0.00；否；harness_timeout |
| open005 | 0.00；否；stagnated | 75.00；是；succeeded | 87.50；是；evidence_insufficient | 75.00；是；succeeded |
| open006 | 100.00；是；succeeded | 100.00；是；succeeded | 87.50；是；succeeded | 100.00；是；succeeded |
| open007 | 0.00；否；harness_timeout | 0.00；否；succeeded | 100.00；是；evidence_insufficient | 100.00；是；succeeded |
| open008 | 100.00；是；stagnated | 100.00；是；succeeded | 0.00；否；harness_timeout | 100.00；是；succeeded |
| open009 | 100.00；是；evidence_insufficient | 100.00；是；succeeded | 0.00；否；evidence_insufficient | 100.00；是；succeeded |
| open010 | 87.50；是；evidence_insufficient | 0.00；否；succeeded | 37.50；否；evidence_insufficient | 100.00；是；succeeded |
| open011 | 100.00；是；succeeded | 100.00；是；succeeded | 100.00；是；succeeded | 100.00；是；succeeded |
| open012 | 100.00；是；succeeded | 100.00；是；succeeded | 100.00；是；succeeded | 100.00；是；succeeded |
| open013 | 0.00；否；harness_timeout | 0.00；否；succeeded | 0.00；否；harness_timeout | 75.00；是；succeeded |
| open014 | 100.00；是；stagnated | 100.00；是；succeeded | 100.00；是；stagnated | 100.00；是；succeeded |
| open015 | 0.00；否；evidence_insufficient | 75.00；是；succeeded | 75.00；是；evidence_insufficient | 87.50；是；succeeded |
| open016 | 100.00；是；evidence_insufficient | 100.00；是；succeeded | 100.00；是；evidence_insufficient | 100.00；是；succeeded |
| open017 | 100.00；是；evidence_insufficient | 0.00；否；succeeded | 100.00；是；evidence_insufficient | 0.00；否；succeeded |
| open018 | 0.00；否；harness_timeout | 100.00；是；succeeded | 0.00；否；harness_timeout | 87.50；是；succeeded |
| open019 | 0.00；否；returned | 0.00；否；clarification_required | 0.00；否；returned | 87.50；是；succeeded |
| open020 | 87.50；是；evidence_insufficient | 0.00；否；succeeded | 87.50；是；evidence_insufficient | 75.00；是；succeeded |

## 开放题：成本与耗时

单元格：已知 token；墙钟秒。

| 题号 | r1/guided | r1/autonomous | r2/guided | r2/autonomous |
|---|---|---|---|---|
| open001 | 9,593；139.0 | 12,835；120.1 | 29,264；192.6 | 5,784；48.5 |
| open002 | ≥31,661；660.3 | 8,297；211.5 | 38,574；144.3 | 8,030；46.8 |
| open003 | 30,061；134.3 | 5,560；183.2 | 30,320；95.7 | 6,300；36.1 |
| open004 | 90,292；431.6 | 20,148；159.5 | 114,742；285.8 | ≥118,848；660.2 |
| open005 | 8,753；55.5 | 3,567；36.5 | 74,900；541.7 | 4,573；27.6 |
| open006 | 11,033；344.2 | 3,265；23.7 | 11,089；36.1 | 5,155；38.4 |
| open007 | ≥29,010；660.3 | 3,711；160.9 | 43,740；165.8 | 3,016；17.9 |
| open008 | 16,163；123.1 | 2,551；16.1 | ≥49,229；660.2 | 3,609；9.8 |
| open009 | 61,980；469.3 | 3,867；165.3 | 144,750；581.7 | 4,545；24.4 |
| open010 | 64,647；252.5 | 3,677；186.2 | 265,605；578.1 | 12,456；55.3 |
| open011 | 13,353；88.9 | 5,090；88.2 | 8,226；42.7 | 4,051；19.2 |
| open012 | 21,061；203.2 | 3,468；39.4 | 11,785；87.5 | 3,786；15.2 |
| open013 | ≥11,918；660.3 | 3,556；119.2 | ≥5,349；660.3 | 15,728；75.6 |
| open014 | 13,013；92.9 | 2,578；33.4 | 16,929；51.2 | 4,244；38.8 |
| open015 | 77,920；348.2 | 24,125；132.8 | 156,693；483.0 | 12,018；40.8 |
| open016 | 32,790；292.3 | 14,262；158.5 | 43,309；146.0 | 7,374；24.7 |
| open017 | 19,740；41.3 | 3,406；81.5 | 32,808；130.5 | 5,360；17.5 |
| open018 | ≥49,910；660.3 | 7,319；41.5 | ≥44,372；660.3 | 10,343；32.5 |
| open019 | 0；3.7 | 1,628；84.6 | 0；3.7 | 5,323；37.6 |
| open020 | 51,067；64.3 | 10,604；61.7 | 65,331；366.0 | 21,276；39.8 |

## 开放题：问题索引

- `open001`：Help me decide whether to use a monorepo or a polyrepo setup for a 12-person platform team. Walk me through the trade-offs and give a recommendation grounded in how real teams have chosen.
- `open002`：Survey the main approaches to distributed tracing in microservices and explain when each is a good fit.
- `open003`：I'm planning a 3-week trip to Japan in November focused on food and traditional culture. Propose a realistic itinerary region by region and explain the logic.
- `open004`：Compare the major serverless container options (e.g. AWS Fargate, Cloud Run, Azure Container Apps) for a team that needs predictable cost and cold-start tolerance. Give a worked cost intuition.
- `open005`：Explain how vector databases differ from traditional keyword search and lay out a hybrid strategy a small engineering team could adopt incrementally.
- `open006`：I'm switching from PostgreSQL to MongoDB for a content-management workload. What should I check about my access patterns and consistency needs before committing? What could go wrong?
- `open007`：Research the current state of WebGPU adoption across browsers and summarize what a frontend team should plan for in the next year.
- `open008`：We have intermittent 5xx errors in a microservice. Give me a structured debugging methodology I can follow, with the most likely causes to check first.
- `open009`：Help me write a design doc outline for adding rate limiting to a public API. What sections do I need and what decisions should each section force?
- `open010`：Compare on-prem Kubernetes vs managed Kubernetes for a regulated fintech. What are the hidden costs and compliance angles people underestimate?
- `open011`：I need to evaluate three open-source workflow engines for a data pipeline. Propose the evaluation criteria I should score them on, and why each criterion matters.
- `open012`：Explain the trade-offs of event-driven architecture versus request-driven for an e-commerce checkout flow, and recommend where to draw the boundary.
- `open013`：Survey how major cloud providers handle secret rotation and what a cross-cloud abstraction would need to account for.
- `open014`：I'm a new engineering manager inheriting a legacy codebase with no tests. Propose a 90-day plan to build confidence without freezing feature work.
- `open015`：Research the main approaches to cost optimization for LLM inference in production and rank them by effort vs. payoff.
- `open016`：Compare strong vs. eventual consistency for a collaborative document editor and explain how CRDTs and operational transform fit in.
- `open017`：I'm choosing between Stripe and building a custom billing system. Lay out the decision factors and the scenarios where building wins.
- `open018`：Help me understand the current options for observability in serverless functions and recommend a starter stack for a solo developer.
- `open019`：Explain how feature flags should be architected for a system that deploys 20+ times a day, including the failure modes to guard against.
- `open020`：Research the state of typed Python async web frameworks and advise which a team should pick if developer experience and type-safety are the top priorities.
