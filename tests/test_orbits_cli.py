import json
from unittest.mock import patch

from orbits.cli import orbits_cli


def test_status_reports_tmux_and_monitor_state(capsys):
    with patch("orbits.cli.orbits_cli.daemon_status", return_value={"running": True, "pid": 123}), patch(
        "orbits.cli.orbits_cli._tmux_session_exists", side_effect=[True, False]
    ):
        rc = orbits_cli.main.__wrapped__ if hasattr(orbits_cli.main, "__wrapped__") else None
        del rc
        import sys
        argv = sys.argv
        try:
            sys.argv = ["orbits", "status"]
            result = orbits_cli.main()
        finally:
            sys.argv = argv
    captured = json.loads(capsys.readouterr().out)
    assert result == 0
    assert captured["monitor"]["running"] is True
    assert captured["orchestrator_tmux"] is True
    assert captured["opencode_tmux"] is False


def test_start_requires_component_flag():
    import sys

    argv = sys.argv
    try:
        sys.argv = ["orbits", "start"]
        try:
            orbits_cli.main()
            assert False, "expected SystemExit"
        except SystemExit as exc:
            assert exc.code == 2
    finally:
        sys.argv = argv


def test_start_monitor_invokes_daemon(capsys):
    import sys

    argv = sys.argv
    try:
        sys.argv = ["orbits", "start", "--monitor"]
        with patch("orbits.cli.orbits_cli.start_daemon", return_value={"started": True, "pid": 99}), patch(
            "orbits.cli.orbits_cli.load_config", return_value={}
        ):
            result = orbits_cli.main()
    finally:
        sys.argv = argv
    captured = json.loads(capsys.readouterr().out)
    assert result == 0
    assert captured["monitor"]["started"] is True


def test_dashboard_reports_token_summary(capsys):
    import sys

    argv = sys.argv
    try:
        sys.argv = ["orbits", "dashboard"]
        with patch("orbits.cli.orbits_cli.load_config", return_value={"daemon": {"status_file": "fake.json"}}), patch(
            "orbits.cli.orbits_cli._read_model_status",
            return_value={
                "claude_sonnet": "unknown",
                "gpt_5_4": "active",
                "interface_model": "active",
                "pending_handoff": True,
                "opencode_telemetry": "jsonl",
                "opencode_input_tokens": 10,
                "opencode_cached_input_tokens": 2,
                "opencode_output_tokens": 5,
                "notes": "ok",
            },
        ), patch("orbits.cli.orbits_cli.daemon_status", return_value={"running": True, "pid": 1}), patch(
            "orbits.cli.orbits_cli._tmux_session_exists", side_effect=[True, False]
        ):
            result = orbits_cli.main()
    finally:
        sys.argv = argv
    captured = json.loads(capsys.readouterr().out)
    assert result == 0
    assert captured["tokens"]["total_known_tokens"] == 17
    assert captured["models"]["pending_handoff"] is True
