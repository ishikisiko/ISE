"""Behavioral regression: the next real wire request must contain tool evidence.

The fake provider derives its answer from the payload, rather than returning a
scripted answer regardless of what the application actually sent.
"""
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from langchain.langchain_llm import UniversalChatModel
from orchestrators.autonomy_policy import resolve_autonomy_policy
from orchestrators.react_loop_graph import ReactLoopGraphRunner
from utils.timing_utils import TimingRecorder


def make_wire_model(monkeypatch, *, mode="autonomous", analysis=None):
    nonce = "evidence-marker-cobalt-orchid"
    requests = []

    @tool
    def web_search(query: str) -> str:
        """Retrieve the current secret marker from a public test source."""
        return "[E1] Verified marker: " + nonce

    class Response:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(self.content)}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}

    class Session:
        def post(self, endpoint, **kwargs):
            payload = kwargs["json"]
            requests.append(payload)
            if len(requests) == 1:
                return Response({"action": "tool", "tool": "web_search", "args": {"query": "marker"}})
            observed = nonce in json.dumps(payload["messages"])
            return Response({"action": "final", "answer": nonce + " [E1]" if observed else "No observation received."})

    model = UniversalChatModel(api_key="test-key", provider="opencode-go", max_retries=0)
    monkeypatch.setattr(model, "_session", Session())
    timing = TimingRecorder(enabled=True)
    runner = ReactLoopGraphRunner(
        llm=model, tools=[web_search], query="Retrieve the marker.",
        autonomy_policy=resolve_autonomy_policy({}, mode), timing_recorder=timing, analysis=analysis,
    )
    return runner, requests, timing, nonce


def test_tool_observation_reaches_provider_and_controls_answer(monkeypatch):
    runner, requests, _, nonce = make_wire_model(monkeypatch)
    result = runner.run("Retrieve the marker.")
    assert len(requests) == 2
    assert nonce in result["answer"]
    # Retrieved material must not become a new system instruction.
    assert all(nonce not in str(m["content"]) for m in requests[1]["messages"] if m["role"] == "system")


def test_shim_preserves_provider_usage_for_every_act(monkeypatch):
    runner, _, timing, _ = make_wire_model(monkeypatch)
    runner.run("Retrieve the marker.")
    act = [call for call in timing.llm_calls if call["label"] == "loop_act"]
    assert len(act) == 2
    assert sum(call.get("total_tokens", 0) for call in act) == 30


def test_single_pipe_dsml_is_executed_not_delivered(monkeypatch):
    runner, _, _, _ = make_wire_model(monkeypatch)
    markup = ('<｜DSML｜tool_calls><｜DSML｜invoke name="web_search">'
              '<｜DSML｜parameter name="query" string="true">marker</｜DSML｜parameter>'
              '</｜DSML｜invoke></｜DSML｜tool_calls>')
    response, error = runner._normalize_function_markup(AIMessage(content=markup))
    assert error is None
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0]["args"] == {"query": "marker"}


def test_malformed_dsml_is_not_a_final_candidate(monkeypatch):
    runner, _, _, _ = make_wire_model(monkeypatch)
    markup = '<｜DSML｜tool_tools><tool_web_search><query>marker</query></tool_web_search></｜DSML｜tool_tools>'
    response, error = runner._normalize_function_markup(AIMessage(content=markup))
    assert error
    assert not response.content
    assert not response.tool_calls


def test_autonomous_recovers_from_malformed_protocol_before_success(monkeypatch):
    runner, _, _, _ = make_wire_model(monkeypatch)
    calls = []
    replies = iter([
        '<｜DSML｜tool_tools><tool_web_search><query>marker</query></tool_web_search></｜DSML｜tool_tools>',
        json.dumps({"action": "final", "answer": "A direct useful answer."}),
    ])

    def invoke(messages, **kwargs):
        calls.append(messages)
        return AIMessage(content=next(replies))

    runner.llm = SimpleNamespace(invoke=invoke)
    result = runner.run("Explain the marker.")
    assert len(calls) == 2
    assert result["answer"] == "A direct useful answer."


@pytest.mark.parametrize("markup", [
    '<｜DSML｜tool_calls><｜DSML｜invoke name="web_search"></｜DSML｜invoke></｜DSML｜tool_calls>',
    '<{"action":"tool","tool":"web_search","args":{"query":"a"}}>\n'
    '<{"action":"tool","tool":"web_search","args":{"query":"b"}}>',
])
def test_best_effort_never_leaks_unexecuted_tool_protocol(markup):
    answer = ReactLoopGraphRunner._best_effort_answer(markup, "exhausted")
    assert "DSML" not in answer
    assert '"action":"tool"' not in answer


def test_protocol_name_in_ordinary_prose_is_not_removed():
    draft = "DSML is a markup protocol; an action can also mean an ordinary step."
    assert draft in ReactLoopGraphRunner._best_effort_answer(draft, "exhausted")


