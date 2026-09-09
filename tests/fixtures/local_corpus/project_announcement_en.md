# Project Announcement: The Intelligent Search Engine

## 1. Introduction & Vision

Welcome! This semester's capstone project for the ML for NLP program is Project ISE, a 10-week challenge to design and implement a next-generation intelligent search engine.

In the age of large models, information retrieval is undergoing a paradigm shift. Simple keyword matching is no longer sufficient. The future of search lies in systems that can deeply understand user intent, dynamically interact with diverse data sources, and orchestrate complex workflows to deliver precise, context-aware answers. The goal of Project ISE is to build such a system.

Your mission is to create a search engine that doesn't just find key word matching results, but synthesizes information. It can act as an intelligent agent, capable of automatically selecting the right tools for a query, processing and reranking information from multiple modalities, and executing complex tasks. This project will challenge you to apply the concepts learned in this course to a practical, cutting-edge application in NLP and AI.

## 2. Core Project Features

You will work in teams to implement a system with the following core capabilities:

- **Intelligent Source Selection**: Given a user query, the system must automatically determine the most appropriate data source(s) to consult. This could range from web search APIs, specialized databases, or a comprehensive local knowledge base.
- **Local RAG Implementation**: A key component will be building and maintaining a robust local knowledge base (Retrieval-Augmented Generation). You will need to process and index a provided set of documents to ensure the system can retrieve and synthesize information that is not available on the public web.
- **Advanced Reranking and Filtering**: Your system must go beyond standard relevance scores. It should implement sophisticated reranking algorithms that consider context, source credibility, and freshness to present the most relevant and reliable results to the user.
- **Dynamic Workflow Automation**: The engine should be able to execute multi-step "workflows" to answer complex queries. For example, a query like "What was the impact of the latest NVIDIA earnings report on their stock price and how does it compare to AMD's?" would require fetching financial reports, stock data, and news articles, and then synthesizing a summary.
- **Multimodal Support**: Users should be able to upload files (e.g., PDFs, images, code snippets) as part of their query. Your system must be able to parse this multimodal input and use it to inform the search process.
- **Domain-Specific Intelligence**: The system should have specialized capabilities for various domains. You will be tasked with implementing intelligent search including but not limited to the following areas:
  - **Weather**: Answer queries about weather forcasts
  - **Transportation**: Answer queries about travel times, and logistics.
  - **Finance**: Retrieve stock or crypto data.

## 3. Technical Framework

Your teams can use large model APIs (e.g., DeepSeek) to handle core NLP tasks. Beyond that, you have the freedom to choose your technology stack for building the surrounding architecture, including databases, backend frameworks, and reranking models.

## 4. Project Timeline & Evaluation

This is a 10-week project, structured to encourage iterative development and continuous improvement.

- **Week 4**: Project Kick-off and Team Formation (due this Friday).
- **Week 4**: Release of Test Set 1.
- **Week 7**: Release of Test Set 2.
- **Week 9**: Mid-term Progress Report Due.
- **Week 10**: Release of Test Set 3.
- **Week 13**: Final Project Submission and Demonstration.

To help you gauge your progress, we will release evaluation test sets periodically. These test sets will contain a variety of queries designed to test the core features of your system. This allows for a continuous, low-stakes self-evaluation process.

**Evaluation Criteria**: Your final submission will be evaluated on the successful implementation of the core features, with a strong emphasis on two key performance metrics:

- **Accuracy**: The relevance and correctness of the returned results.
- **Search Time**: The overall latency of the system, from query submission to result presentation. You must comprehensively optimize your system for both speed and accuracy.

No Web UI is needed. You can just run the system in command lines. However, if you want to implement the web UI, it is extremely good.

## 5. Team Formation

You will form teams of 3-4 members. Teamwork and collaboration are essential for a project of this scale. Please finalize and submit your team registrations by this Friday.

## 6. Getting Started

- Form your team and register it by this Friday.
- Begin brainstorming the high-level architecture of your system.

This project is a marathon, not a sprint. Plan your work, divide responsibilities, and build incrementally. We are excited to see the innovative solutions you will create.

Good luck!
