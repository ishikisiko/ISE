/* ISE · 智能检索引擎 — 前端逻辑
 * 原生 ES2015，无构建步骤。通过 SSE 实时渲染 Agent 工作流步骤。
 */
"use strict";

document.addEventListener("DOMContentLoaded", () => {
    // ------------------------------------------------------------------
    // DOM 引用
    // ------------------------------------------------------------------
    const thread = document.getElementById("thread");
    const composer = document.getElementById("composer");
    const queryInput = document.getElementById("query");
    const sendBtn = document.getElementById("send-btn");
    const attachBtn = document.getElementById("attach-btn");
    const fileInput = document.getElementById("file-input");
    const chipRow = document.getElementById("chip-row");
    const searchPill = document.getElementById("search-pill");
    const modelSelect = document.getElementById("model");
    const settingsBtn = document.getElementById("settings-btn");
    const settingsPanel = document.getElementById("settings-panel");
    const statusLine = document.getElementById("status-line");
    const topbarModel = document.getElementById("topbar-model");
    const sidebar = document.getElementById("sidebar");
    const sidebarToggle = document.getElementById("sidebar-toggle");
    const sidebarClose = document.getElementById("sidebar-close");
    const newConvBtn = document.getElementById("new-conv-btn");
    const convList = document.getElementById("conv-list");
    const convEmpty = document.getElementById("conv-empty");
    const forceSearchInput = document.getElementById("force-search");
    const limitTotal = document.getElementById("limit-total");
    const limitPerSource = document.getElementById("limit-per-source");
    const limitReference = document.getElementById("limit-reference");
    const searchDepthSelect = document.getElementById("search-depth");
    const sourceCheckboxes = Array.from(
        document.querySelectorAll('#source-chips input[type="checkbox"]')
    );
    const timingCheckboxes = Array.from(
        document.querySelectorAll('#timing-chips input[type="checkbox"]')
    );

    // ------------------------------------------------------------------
    // 设置持久化
    // ------------------------------------------------------------------
    const SETTINGS_KEY = "ise.settings.v1";
    const CONVERSATION_ID_KEY = "ise.conversation.v1";
    const SIDEBAR_KEY = "ise.sidebar.v1";

    function newConversationId() {
        return (
            Date.now().toString(36) + "-" +
            Math.random().toString(36).slice(2, 10)
        );
    }

    function getConversationId() {
        let id = "";
        try {
            id = localStorage.getItem(CONVERSATION_ID_KEY) || "";
        } catch {
            id = "";
        }
        if (!id) {
            id = newConversationId();
            try {
                localStorage.setItem(CONVERSATION_ID_KEY, id);
            } catch {
                /* localStorage 不可用时静默 */
            }
        }
        return id;
    }

    function resetConversation() {
        const id = newConversationId();
        try {
            localStorage.setItem(CONVERSATION_ID_KEY, id);
        } catch {
            /* localStorage 不可用时静默 */
        }
        return id;
    }

    let conversationId = getConversationId();
    const DEFAULT_SETTINGS = {
        search: true,
        sources: ["brave", "firecrawl", "tavily", "parallel", "brightdata", "google"],
        forceSearch: false,
        limits: { total: 5, perSource: 5, reference: 5 },
        searchDepth: "auto",
        timing: ["total", "search", "llm", "tools"],
        model: "",
    };

    function loadSettings() {
        try {
            const raw = localStorage.getItem(SETTINGS_KEY);
            if (!raw) return { ...DEFAULT_SETTINGS };
            const parsed = JSON.parse(raw);
            const merged = {
                ...DEFAULT_SETTINGS,
                ...parsed,
                limits: { ...DEFAULT_SETTINGS.limits, ...(parsed.limits || {}) },
            };
            merged.sources = (Array.isArray(merged.sources) ? merged.sources : []).filter((s) =>
                DEFAULT_SETTINGS.sources.includes(s)
            );
            if (!merged.sources.length) merged.sources = [...DEFAULT_SETTINGS.sources];
            if (!["auto", "basic", "advanced", "fast", "ultra-fast"].includes(merged.searchDepth)) {
                merged.searchDepth = DEFAULT_SETTINGS.searchDepth;
            }
            return merged;
        } catch {
            return { ...DEFAULT_SETTINGS };
        }
    }

    function saveSettings() {
        try {
            localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
        } catch {
            /* localStorage 不可用时静默 */
        }
    }

    const settings = loadSettings();

    const state = {
        loading: false,
        images: [],
        turnCount: 0,
        conversations: [],
        activeConversationId: "",
    };

    // ------------------------------------------------------------------
    // 基础工具
    // ------------------------------------------------------------------
    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    function setStatus(text, isError = false) {
        statusLine.textContent = text || "";
        statusLine.classList.toggle("is-error", Boolean(isError));
    }

    function fmtMs(ms) {
        if (ms === null || ms === undefined || Number.isNaN(Number(ms))) return "";
        const value = Number(ms);
        if (value < 1) return "<1 ms";
        if (value < 1000) return `${Math.round(value)} ms`;
        return `${(value / 1000).toFixed(1)} s`;
    }

    function snippet(text, limit = 220) {
        if (!text) return "";
        const clean = String(text).replace(/\s+/g, " ").trim();
        return clean.length <= limit ? clean : `${clean.slice(0, limit - 1)}…`;
    }

    function domainOf(url) {
        try {
            return new URL(url).hostname.replace(/^www\./, "");
        } catch {
            return "";
        }
    }

    // ------------------------------------------------------------------
    // 迷你 Markdown 渲染（安全子集：先转义再替换）
    // ------------------------------------------------------------------
    function escapeHTML(str) {
        return String(str || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    }

    function renderMarkdown(md) {
        if (!md) return "";
        let src = escapeHTML(md);

        src = src.replace(/```(\w*)\n?([\s\S]*?)```/g, (m, lang, code) => {
            const language = lang ? lang.toLowerCase() : "none";
            return `<pre><code class="language-${language}">${code.replace(/\n$/, "")}</code></pre>`;
        });
        src = src.replace(/`([^`\n]+)`/g, (m, code) => `<code>${code}</code>`);
        src = src.replace(/\*\*([^*]+)\*\*/g, (m, t) => `<strong>${t}</strong>`);
        src = src.replace(/(^|\s)\*([^*\n]+)\*(?=\s|$|[,.;:!?])/g, (m, pre, t) => `${pre}<em>${t}</em>`);
        src = src.replace(/\[([^\]]+)\]\(((?:https?:\/\/)[^\s)]+)\)/g, (m, text, url) =>
            `<a href="${url}" target="_blank" rel="noopener noreferrer">${text}</a>`
        );
        src = src.replace(/^######\s+(.+)$/gm, "<h3>$1</h3>");
        src = src.replace(/^#####\s+(.+)$/gm, "<h3>$1</h3>");
        src = src.replace(/^####\s+(.+)$/gm, "<h3>$1</h3>");
        src = src.replace(/^###\s+(.+)$/gm, "<h3>$1</h3>");
        src = src.replace(/^##\s+(.+)$/gm, "<h3>$1</h3>");
        src = src.replace(/^#\s+(.+)$/gm, "<h3>$1</h3>");
        src = src.replace(/^---+$/gm, "<hr>");
        src = src.replace(/^&gt;\s?(.*)$/gm, "<blockquote>$1</blockquote>");

        src = src.replace(/((?:^\d+[.、]\s.+(?:\n|$))+)/gm, (block) => {
            const items = block.trim().split(/\n/)
                .map((line) => line.replace(/^\d+[.、]\s+/, "").trim())
                .filter(Boolean);
            return `<ol>${items.map((it) => `<li>${it}</li>`).join("")}</ol>`;
        });
        src = src.replace(/((?:^\s*[-*•]\s.+(?:\n|$))+)/gm, (block) => {
            const items = block.trim().split(/\n/)
                .map((line) => line.replace(/^\s*[-*•]\s+/, "").trim())
                .filter(Boolean);
            return `<ul>${items.map((it) => `<li>${it}</li>`).join("")}</ul>`;
        });

        const parts = src.split(/\n{2,}/).map((p) => {
            if (/^\s*<(h3|ul|ol|pre|hr|blockquote)/.test(p)) return p;
            return `<p>${p.replace(/\n/g, "<br>")}</p>`;
        });
        return parts.join("");
    }

    function highlightIn(container) {
        if (window.Prism && typeof window.Prism.highlightAllUnder === "function") {
            window.Prism.highlightAllUnder(container);
        }
    }

    // ------------------------------------------------------------------
    // 空态与建议问题
    // ------------------------------------------------------------------
    const SUGGESTIONS = [
        "检索增强生成（RAG）相比微调有什么优势？",
        "香港未来三天的天气怎么样？",
        "英伟达最新的股价和市值是多少？",
        "上传一份文档，然后让我总结要点",
    ];

    function renderEmpty() {
        if (state.turnCount > 0) return;
        thread.innerHTML = "";
        const empty = el("div", "empty");
        empty.appendChild(el("h1", "empty-title", "有什么可以帮你？"));
        empty.appendChild(
            el("p", "empty-sub", "联网检索、本地文档与领域数据融合，逐步展示推理过程。")
        );
        const grid = el("div", "suggestions");
        for (const text of SUGGESTIONS) {
            const card = el("button", "suggestion", text);
            card.type = "button";
            card.addEventListener("click", () => {
                queryInput.value = text;
                autosize();
                queryInput.focus();
            });
            grid.appendChild(card);
        }
        empty.appendChild(grid);
        thread.appendChild(empty);
    }

    function clearEmpty() {
        const empty = thread.querySelector(".empty");
        if (empty) empty.remove();
    }

    // ------------------------------------------------------------------
    // 会话管理（侧栏）
    // ------------------------------------------------------------------
    function fmtRelative(iso) {
        if (!iso) return "";
        const date = new Date(iso.replace(" ", "T") + "Z");
        if (Number.isNaN(date.getTime())) return "";
        const diff = Date.now() - date.getTime();
        const min = Math.floor(diff / 60000);
        if (min < 1) return "刚刚";
        if (min < 60) return `${min} 分钟前`;
        const hr = Math.floor(min / 60);
        if (hr < 24) return `${hr} 小时前`;
        const day = Math.floor(hr / 24);
        if (day < 7) return `${day} 天前`;
        return date.toLocaleDateString();
    }

    function setSidebarOpen(open) {
        sidebar.classList.toggle("is-hidden", !open);
        try {
            localStorage.setItem(SIDEBAR_KEY, open ? "1" : "0");
        } catch {
            /* localStorage 不可用时静默 */
        }
    }

    function isSidebarOpen() {
        try {
            return localStorage.getItem(SIDEBAR_KEY) !== "0";
        } catch {
            return true;
        }
    }

    function setActiveConversation(id) {
        state.activeConversationId = id || "";
        for (const item of convList.querySelectorAll(".conv-item")) {
            item.classList.toggle(
                "is-active",
                item.dataset.id === state.activeConversationId
            );
        }
    }

    function renderConversationList() {
        convList.innerHTML = "";
        const items = state.conversations;
        if (!items.length) {
            convEmpty.hidden = false;
            return;
        }
        convEmpty.hidden = true;

        for (const conv of items) {
            const item = el("div", "conv-item");
            item.dataset.id = conv.conversation_id;
            item.setAttribute("role", "listitem");
            if (conv.conversation_id === state.activeConversationId) {
                item.classList.add("is-active");
            }

            const main = el("div", "conv-item-main");
            const title = el("span", "conv-item-title", conv.title || "新会话");
            title.title = conv.title || "";
            const meta = el("span", "conv-item-meta",
                `${fmtRelative(conv.last_activity) || conv.last_activity || ""} · ${conv.turn_count} 轮`);
            main.append(title, meta);

            const actions = el("div", "conv-actions");
            const rename = el("button", "conv-act", "");
            rename.type = "button";
            rename.title = "重命名";
            rename.setAttribute("aria-label", "重命名会话");
            rename.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 013 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>';
            const del = el("button", "conv-act is-delete", "");
            del.type = "button";
            del.title = "删除";
            del.setAttribute("aria-label", "删除会话");
            del.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M8 6V4a1 1 0 011-1h6a1 1 0 011 1v2M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/></svg>';
            actions.append(rename, del);

            item.append(main, actions);
            convList.appendChild(item);

            item.addEventListener("click", (event) => {
                if (event.target.closest(".conv-actions")) return;
                if (state.loading) return;
                selectConversation(conv.conversation_id);
            });
            rename.addEventListener("click", (event) => {
                event.stopPropagation();
                renameConversation(conv.conversation_id, conv.title);
            });
            del.addEventListener("click", async (event) => {
                event.stopPropagation();
                if (!confirm(`删除会话「${conv.title || "新会话"}」？`)) return;
                await deleteConversation(conv.conversation_id);
            });
        }
    }

    async function loadConversationList() {
        try {
            const response = await fetch("/api/conversations");
            if (!response.ok) throw new Error("list");
            const data = await response.json();
            state.conversations = Array.isArray(data.conversations) ? data.conversations : [];
        } catch {
            state.conversations = [];
        }
        renderConversationList();
    }

    function renderRestoredTurns(turns, title) {
        thread.innerHTML = "";
        state.turnCount = 0;
        clearEmpty();
        if (!Array.isArray(turns) || !turns.length) {
            renderEmpty();
            return;
        }
        for (const turn of turns) {
            const result = turn && turn.result && typeof turn.result === "object" ? turn.result : null;
            const refs = appendTurn(turn.query || "", []);
            if (result) {
                // Full restore: synthesize workflow steps and render sources,
                // docs, notes and meta exactly like a live answer.
                for (const step of synthesizeSteps(result)) refs.workflow.apply(step);
                renderResult(refs, result);
            } else {
                // Legacy turns recorded before full-result persistence.
                refs.workflow.el.remove();
                const answerText = (turn.answer || "").trim() || "（未保存回答内容）";
                const answerEl = el("div", "answer");
                answerEl.innerHTML = renderMarkdown(answerText);
                highlightIn(answerEl);
                refs.turn.appendChild(answerEl);
            }
        }
        window.scrollTo({ top: 0, behavior: "smooth" });
        // suppress unused-title linting for restored view
        void title;
    }

    async function selectConversation(id) {
        if (!id) return;
        setLoading(true);
        setStatus("正在加载会话…");
        try {
            const response = await fetch(`/api/conversations/${encodeURIComponent(id)}`);
            if (!response.ok) throw new Error("load");
            const data = await response.json();
            conversationId = id;
            try {
                localStorage.setItem(CONVERSATION_ID_KEY, id);
            } catch {
                /* localStorage 不可用时静默 */
            }
            setActiveConversation(id);
            renderRestoredTurns(data.turns, data.title);
            setStatus("已恢复会话");
        } catch {
            setStatus("加载会话失败", true);
        } finally {
            setLoading(false);
            queryInput.focus();
        }
    }

    function startNewConversation() {
        if (state.loading) return;
        conversationId = resetConversation();
        try {
            localStorage.setItem(CONVERSATION_ID_KEY, conversationId);
        } catch {
            /* localStorage 不可用时静默 */
        }
        setActiveConversation("");
        thread.innerHTML = "";
        state.turnCount = 0;
        renderEmpty();
        setStatus("已开启新会话");
        queryInput.focus();
    }

    async function deleteConversation(id) {
        try {
            const response = await fetch(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
            if (!response.ok) throw new Error("delete");
        } catch {
            setStatus("删除会话失败", true);
            return;
        }
        if (id === state.activeConversationId || id === conversationId) {
            startNewConversation();
        }
        await loadConversationList();
        setStatus("会话已删除");
    }

    async function renameConversation(id, currentTitle) {
        const next = window.prompt("重命名会话", currentTitle || "");
        if (next === null) return;
        const trimmed = next.trim();
        try {
            const response = await fetch(
                `/api/conversations/${encodeURIComponent(id)}/title`,
                {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ title: trimmed }),
                }
            );
            if (!response.ok) throw new Error("rename");
        } catch {
            setStatus("重命名失败", true);
            return;
        }
        await loadConversationList();
        setStatus(trimmed ? "会话已重命名" : "已恢复默认标题");
    }

    // ------------------------------------------------------------------
    // 工作流视图
    // ------------------------------------------------------------------
    function createWorkflow() {
        const root = el("div", "workflow");

        const summary = el("button", "wf-summary");
        summary.type = "button";
        summary.setAttribute("aria-expanded", "true");
        const chevron = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        chevron.setAttribute("viewBox", "0 0 24 24");
        chevron.setAttribute("width", "12");
        chevron.setAttribute("height", "12");
        chevron.setAttribute("fill", "none");
        chevron.setAttribute("stroke", "currentColor");
        chevron.setAttribute("stroke-width", "2.4");
        chevron.setAttribute("stroke-linecap", "round");
        chevron.setAttribute("stroke-linejoin", "round");
        chevron.innerHTML = '<path d="M6 9l6 6 6-6"/>';
        const summaryText = el("span", "wf-summary-text", "正在执行工作流");
        const summaryTime = el("span", "wf-summary-time", "");
        summary.append(chevron, summaryText, summaryTime);

        const list = el("ol", "wf-list");
        root.append(summary, list);

        const steps = new Map();
        let ticker = null;
        let activeId = null;
        let startedAt = performance.now();
        let finished = false;

        function tick() {
            const elapsed = performance.now() - startedAt;
            summaryTime.textContent = fmtMs(elapsed);
            if (activeId && steps.has(activeId)) {
                const step = steps.get(activeId);
                step.timeEl.textContent = fmtMs(performance.now() - step.startTs);
            }
        }

        function ensureTicker() {
            if (ticker === null) {
                ticker = window.setInterval(tick, 100);
            }
        }

        function stopTicker() {
            if (ticker !== null) {
                window.clearInterval(ticker);
                ticker = null;
            }
        }

        summary.addEventListener("click", () => {
            const collapsed = root.classList.toggle("is-collapsed");
            summary.setAttribute("aria-expanded", collapsed ? "false" : "true");
        });

        return {
            el: root,

            apply(step) {
                if (!step || !step.id) return;
                let node = steps.get(step.id);
                if (!node) {
                    const item = el("li", "wf-step");
                    const dot = el("span", "wf-dot");
                    dot.setAttribute("aria-hidden", "true");
                    const body = el("div", "wf-body");
                    const head = el("div", "wf-head");
                    const titleGroup = el("span", "wf-title-group");
                    const title = el("span", "wf-title", step.title || step.id);
                    const badge = el("span", "wf-badge");
                    badge.hidden = true;
                    titleGroup.append(title, badge);
                    const time = el("span", "wf-time", "");
                    head.append(titleGroup, time);
                    const detail = el("p", "wf-detail");
                    detail.hidden = true;
                    const items = el("ul", "wf-items");
                    items.hidden = true;
                    const records = el("details", "wf-records");
                    records.hidden = true;
                    const recordSummary = el("summary", "wf-record-summary");
                    const recordList = el("div", "wf-record-list");
                    records.append(recordSummary, recordList);
                    body.append(head, detail, items, records);
                    item.append(dot, body);
                    node = {
                        root: item,
                        titleEl: title,
                        badgeEl: badge,
                        timeEl: time,
                        detailEl: detail,
                        itemsEl: items,
                        recordsEl: records,
                        recordSummaryEl: recordSummary,
                        recordListEl: recordList,
                        startTs: 0,
                    };
                    steps.set(step.id, node);
                    list.appendChild(item);
                }

                if (step.title) node.titleEl.textContent = step.title;

                if (step.badge && step.badge.text) {
                    node.badgeEl.textContent = step.badge.text;
                    node.badgeEl.className = `wf-badge${step.badge.tone ? ` is-${step.badge.tone}` : ""}`;
                    node.badgeEl.hidden = false;
                } else {
                    node.badgeEl.hidden = true;
                }

                node.root.classList.remove("is-active", "is-done", "is-error", "is-skipped");
                const status = step.status || "done";
                node.root.classList.add(`is-${status}`);

                if (status === "active") {
                    if (activeId && steps.has(activeId) && activeId !== step.id) {
                        const prev = steps.get(activeId);
                        prev.root.classList.remove("is-active");
                        prev.root.classList.add("is-done");
                    }
                    activeId = step.id;
                    node.startTs = performance.now();
                    node.timeEl.textContent = "";
                    ensureTicker();
                } else {
                    if (activeId === step.id) activeId = null;
                    node.timeEl.textContent = fmtMs(step.duration_ms);
                }

                if (step.detail) {
                    node.detailEl.textContent = step.detail;
                    node.detailEl.hidden = false;
                }

                if (Array.isArray(step.items) && step.items.length) {
                    node.itemsEl.innerHTML = "";
                    for (const entry of step.items) {
                        const row = el("li");
                        row.append(
                            el("span", "wf-item-label", entry.label || ""),
                            el("span", "wf-item-value", entry.value || "")
                        );
                        node.itemsEl.appendChild(row);
                    }
                    node.itemsEl.hidden = false;
                }

                const hasRecordGroup = step.record_kind || Array.isArray(step.records);
                if (hasRecordGroup) {
                    const kind = step.record_kind || "search_results";
                    const records = Array.isArray(step.records) ? step.records : [];
                    const defaultLabel = kind === "extracted_pages" ? "已抽取网页" : "搜索结果";
                    node.recordSummaryEl.textContent = step.record_label || `${defaultLabel} · ${records.length}`;
                    node.recordListEl.innerHTML = "";

                    if (!records.length) {
                        node.recordListEl.appendChild(el("p", "wf-record-empty", "没有可展示的返回条目"));
                    }

                    records.forEach((record, index) => {
                        const item = el("div", "wf-record");
                        const url = record && record.url ? String(record.url) : "";
                        const titleText = (record && record.title)
                            ? String(record.title)
                            : domainOf(url) || `条目 ${index + 1}`;
                        const title = document.createElement(url ? "a" : "span");
                        title.className = "wf-record-title";
                        title.textContent = titleText;
                        if (url) {
                            title.href = url;
                            title.target = "_blank";
                            title.rel = "noopener noreferrer";
                        }
                        const meta = el("div", "wf-record-meta");
                        const source = (record && record.provider) ? String(record.provider) : domainOf(url);
                        if (source) meta.appendChild(el("span", null, source));
                        if (record && record.status === "error") {
                            meta.appendChild(el("span", "wf-record-status is-error", "失败"));
                        } else if (kind === "extracted_pages" && record && record.content_chars !== undefined) {
                            const contentChars = Number(record.content_chars);
                            if (Number.isFinite(contentChars) && contentChars >= 0) {
                                meta.appendChild(el("span", "wf-record-status", `已抽取 ${contentChars.toLocaleString()} 字符`));
                            }
                        }
                        item.append(title, meta);

                        const detail = record && (record.snippet || record.reason || record.detail);
                        if (detail) item.appendChild(el("p", "wf-record-detail", String(detail)));
                        node.recordListEl.appendChild(item);
                    });
                    node.recordsEl.hidden = false;
                }
            },

            finalize(totalMs) {
                if (finished) return;
                finished = true;
                stopTicker();
                activeId = null;
                const doneCount = Array.from(steps.values()).filter((n) =>
                    n.root.classList.contains("is-done")
                ).length;
                const total = totalMs !== undefined && totalMs !== null
                    ? Number(totalMs)
                    : performance.now() - startedAt;
                summaryText.textContent = `已完成 ${doneCount} 步 · ${fmtMs(total)}`;
                summaryTime.textContent = "";
                root.classList.add("is-collapsed");
                summary.setAttribute("aria-expanded", "false");
            },

            fail() {
                stopTicker();
                if (activeId && steps.has(activeId)) {
                    const node = steps.get(activeId);
                    node.root.classList.remove("is-active");
                    node.root.classList.add("is-error");
                }
                activeId = null;
                summaryText.textContent = "工作流中断";
                summaryTime.textContent = "";
            },
        };
    }

    // ------------------------------------------------------------------
    // Turn 结构
    // ------------------------------------------------------------------
    function appendTurn(query, images) {
        clearEmpty();
        state.turnCount += 1;

        const turn = el("section", "turn");
        turn.appendChild(el("h2", "q-title", query));

        if (images && images.length) {
            const strip = el("div", "q-attachments");
            for (const img of images) {
                const thumb = document.createElement("img");
                thumb.src = img.base64;
                thumb.alt = img.name || "图片";
                strip.appendChild(thumb);
            }
            turn.appendChild(strip);
        }

        const workflow = createWorkflow();
        turn.appendChild(workflow.el);

        thread.appendChild(turn);
        turn.scrollIntoView({ behavior: "smooth", block: "start" });
        return { turn, workflow };
    }

    // ------------------------------------------------------------------
    // 结果渲染
    // ------------------------------------------------------------------
    const MODE_LABELS = {
        search: "联网检索",
        local_rag: "本地检索",
        direct_llm: "直接回答",
        small_talk: "闲聊",
        domain_api: "领域接口",
        image_content_present: "图片理解",
        search_unavailable: "本地回退",
        react_fallback: "深度检索",
        react_agent: "ReAct",
    };

    function renderSources(turn, hits) {
        if (!Array.isArray(hits) || !hits.length) return;
        const details = el("details", "sources");
        details.open = true;
        const summaryEl = el("summary");
        summaryEl.appendChild(el("span", "section-label", `来源 · ${hits.length}`));
        details.appendChild(summaryEl);

        const list = el("div", "source-list");
        hits.forEach((hit, index) => {
            const url = hit.url || "";
            const domain = domainOf(url) || "来源";
            const titleText = (hit.title && String(hit.title).trim())
                ? String(hit.title).trim()
                : domain;

            const row = el("div", "source-row");
            row.appendChild(el("span", "src-index", String(index + 1).padStart(2, "0")));

            const body = el("div", "src-body");

            const head = el("div", "src-head");
            if (url) {
                const fav = document.createElement("img");
                fav.className = "src-fav";
                fav.loading = "lazy";
                fav.src = `https://www.google.com/s2/favicons?sz=32&domain_url=${encodeURIComponent(url)}`;
                fav.alt = "";
                fav.addEventListener("error", () => {
                    const fallback = el("span", "src-fallback", domain.charAt(0) || "·");
                    fav.replaceWith(fallback);
                });
                head.appendChild(fav);
            } else {
                head.appendChild(el("span", "src-fallback", domain.charAt(0) || "·"));
            }

            const title = document.createElement(url ? "a" : "span");
            title.className = "src-title";
            title.textContent = titleText;
            if (url) {
                title.href = url;
                title.target = "_blank";
                title.rel = "noopener noreferrer";
            }
            head.appendChild(title);
            body.appendChild(head);

            if (url) body.appendChild(el("div", "src-domain", domain));

            const snippetText = (hit.snippet && String(hit.snippet).trim())
                ? String(hit.snippet).trim()
                : "";
            if (snippetText) body.appendChild(el("p", "src-snippet", snippetText));

            row.appendChild(body);
            list.appendChild(row);
        });

        details.appendChild(list);
        turn.appendChild(details);
    }

    function renderDocs(turn, docs) {
        if (!Array.isArray(docs) || !docs.length) return;
        const details = el("details", "docs");
        const summaryEl = el("summary");
        summaryEl.appendChild(el("span", "section-label", `本地文档 · ${docs.length}`));
        details.appendChild(summaryEl);
        docs.forEach((doc, index) => {
            const item = el("div", "doc-item");
            item.appendChild(el("div", "doc-name", doc.source || `文档片段 ${index + 1}`));
            item.appendChild(el("div", "doc-snippet", snippet(doc.content, 200)));
            details.appendChild(item);
        });
        turn.appendChild(details);
    }

    function renderNotes(turn, data) {
        const notes = [];
        if (data.llm_error) notes.push({ kind: "error", text: data.llm_error });
        if (data.llm_warning) notes.push({ kind: "warn", text: data.llm_warning });
        if (data.search_error) notes.push({ kind: "warn", text: `搜索：${data.search_error}` });
        const warnings = Array.isArray(data.search_warnings)
            ? data.search_warnings
            : data.search_warnings
                ? [data.search_warnings]
                : [];
        for (const w of warnings.filter(Boolean)) notes.push({ kind: "warn", text: w });
        for (const note of notes) {
            turn.appendChild(el("div", `note note-${note.kind}`, note.text));
        }
    }

    function renderMeta(turn, data) {
        const control = data.control || {};
        const times = data.response_times || {};
        const row = el("div", "meta-row");
        let hasMeta = false;

        if (settings.timing.includes("total") && typeof times.total_ms === "number") {
            row.appendChild(el("span", null, fmtMs(times.total_ms)));
            hasMeta = true;
        }

        const llmCalls = Array.isArray(times.llm_calls) ? times.llm_calls : [];
        const lastCall = llmCalls[llmCalls.length - 1];
        if (lastCall && (lastCall.provider || lastCall.model)) {
            if (hasMeta) row.appendChild(el("span", "meta-sep", "·"));
            const suffix = lastCall.provider && lastCall.model
                ? `${lastCall.provider}/${lastCall.model}`
                : lastCall.provider || lastCall.model;
            row.appendChild(el("span", null, suffix));
            hasMeta = true;
        }

        const mode = MODE_LABELS[control.search_mode] || control.search_mode;
        if (mode) {
            if (hasMeta) row.appendChild(el("span", "meta-sep", "·"));
            row.appendChild(el("span", null, mode));
            hasMeta = true;
        }

        if (control.fallback_triggered) {
            if (hasMeta) row.appendChild(el("span", "meta-sep", "·"));
            row.appendChild(el("span", "meta-flag", "已启用深度检索恢复"));
            hasMeta = true;
        }

        if (hasMeta) turn.appendChild(row);

        // 耗时明细（受「耗时明细」设置控制）
        const blocks = [];
        if (settings.timing.includes("search") && Array.isArray(times.search_sources)) {
            for (const entry of times.search_sources) blocks.push({ label: entry.label || entry.source || "搜索源", value: entry.duration_ms, group: "search" });
        }
        if (settings.timing.includes("llm") && llmCalls.length) {
            for (const entry of llmCalls) blocks.push({ label: entry.label || "LLM", value: entry.duration_ms, group: "llm" });
        }
        if (settings.timing.includes("tools") && Array.isArray(times.tool_calls)) {
            for (const entry of times.tool_calls) blocks.push({ label: entry.tool || "工具", value: entry.duration_ms, group: "tools" });
        }
        if (blocks.length) {
            const block = el("div", "meta-block");
            for (const item of blocks) {
                const line = el("div", "meta-line");
                const label = el("b", null, item.label);
                const value = el("span", null, fmtMs(item.value));
                line.append(label, value);
                block.appendChild(line);
            }
            turn.appendChild(block);
        }
    }

    function renderResult(refs, data) {
        refs.workflow.finalize(data.response_times && data.response_times.total_ms);

        const answer = (data && data.answer ? String(data.answer) : "").trim() || "未能生成答案";
        const answerEl = el("div", "answer");
        answerEl.innerHTML = renderMarkdown(answer);
        highlightIn(answerEl);

        // 回答插入到 workflow 之后
        refs.workflow.el.after(answerEl);

        renderSources(refs.turn, data.search_hits);
        renderDocs(refs.turn, data.retrieved_docs);
        renderNotes(refs.turn, data);
        renderMeta(refs.turn, data);
    }

    // ------------------------------------------------------------------
    // 降级路径：从最终结果合成步骤
    // ------------------------------------------------------------------
    function synthesizeSteps(data) {
        const control = (data && data.control) || {};
        const times = (data && data.response_times) || {};
        const steps = [];
        const llmCalls = Array.isArray(times.llm_calls) ? times.llm_calls : [];
        const findCall = (label) => llmCalls.find((c) => c.label === label);

        const mode = control.search_mode || "";
        if (mode === "image_content_present") {
            steps.push({ id: "visual", title: "图片理解", status: "done", detail: "已生成回答" });
            return steps;
        }

        steps.push({
            id: "intent",
            title: "意图理解",
            status: "done",
            detail: control.domain && control.domain !== "general"
                ? `识别领域：${control.domain}`
                : mode === "small_talk" ? "识别为闲聊" : "通用问题",
        });

        if (control.decision) {
            steps.push({
                id: "route",
                title: "路由决策",
                status: "done",
                detail: control.decision.needs_search ? "需要联网检索" : "无需检索，直接回答",
                duration_ms: findCall("search_decision")?.duration_ms,
            });
        } else if (control.force_search_enabled) {
            steps.push({ id: "route", title: "路由决策", status: "skipped", detail: "已跳过：强制联网" });
        }

        if (Array.isArray(control.keywords) && control.keywords.length) {
            steps.push({
                id: "keywords",
                title: "生成检索词",
                status: "done",
                detail: control.keywords.slice(0, 4).join("、"),
                duration_ms: findCall("keyword_generation")?.duration_ms,
            });
        }

        if (control.search_performed) {
            const searchSources = Array.isArray(times.search_sources) ? times.search_sources : [];
            steps.push({
                id: "search",
                title: "联网检索",
                status: "done",
                detail: `${Array.isArray(data.search_hits) ? data.search_hits.length : 0} 条结果`,
                items: searchSources.map((s) => ({
                    label: s.label || s.source || "搜索源",
                    value: fmtMs(s.duration_ms) + (s.error ? ` · ${s.error}` : ""),
                })),
            });
        }

        const apiCalls = Array.isArray(data && data.search_api_calls) ? data.search_api_calls : [];
        apiCalls.forEach((call, index) => {
            const count = Number.isFinite(Number(call && call.result_count))
                ? Math.max(0, Number(call.result_count))
                : Array.isArray(call && call.records) ? call.records.length : 0;
            const failed = call && call.status === "error";
            const label = (call && (call.label || call.provider)) || "Search";
            const isExtract = call && call.kind === "extracted_pages";
            const items = [
                { label: "提供方", value: (call && call.provider) || label },
                { label: isExtract ? "页面" : "结果", value: `${count} ${isExtract ? "页" : "条"}` },
            ];
            if (call && call.reason) items.push({ label: "原因", value: call.reason });
            steps.push({
                id: `${isExtract ? "extract_api" : "search_api_fallback"}_${index + 1}`,
                title: isExtract ? `官方文档抓取：${label}` : `搜索 API：${label}`,
                status: failed ? "error" : "done",
                detail: failed ? "抓取失败" : (isExtract ? `抽取 ${count} 个页面` : `返回 ${count} 条结果`),
                duration_ms: call && call.duration_ms,
                items,
                records: Array.isArray(call && call.records) ? call.records : [],
                record_kind: isExtract ? "extracted_pages" : "search_results",
                record_label: isExtract ? `已抽取网页 · ${count}` : `搜索结果 · ${count}`,
            });
        });

        if (Array.isArray(data.retrieved_docs) && data.retrieved_docs.length) {
            steps.push({ id: "local", title: "本地文档检索", status: "done", detail: `${data.retrieved_docs.length} 个片段` });
        }

        if (control.search_performed || (Array.isArray(data.retrieved_docs) && data.retrieved_docs.length)) {
            steps.push({ id: "rerank", title: "证据重排融合", status: "done" });
        }

        const answerCall = findCall("search_rag_answer") || findCall("local_rag_answer") || findCall("direct_answer");
        steps.push({
            id: "generate",
            title: "生成回答",
            status: "done",
            detail: answerCall && answerCall.provider && answerCall.model
                ? `${answerCall.provider}/${answerCall.model}`
                : undefined,
            duration_ms: answerCall?.duration_ms,
        });

        const postcheck = control.postcheck;
        if (postcheck) {
            if (postcheck.eligible === false && postcheck.skipped_reason) {
                steps.push({ id: "postcheck", title: "质量校验", status: "skipped", detail: "未启用或不适用" });
            } else {
                steps.push({
                    id: "postcheck",
                    title: "质量校验",
                    status: "done",
                    detail: postcheck.passes_postcheck ? "通过" : "未通过",
                    duration_ms: findCall("postcheck_judge")?.duration_ms,
                });
            }
        }

        if (control.fallback_triggered) {
            const loopStatus = control.loop_status;
            const loopMeta = {
                succeeded: { text: "循环成功", tone: "ok", status: "done" },
                exhausted: { text: "迭代用尽", tone: "warn", status: "done" },
                stagnated: { text: "检索停滞", tone: "warn", status: "error" },
                unrecoverable: { text: "不可恢复", tone: "err", status: "error" },
            }[loopStatus];
            const reasonLabels = {
                constraints_satisfied: "约束满足",
                continue: "继续检索",
                final_answer_rejected: "答案未达标，继续补充",
                exhausted: "迭代用尽",
                stagnated: "检索停滞",
                unrecoverable: "工具持续失败",
                invalid_tool_request: "工具调用格式无效",
                process_narration: "过程性文本，继续补充",
            };
            const verdicts = Array.isArray(control.loop_verdicts) ? control.loop_verdicts : [];
            const reactStep = {
                id: "react",
                title: "深度检索恢复",
                status: loopMeta ? loopMeta.status : "done",
                detail: loopMeta
                    ? `${control.loop_iterations ?? "?"} 轮迭代 · ${control.engine || "langgraph"} 引擎`
                    : control.max_iterations
                        ? `最多 ${control.max_iterations} 轮迭代`
                        : "ReAct",
            };
            if (loopMeta) {
                reactStep.badge = { text: loopMeta.text, tone: loopMeta.tone };
            }
            if (verdicts.length) {
                reactStep.items = verdicts.map((v) => ({
                    label: `第 ${v.iteration ?? "?"} 轮`,
                    value: (reasonLabels[v.reason] || v.reason || "")
                        + (Array.isArray(v.constraints_missing) && v.constraints_missing.length
                            ? `（缺：${v.constraints_missing.join("、")}）`
                            : ""),
                }));
            }
            steps.push(reactStep);
        }

        return steps;
    }

    // ------------------------------------------------------------------
    // SSE 客户端
    // ------------------------------------------------------------------
    function parseSSEFrame(frame) {
        let event = "message";
        const dataLines = [];
        for (const line of frame.split("\n")) {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
        }
        if (!dataLines.length) return null;
        try {
            return { event, data: JSON.parse(dataLines.join("\n")) };
        } catch {
            return null;
        }
    }

    async function streamAnswer(payload, handlers) {
        const response = await fetch("/api/answer/stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        if (!response.ok || !response.body) {
            const error = new Error(`stream_http_${response.status}`);
            error.fallback = true;
            throw error;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let idx;
            while ((idx = buffer.indexOf("\n\n")) !== -1) {
                const frame = buffer.slice(0, idx);
                buffer = buffer.slice(idx + 2);
                const parsed = parseSSEFrame(frame);
                if (!parsed) continue;
                if (parsed.event === "step") handlers.onStep(parsed.data);
                else if (parsed.event === "result") handlers.onResult(parsed.data);
                else if (parsed.event === "error") handlers.onError(parsed.data);
            }
        }
    }

    async function legacyAnswer(payload) {
        const response = await fetch("/api/answer", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => null);
        if (!response.ok || !data || data.error) {
            throw new Error((data && data.error) || "请求失败");
        }
        return data;
    }

    // ------------------------------------------------------------------
    // 提交
    // ------------------------------------------------------------------
    function extractCodeBlocks(text) {
        const blocks = [];
        const regex = /```(\w*)\n([\s\S]*?)```/g;
        let match;
        while ((match = regex.exec(text)) !== null) {
            blocks.push({ lang: match[1] || "text", content: match[2] });
        }
        return blocks;
    }

    function normalizeLimits() {
        let total = parseInt(limitTotal.value, 10);
        if (!Number.isFinite(total) || total < 1) total = 1;
        if (total > 30) total = 30;
        limitTotal.value = String(total);

        let perSource = parseInt(limitPerSource.value, 10);
        if (!Number.isFinite(perSource) || perSource < 1) perSource = 1;
        if (perSource > 20) perSource = 20;
        if (perSource > total) perSource = total;
        limitPerSource.value = String(perSource);

        let reference = parseInt(limitReference.value, 10);
        if (!Number.isFinite(reference) || reference < 1) reference = 1;
        if (reference > 20) reference = 20;
        limitReference.value = String(reference);

        settings.limits = { total, perSource, reference };
        saveSettings();
    }

    function buildPayload(query) {
        const payload = {
            query,
            search: settings.search ? "on" : "off",
            conversation_id: conversationId,
        };
        const codeBlocks = extractCodeBlocks(query);
        if (codeBlocks.length) payload.code_blocks = codeBlocks;
        if (settings.model) payload.model = settings.model;
        if (settings.search) {
            if (settings.sources.length) payload.search_sources = [...settings.sources];
            if (settings.forceSearch) payload.force_search = true;
            normalizeLimits();
            payload.search_total_limit = settings.limits.total;
            payload.search_source_limit = settings.limits.perSource;
            payload.search_reference_limit = settings.limits.reference;
            if (settings.searchDepth && settings.searchDepth !== "auto") {
                payload.search_depth = settings.searchDepth;
            }
        }
        if (state.images.length) {
            payload.images = state.images.map((img) => ({
                base64: img.base64,
                mime_type: img.mime_type,
            }));
        }
        return payload;
    }

    function setLoading(isLoading) {
        state.loading = isLoading;
        sendBtn.disabled = isLoading;
        sendBtn.classList.toggle("is-loading", isLoading);
        queryInput.readOnly = isLoading;
    }

    async function handleSubmit(event) {
        event.preventDefault();
        if (state.loading) return;

        const query = queryInput.value.trim();
        if (!query) {
            setStatus("请输入问题。");
            return;
        }

        const attachedImages = [...state.images];
        const refs = appendTurn(query, attachedImages);
        setLoading(true);
        setStatus("正在执行…");
        queryInput.value = "";
        autosize();

        const payload = buildPayload(query);
        let finished = false;
        let failed = null;

        try {
            await streamAnswer(payload, {
                onStep: (step) => refs.workflow.apply(step),
                onResult: (data) => {
                    finished = true;
                    renderResult(refs, data);
                    setStatus("回答已生成");
                },
                onError: (data) => {
                    failed = (data && data.error) || "请求失败";
                },
            });
        } catch (err) {
            if (err && err.fallback) {
                // 旧后端降级：一次性接口 + 事后合成步骤
                try {
                    const data = await legacyAnswer(payload);
                    for (const step of synthesizeSteps(data)) refs.workflow.apply(step);
                    finished = true;
                    renderResult(refs, data);
                    setStatus("回答已生成");
                } catch (legacyErr) {
                    failed = (legacyErr && legacyErr.message) || "请求失败";
                }
            } else {
                failed = "连接中断，请重试";
            }
        }

        if (!finished && !failed) failed = "服务未返回结果";
        if (failed) {
            refs.workflow.fail();
            const note = el("div", "note note-error", failed);
            refs.workflow.el.after(note);
            setStatus(failed, true);
        } else if (finished) {
            setActiveConversation(conversationId);
            loadConversationList();
        }

        setLoading(false);
        queryInput.focus();
        state.images = [];
        renderChips();
    }

    // ------------------------------------------------------------------
    // 模型列表
    // ------------------------------------------------------------------
    const PROVIDER_META = {
        "opencode-go": { label: "OpenCode Go", order: 1 },
        zai: { label: "Zai", order: 2 },
        glm: { label: "GLM", order: 3 },
        openai: { label: "OpenAI", order: 4 },
        anthropic: { label: "Anthropic", order: 5 },
        google: { label: "Google", order: 6 },
        minimax: { label: "Minimax", order: 7 },
        hkgai: { label: "HKGAI", order: 8 },
        openrouter: { label: "OpenRouter", order: 9 },
    };

    function refreshTopbarModel() {
        const selected = modelSelect.selectedOptions[0];
        topbarModel.textContent = selected && selected.value
            ? selected.textContent
            : "默认模型";
    }

    async function loadModels() {
        try {
            const response = await fetch("/api/models");
            if (!response.ok) throw new Error("models");
            const data = await response.json();
            const rawModels = Array.isArray(data.models) ? data.models : [];

            const byId = new Map();
            for (const m of rawModels) {
                const id = m && m.id ? String(m.id) : null;
                if (!id) continue;
                const key = (m.provider || "").toString().trim().toLowerCase();
                const provider = PROVIDER_META[key] ? key : key || "openrouter";
                const existing = byId.get(id);
                if (!existing || (existing.provider === "openrouter" && provider !== "openrouter")) {
                    byId.set(id, { id, provider });
                }
            }

            const groups = new Map();
            for (const { id, provider } of byId.values()) {
                if (!groups.has(provider)) groups.set(provider, []);
                const label = `${(PROVIDER_META[provider] || {}).label || provider} — ${id}`;
                groups.get(provider).push({ id, label });
            }

            modelSelect.innerHTML = "";
            modelSelect.appendChild(new Option("默认模型", ""));

            const sortedProviders = Array.from(groups.keys()).sort((a, b) => {
                const oa = (PROVIDER_META[a] || {}).order ?? 99;
                const ob = (PROVIDER_META[b] || {}).order ?? 99;
                if (oa !== ob) return oa - ob;
                return a.localeCompare(b);
            });

            for (const provider of sortedProviders) {
                const group = document.createElement("optgroup");
                group.label = (PROVIDER_META[provider] || {}).label || provider;
                for (const item of groups.get(provider)) {
                    group.appendChild(new Option(item.label, item.id));
                }
                modelSelect.appendChild(group);
            }

            if (settings.model && byId.has(settings.model)) {
                modelSelect.value = settings.model;
            }
        } catch {
            modelSelect.innerHTML = "";
            modelSelect.appendChild(new Option("默认模型", ""));
        }
        refreshTopbarModel();
    }

    // ------------------------------------------------------------------
    // 文件与图片
    // ------------------------------------------------------------------
    function renderChips() {
        chipRow.innerHTML = "";
        for (let i = 0; i < state.images.length; i += 1) {
            const img = state.images[i];
            const chip = el("span", "fchip");
            const thumb = document.createElement("img");
            thumb.src = img.base64;
            thumb.alt = "";
            chip.appendChild(thumb);
            chip.appendChild(el("span", "fchip-name", img.name));
            const remove = el("button", null, "×");
            remove.type = "button";
            remove.setAttribute("aria-label", `移除 ${img.name}`);
            remove.addEventListener("click", () => {
                state.images.splice(i, 1);
                renderChips();
            });
            chip.appendChild(remove);
            chipRow.appendChild(chip);
        }
        for (const name of state.docs) {
            const chip = el("span", "fchip");
            chip.appendChild(el("span", "fchip-name", name));
            const remove = el("button", null, "×");
            remove.type = "button";
            remove.setAttribute("aria-label", `删除 ${name}`);
            remove.addEventListener("click", async () => {
                try {
                    await fetch(`/api/files/${encodeURIComponent(name)}`, { method: "DELETE" });
                    setStatus(`已删除 ${name}`);
                    await fetchFiles();
                } catch {
                    setStatus(`删除 ${name} 失败`, true);
                }
            });
            chip.appendChild(remove);
            chipRow.appendChild(chip);
        }
    }

    async function fetchFiles() {
        try {
            const response = await fetch("/api/files");
            if (!response.ok) throw new Error("files");
            const files = await response.json();
            state.docs = Array.isArray(files) ? files : [];
        } catch {
            state.docs = [];
        }
        renderChips();
    }

    async function handleFiles(files) {
        const imageFiles = [];
        const docFiles = [];
        for (const file of files) {
            if (file.type.startsWith("image/")) imageFiles.push(file);
            else docFiles.push(file);
        }

        for (const file of imageFiles) {
            if (file.size > 5 * 1024 * 1024) {
                setStatus(`${file.name} 太大（最大 5MB）`, true);
                continue;
            }
            const dataUrl = await new Promise((resolve) => {
                const reader = new FileReader();
                reader.onload = (e) => resolve(e.target.result);
                reader.readAsDataURL(file);
            });
            state.images.push({ name: file.name, base64: dataUrl, mime_type: file.type });
        }
        if (imageFiles.length) {
            renderChips();
            setStatus(`已添加 ${imageFiles.length} 张图片`);
        }

        for (const file of docFiles) {
            const formData = new FormData();
            formData.append("file", file);
            try {
                setStatus("正在上传文件…");
                const response = await fetch("/api/files", { method: "POST", body: formData });
                if (!response.ok) throw new Error("upload");
                setStatus("文件上传完成");
            } catch {
                setStatus(`${file.name} 上传失败`, true);
            }
        }
        if (docFiles.length) await fetchFiles();
    }

    // ------------------------------------------------------------------
    // 控件绑定
    // ------------------------------------------------------------------
    function autosize() {
        queryInput.style.height = "auto";
        queryInput.style.height = `${Math.min(queryInput.scrollHeight, 220)}px`;
    }

    function applySettingsToControls() {
        searchPill.classList.toggle("is-on", settings.search);
        searchPill.setAttribute("aria-pressed", settings.search ? "true" : "false");
        for (const checkbox of sourceCheckboxes) {
            checkbox.checked = settings.sources.includes(checkbox.value);
        }
        forceSearchInput.checked = settings.forceSearch;
        limitTotal.value = String(settings.limits.total);
        limitPerSource.value = String(settings.limits.perSource);
        limitReference.value = String(settings.limits.reference);
        searchDepthSelect.value = settings.searchDepth;
        for (const checkbox of timingCheckboxes) {
            checkbox.checked = settings.timing.includes(checkbox.value);
        }
        updateSearchDependentControls();
    }

    function updateSearchDependentControls() {
        const disabled = !settings.search;
        for (const input of [limitTotal, limitPerSource, limitReference]) {
            input.disabled = disabled;
        }
        searchDepthSelect.disabled = disabled;
        forceSearchInput.disabled = disabled;
        if (disabled && settings.forceSearch) {
            settings.forceSearch = false;
            forceSearchInput.checked = false;
        }
    }

    composer.addEventListener("submit", handleSubmit);

    queryInput.addEventListener("input", autosize);
    queryInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
            event.preventDefault();
            composer.requestSubmit();
        }
    });

    attachBtn.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
        if (fileInput.files && fileInput.files.length) {
            handleFiles(Array.from(fileInput.files));
            fileInput.value = "";
        }
    });

    searchPill.addEventListener("click", () => {
        settings.search = !settings.search;
        searchPill.classList.toggle("is-on", settings.search);
        searchPill.setAttribute("aria-pressed", settings.search ? "true" : "false");
        updateSearchDependentControls();
        saveSettings();
        setStatus(settings.search ? "联网搜索已启用" : "联网搜索已关闭");
    });

    if (newConvBtn) {
        newConvBtn.addEventListener("click", startNewConversation);
    }
    if (sidebarToggle) {
        sidebarToggle.addEventListener("click", () => setSidebarOpen(sidebar.classList.contains("is-hidden")));
    }
    if (sidebarClose) {
        sidebarClose.addEventListener("click", () => setSidebarOpen(false));
    }

    modelSelect.addEventListener("change", () => {
        settings.model = modelSelect.value;
        saveSettings();
        refreshTopbarModel();
    });

    settingsBtn.addEventListener("click", () => {
        const willOpen = settingsPanel.hidden;
        settingsPanel.hidden = !willOpen;
        settingsBtn.setAttribute("aria-expanded", willOpen ? "true" : "false");
        settingsBtn.classList.toggle("is-on", willOpen);
    });

    for (const checkbox of sourceCheckboxes) {
        checkbox.addEventListener("change", () => {
            const value = checkbox.value;
            if (checkbox.checked) {
                if (!settings.sources.includes(value)) settings.sources.push(value);
            } else {
                if (settings.sources.length <= 1) {
                    checkbox.checked = true;
                    setStatus("至少选择一个搜索源");
                    return;
                }
                settings.sources = settings.sources.filter((s) => s !== value);
            }
            saveSettings();
        });
    }

    for (const checkbox of timingCheckboxes) {
        checkbox.addEventListener("change", () => {
            const value = checkbox.value;
            if (checkbox.checked) {
                if (!settings.timing.includes(value)) settings.timing.push(value);
            } else {
                if (settings.timing.length <= 1) {
                    checkbox.checked = true;
                    setStatus("至少保留一项耗时明细");
                    return;
                }
                settings.timing = settings.timing.filter((t) => t !== value);
            }
            saveSettings();
        });
    }

    forceSearchInput.addEventListener("change", () => {
        settings.forceSearch = forceSearchInput.checked;
        saveSettings();
    });

    searchDepthSelect.addEventListener("change", () => {
        settings.searchDepth = searchDepthSelect.value;
        saveSettings();
    });

    for (const input of [limitTotal, limitPerSource, limitReference]) {
        input.addEventListener("change", normalizeLimits);
    }

    // ------------------------------------------------------------------
    // 初始化
    // ------------------------------------------------------------------
    async function bootstrap() {
        state.docs = [];
        setSidebarOpen(isSidebarOpen());
        setActiveConversation(conversationId);
        applySettingsToControls();
        renderEmpty();
        fetchFiles();
        loadModels();
        autosize();
        await loadConversationList();
        // On a fresh load with no active turn in the thread, restore the most
        // recent conversation so the user picks up where they left off.
        if (state.conversations.length && state.turnCount === 0) {
            await selectConversation(state.conversations[0].conversation_id);
        }
    }

    bootstrap();
});