@pytest.mark.parametrize("mode", ["guided", "autonomous"])
def test_clarification_owner_applies_inside_loop_not_only_before_it(monkeypatch, mode):
    analysis = SimpleNamespace(
        critical_ambiguity=True, ambiguities=["unresolved_entity_reference"],
        constraints={}, requires_evidence=False, entities=[],
    )
    runner, requests, _, nonce = make_wire_model(monkeypatch, mode=mode, analysis=analysis)
    result = runner.run("Retrieve the marker.")
    if mode == "guided":
        assert len(requests) == 1
        assert result["loop_status"] == "clarification_required"
    else:
        assert len(requests) == 2
        assert nonce in result["answer"]
        assert result["loop_status"] == "succeeded"
        assert not result["model_clarification"]
        assert "unresolved_entity_reference" in requests[0]["messages"][0]["content"]
        assert "ask_user" in requests[0]["messages"][0]["content"]


def test_act_keeps_usage_when_invalid_content_is_replaced(monkeypatch):
    runner, _, timing, _ = make_wire_model(monkeypatch)
    usage = {"input_tokens": 17, "output_tokens": 3, "total_tokens": 20}
    runner.llm = SimpleNamespace(invoke=lambda *a, **k: AIMessage(
        content="<｜DSML｜tool_tools>broken</｜DSML｜tool_tools>",
        usage_metadata=usage, response_metadata={"provider_marker": "preserved"},
    ))
    update = runner._act(runner._build_initial_state("Query"))
    assert update["invalid_tool_request"]
    assert update["messages"][0].content == ""
    assert update["messages"][0].usage_metadata == usage
    assert update["messages"][0].response_metadata == {"provider_marker": "preserved"}
    assert update["token_budget_state"]["measured_input_tokens"] == 17
    assert timing.llm_calls[0]["total_tokens"] == 20


@pytest.mark.parametrize("payload", [
    {"action": "tool", "tool": "web_search", "args": {"query": "q"}},
    {"action": "final", "answer": "A useful answer."},
])
def test_shim_preserves_provider_response_metadata(monkeypatch, payload):
    runner, _, _, _ = make_wire_model(monkeypatch)
    runner.llm = SimpleNamespace(invoke=lambda *a, **k: AIMessage(
        content=json.dumps(payload), id="provider-message",
        usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
        response_metadata={"finish_reason": "stop"},
    ))
    message = runner._act_shim([])
    assert message.id == "provider-message"
    assert message.usage_metadata["total_tokens"] == 10
    assert message.response_metadata == {"finish_reason": "stop"}


@pytest.mark.parametrize("content", [
    '<｜DSML｜invoke name="web_search"><｜DSML｜parameter name="query">q</｜DSML｜parameter></｜DSML｜invoke>',
    '<{"action":"tool","tool":"web_search","args":{"query":"a"}}>\n'
    '<{"action":"tool","tool":"web_search","args":{"query":"b"}}>',
    json.dumps({"action": "final", "answer": '<｜DSML｜tool_tools>unfinished</｜DSML｜tool_tools>'}),
])
def test_grounded_synthesis_cannot_deliver_tool_protocol(monkeypatch, content):
    runner, _, timing, _ = make_wire_model(monkeypatch)
    calls = []

    def invoke(*args, **kwargs):
        calls.append(args)
        return AIMessage(content=content,
                         usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10})

    runner.llm = SimpleNamespace(invoke=invoke)
    answer = runner._generate_grounded_synthesis({"messages": [AIMessage(content="Prior known finding.")]})
    assert "Prior known finding." in answer
    assert "DSML" not in answer and '"action":"tool"' not in answer
    assert len(calls) == 1
    assert timing.llm_calls[0]["total_tokens"] == 10


def test_best_effort_unwraps_real_final_answer(monkeypatch):
    answer = ReactLoopGraphRunner._best_effort_answer(
        json.dumps({"action": "final", "answer": "A useful answer."}), "exhausted")
    assert "A useful answer." in answer
    assert '"action"' not in answer


def test_grounded_synthesis_rejects_prose_attached_to_native_tool_call(monkeypatch):
    runner, _, _, _ = make_wire_model(monkeypatch)
    runner.llm = SimpleNamespace(invoke=lambda *a, **k: AIMessage(
        content="I will search for the answer.",
        tool_calls=[{"name": "web_search", "args": {"query": "q"}, "id": "unexpected"}],
    ))
    answer = runner._generate_grounded_synthesis({"messages": [AIMessage(content="Prior known finding.")]})
    assert "Prior known finding." in answer
    assert "I will search" not in answer


def test_multiple_wrapped_tool_objects_get_correction_feedback(monkeypatch):
    runner, _, _, _ = make_wire_model(monkeypatch)
    text = '<{"action":"tool","tool":"web_search","args":{"query":"a"}}>\n' \
           '<{"action":"tool","tool":"web_search","args":{"query":"b"}}>'
    message, error = runner._normalize_function_markup(AIMessage(content=text))
    assert error == "unrecognized_json_tool_markup"
    assert not message.content and not message.tool_calls
