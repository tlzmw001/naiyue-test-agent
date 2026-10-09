"""The existing bounded probe must exercise the actual CLI display path."""

from types import SimpleNamespace
import logging

from langchain_core.messages import AIMessage
from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog
from scripts import probe_message_events as probe
from test_application_run_events import ToolModel


def test_real_probe_cli_option_exercises_cli_and_checks_both_replies(monkeypatch, capsys):
    model = ToolModel(responses=[AIMessage(content='CLI_BEFORE_TOOL', tool_calls=[
        {'name': 'read_probe_value', 'args': {}, 'id': 'read'},
    ]), AIMessage(content='synthetic-value')])
    monkeypatch.setattr(probe.secrets, 'token_hex', lambda _n: 'synthetic-value')
    monkeypatch.setattr(application, 'load_model_catalog', lambda _p: ModelCatalog('A', {'A': model}))
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')
    root = logging.getLogger()
    previous_level = root.level
    try:
        probe._worker(SimpleNamespace(config='unused', model_id=None, with_control=False, with_cli=True))
    finally:
        root.setLevel(previous_level)
    out = capsys.readouterr().out
    assert 'cli_display=verified replies=2' in out
    assert 'CLI_BEFORE_TOOL' not in out and 'synthetic-value' not in out
