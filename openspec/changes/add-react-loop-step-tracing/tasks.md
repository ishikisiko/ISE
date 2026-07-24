## 1. LangGraph ReAct Trace Events

- [x] 1.1 Add an optional workflow tracer to `ReactLoopGraphRunner` and emit bounded iteration lifecycle events from act and evaluate.
- [x] 1.2 Emit one safe tool-request/outcome event per observed tool call, including public query arguments, status, duration, result count where detectable, and bounded error summaries.
- [x] 1.3 Treat recognized textual tool error responses as failed observations so trace status and loop accounting agree.

## 2. Tool-Call Compatibility And Response Metadata

- [x] 2.1 Normalize the supported XML-style function/query response into an enabled tool call before it can be treated as an answer.
- [x] 2.2 Record unsupported or malformed function markup as an invalid-tool-request trace outcome, feed corrective retry context, and prevent raw markup from becoming the final answer.
- [x] 2.3 Pass the tracer into the LangGraph runner, return a bounded `control.react_trace` projection, and add the frontend label for the new verdict reason.

## 3. Tests And Validation

- [x] 3.1 Add unit tests for iteration, tool success/failure, verdict event details, and bounded safe trace payloads.
- [x] 3.2 Add regression tests for XML call normalization, malformed markup handling, response control projection, and streamed tracer event ordering.
- [x] 3.3 Run focused ReAct/server tests, the full pytest suite, strict OpenSpec validation, and a deterministic stream smoke check.

## 4. Trace Presentation Refinement

- [x] 4.1 Return only current-turn verdicts from resumed LangGraph runs while retaining checkpoint history, and reset per-act observation facts before evaluation.
- [x] 4.2 Keep detailed verdict items on evaluation events only; make the outer ReAct event a compact summary when detailed trace events are available.
- [x] 4.3 Reject process narration as a final-answer candidate, add regression coverage for all presentation boundaries, and rerun focused/full validation.

## 5. Per-API Retrieval Audit Records

- [x] 5.1 Add a bounded, safe workflow-record payload and retain the actual
  result snapshot for every concrete search-provider request, including
  combined, priority, and key-fallback attempts.
- [x] 5.2 Emit one trace step per search API call from the normal RAG and ReAct
  tool paths; add an equivalent safe page-record projection for actual
  selected-page extraction calls without enabling automatic page fetching.
- [x] 5.3 Render each search or extraction record list as a collapsed,
  expandable workflow group with individually linkable pages and no raw page
  content.
- [x] 5.4 Add regression coverage for safe payloads, provider/fallback call
  separation, streamed trace forwarding, and frontend record rendering; run
  focused/full validation and a local stream smoke check.
