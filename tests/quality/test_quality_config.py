"""Config-file defaults for the evaluation framework (``config.quality.json``).

Precedence under test: explicit CLI flag > config file > the script's built-in
default. A key the parser does not know, or a value its ``type``/``choices``
would reject, must be fatal rather than silently falling back.
"""
from __future__ import annotations

import argparse
import json
import sys

import pytest

import tests.quality_review as quality_review
import tests.quality_runner as quality_runner
from tests.quality.common import (
    ROOT,
    SECRET_MARKERS,
    apply_config_defaults,
    load_quality_config,
    quality_config_path,
    quality_defaults,
)

pytestmark = pytest.mark.quality_offline

SHIPPED = ROOT / "config.quality.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="builtin-model")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--autonomy", choices=["guided", "autonomous"], default=None)
    parser.add_argument("--multi-turn", action="store_true")
    return parser


def write_config(tmp_path, payload) -> str:
    path = tmp_path / "config.quality.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


# ------------------------------------------------------------------ loader
def test_missing_default_file_keeps_builtin_defaults(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ISE_QUALITY_CONFIG", raising=False)
    monkeypatch.setattr("tests.quality.common.QUALITY_CONFIG_PATH", tmp_path / "absent.json")
    assert load_quality_config() == {}


def test_explicitly_named_file_must_exist(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ISE_QUALITY_CONFIG", raising=False)
    with pytest.raises(SystemExit):
        load_quality_config(str(tmp_path / "absent.json"))


def test_env_var_selects_the_file(monkeypatch, tmp_path) -> None:
    path = write_config(tmp_path, {"judge": {"model": "from-env"}})
    monkeypatch.setenv("ISE_QUALITY_CONFIG", path)
    assert load_quality_config()["judge"]["model"] == "from-env"
    assert quality_config_path() == tmp_path / "config.quality.json"


def test_non_object_file_is_fatal(monkeypatch, tmp_path) -> None:
    path = tmp_path / "config.quality.json"
    path.write_text("[1, 2]", encoding="utf-8")
    monkeypatch.delenv("ISE_QUALITY_CONFIG", raising=False)
    with pytest.raises(SystemExit):
        load_quality_config(str(path))


def test_comment_keys_are_not_options() -> None:
    block = quality_defaults({"judge": {"_comment": "x", "model": "m"}}, "judge")
    assert block == {"model": "m"}
    assert quality_defaults({}, "judge") == {}


# --------------------------------------------------------------- precedence
def test_config_replaces_builtin_default() -> None:
    parser = build_parser()
    apply_config_defaults(parser, {"model": "from-config", "workers": 4}, source="test")
    args = parser.parse_args([])
    assert (args.model, args.workers) == ("from-config", 4)


def test_cli_flag_beats_config() -> None:
    parser = build_parser()
    apply_config_defaults(parser, {"model": "from-config"}, source="test")
    assert parser.parse_args(["--model", "from-cli"]).model == "from-cli"


def test_values_are_coerced_through_the_flag_type() -> None:
    parser = build_parser()
    apply_config_defaults(parser, {"workers": "4", "temperature": 1}, source="test")
    args = parser.parse_args([])
    assert args.workers == 4 and isinstance(args.workers, int)
    assert args.temperature == 1.0 and isinstance(args.temperature, float)


def test_null_keeps_the_flag_unset() -> None:
    parser = build_parser()
    apply_config_defaults(parser, {"autonomy": None}, source="test")
    assert parser.parse_args([]).autonomy is None


# ------------------------------------------------------------- rejections
def test_unknown_key_is_fatal() -> None:
    with pytest.raises(SystemExit) as excinfo:
        apply_config_defaults(build_parser(), {"judge_modle": "typo"}, source="cfg")
    assert "judge_modle" in str(excinfo.value)


def test_uncoercible_value_is_fatal() -> None:
    with pytest.raises(SystemExit) as excinfo:
        apply_config_defaults(build_parser(), {"workers": "many"}, source="cfg")
    assert "workers" in str(excinfo.value)


def test_value_outside_choices_is_fatal() -> None:
    with pytest.raises(SystemExit):
        apply_config_defaults(build_parser(), {"autonomy": "wild"}, source="cfg")


def test_flag_needs_a_boolean() -> None:
    with pytest.raises(SystemExit):
        apply_config_defaults(build_parser(), {"multi_turn": "yes"}, source="cfg")
    parser = build_parser()
    apply_config_defaults(parser, {"multi_turn": True}, source="cfg")
    assert parser.parse_args([]).multi_turn is True


# ------------------------------------------------------- the shipped file
def test_shipped_config_drives_the_runner(monkeypatch) -> None:
    shipped = json.loads(SHIPPED.read_text(encoding="utf-8"))
    monkeypatch.delenv("ISE_QUALITY_CONFIG", raising=False)
    monkeypatch.setattr(sys, "argv", ["quality_runner", "--suite", "offline"])
    args = quality_runner.parse_args()
    for key, value in quality_defaults(shipped, "runner").items():
        assert getattr(args, key) == value, key
    monkeypatch.setattr(sys, "argv", ["quality_runner", "--suite", "offline", "--judge-model", "other-judge"])
    assert quality_runner.parse_args().judge_model == "other-judge"


def test_shipped_config_drives_the_reviewer(monkeypatch) -> None:
    shipped = json.loads(SHIPPED.read_text(encoding="utf-8"))
    monkeypatch.delenv("ISE_QUALITY_CONFIG", raising=False)
    monkeypatch.setattr(sys, "argv", ["quality_review", "--source", "runtime/quality/none"])
    args = quality_review.parse_args()
    for key, value in quality_defaults(shipped, "judge").items():
        assert getattr(args, key) == value, key
    monkeypatch.setattr(sys, "argv", ["quality_review", "--source", "runtime/quality/none", "--model", "other-judge"])
    assert quality_review.parse_args().model == "other-judge"


def test_shipped_config_carries_no_credentials() -> None:
    """Only option keys are checked; the ``_comment`` prose may name config.json's fields."""
    shipped = json.loads(SHIPPED.read_text(encoding="utf-8"))
    for section, block in shipped.items():
        for key in quality_defaults(shipped, section) if isinstance(block, dict) else {}:
            segments = set(key.lower().split("_"))  # whole segments: api_key trips, max_tokens does not
            assert not segments & set(SECRET_MARKERS), f"{section}.{key}"


def test_manifest_pins_the_config_digest(monkeypatch) -> None:
    monkeypatch.delenv("ISE_QUALITY_CONFIG", raising=False)
    monkeypatch.setattr(sys, "argv", ["quality_review", "--source", "runtime/quality/none"])
    manifest = quality_review.build_manifest(quality_review.parse_args(), [])
    assert manifest["quality_config_digest"]
    assert manifest["model"] == json.loads(SHIPPED.read_text(encoding="utf-8"))["judge"]["model"]
