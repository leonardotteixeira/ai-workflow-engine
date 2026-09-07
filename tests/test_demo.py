"""Fase 12 §12.14/§12.15 — the offline demo script must actually run clean,
end to end, with no network/credentials, and finish with replay agreeing
with the live execution."""

from __future__ import annotations

from workflow_engine import demo


def test_demo_runs_end_to_end_offline(capsys) -> None:
    demo.main()
    output = capsys.readouterr().out
    assert "COMPLETED" in output
    assert "matches live execution: True" in output
    assert "api_key" not in output.lower()
