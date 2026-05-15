"""Tests for orbit.config — load_workflow, build_service_config, validate_dispatch_config."""
import tempfile
from pathlib import Path

import pytest

from orbit.config import (
    build_service_config,
    load_workflow,
    validate_dispatch_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp_path: Path, content: str, filename: str = "ORBIT.md") -> Path:
    p = tmp_path / filename
    p.write_text(content)
    return p


def _minimal_workflow(tmp_path: Path, extra_front_matter: str = "", body: str = "Hello {{ issue.identifier }}") -> Path:
    content = f"---\ntracker:\n  kind: file\n  path: ./tasks.yaml\n{extra_front_matter}---\n{body}\n"
    f = _write(tmp_path, content)
    (tmp_path / "tasks.yaml").write_text("tasks: []\n")
    return f


# ---------------------------------------------------------------------------
# load_workflow tests
# ---------------------------------------------------------------------------

class TestLoadWorkflow:
    def test_load_workflow_parses_front_matter_and_body(self, tmp_path):
        content = "---\ntracker:\n  kind: file\n  path: ./tasks.yaml\n---\nMy prompt body\n"
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        assert wf.config["tracker"]["kind"] == "file"
        assert "My prompt body" in wf.prompt_template
        assert wf.path == f

    def test_load_workflow_missing_file_raises(self, tmp_path):
        missing = tmp_path / "NONEXISTENT.md"
        with pytest.raises(FileNotFoundError, match="missing_workflow_file"):
            load_workflow(missing)

    def test_load_workflow_invalid_yaml_raises(self, tmp_path):
        content = "---\n: : invalid: yaml\n  - bad\n---\nbody\n"
        f = _write(tmp_path, content)
        with pytest.raises(ValueError, match="workflow_parse_error"):
            load_workflow(f)

    def test_load_workflow_non_map_front_matter_raises(self, tmp_path):
        content = "---\n- item1\n- item2\n---\nbody\n"
        f = _write(tmp_path, content)
        with pytest.raises(ValueError, match="workflow_front_matter_not_a_map"):
            load_workflow(f)

    def test_load_workflow_no_front_matter(self, tmp_path):
        """File without '---' delimiter — entire text becomes prompt, config is empty."""
        content = "Just a plain prompt with no front matter."
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        assert wf.config == {}
        assert "plain prompt" in wf.prompt_template

    def test_load_workflow_empty_front_matter(self, tmp_path):
        """Empty YAML front matter block yields empty config dict."""
        content = "---\n---\nMy prompt\n"
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        assert wf.config == {}
        assert "My prompt" in wf.prompt_template

    def test_load_workflow_body_stripped(self, tmp_path):
        """Prompt body has leading/trailing whitespace stripped."""
        content = "---\ntracker:\n  kind: file\n---\n\n  hello world  \n\n"
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        assert wf.prompt_template == "hello world"


# ---------------------------------------------------------------------------
# build_service_config / config defaults
# ---------------------------------------------------------------------------

class TestBuildServiceConfig:
    def test_config_defaults_apply(self, tmp_path):
        """An entirely empty front matter produces sensible defaults."""
        content = "---\n---\nPrompt\n"
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        cfg = build_service_config(wf)

        # TrackerConfig defaults
        assert cfg.tracker.kind == ""
        # PollingConfig default
        assert cfg.polling.interval_ms == 30_000
        # HooksConfig default timeout
        assert cfg.hooks.timeout_ms == 60_000
        # AgentConfig defaults
        assert cfg.agent.max_concurrent_agents == 10
        assert cfg.agent.max_turns == 20
        # RunnerConfig default
        assert cfg.runner.kind == "cli"
        # KnowledgeConfig defaults
        assert cfg.knowledge.enabled is False
        assert cfg.knowledge.inject_context is True
        assert cfg.knowledge.ingest_results is True
        assert cfg.knowledge.context_max_tokens == 2_000

    def test_workspace_root_relative_resolves_to_absolute(self, tmp_path):
        """workspace.root relative path is resolved relative to workflow_dir."""
        content = "---\nworkspace:\n  root: ./workspaces\n---\nPrompt\n"
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        cfg = build_service_config(wf)
        expected = str((tmp_path / "workspaces").resolve())
        assert cfg.workspace.root == expected

    def test_workspace_root_default_is_tempdir(self, tmp_path):
        """workspace.root defaults to tempdir-based path when not specified."""
        content = "---\n---\nPrompt\n"
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        cfg = build_service_config(wf)
        # Should be under system temp
        assert tempfile.gettempdir() in cfg.workspace.root
        assert "orbit_workspaces" in cfg.workspace.root

    def test_var_resolution(self, tmp_path, monkeypatch):
        """$VAR_NAME in api_key is expanded from environment."""
        monkeypatch.setenv("MY_API_KEY", "secret-token-xyz")
        content = "---\ntracker:\n  kind: linear\n  api_key: $MY_API_KEY\n  project_slug: myproject\n---\nPrompt\n"
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        cfg = build_service_config(wf)
        assert cfg.tracker.api_key == "secret-token-xyz"

    def test_var_resolution_missing_env_gives_empty(self, tmp_path, monkeypatch):
        """$UNSET_VAR resolves to empty string."""
        monkeypatch.delenv("UNSET_ORB_VAR", raising=False)
        content = "---\ntracker:\n  kind: linear\n  api_key: $UNSET_ORB_VAR\n  project_slug: myproject\n---\nPrompt\n"
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        cfg = build_service_config(wf)
        assert cfg.tracker.api_key == ""

    def test_path_expansion_tilde(self, tmp_path):
        """~ in workspace.root is expanded to home directory."""
        content = "---\nworkspace:\n  root: ~/myworkspaces\n---\nPrompt\n"
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        cfg = build_service_config(wf)
        assert not cfg.workspace.root.startswith("~")
        assert "myworkspaces" in cfg.workspace.root

    def test_tracker_path_resolved_relative_to_workflow(self, tmp_path):
        """tracker.path for file tracker is resolved relative to workflow file dir."""
        content = "---\ntracker:\n  kind: file\n  path: ./tasks.yaml\n---\nPrompt\n"
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        cfg = build_service_config(wf)
        expected = str((tmp_path / "tasks.yaml").resolve())
        assert cfg.tracker.path == expected

    def test_agent_max_turns_invalid_raises(self, tmp_path):
        """agent.max_turns <= 0 raises ValueError."""
        content = "---\nagent:\n  max_turns: 0\n---\nPrompt\n"
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        with pytest.raises(ValueError, match="max_turns"):
            build_service_config(wf)

    def test_agent_max_turns_negative_raises(self, tmp_path):
        content = "---\nagent:\n  max_turns: -5\n---\nPrompt\n"
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        with pytest.raises(ValueError, match="max_turns"):
            build_service_config(wf)

    def test_hooks_timeout_invalid_raises(self, tmp_path):
        """hooks.timeout_ms of zero or negative raises ValueError."""
        content = "---\nhooks:\n  timeout_ms: 0\n---\nPrompt\n"
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        with pytest.raises(ValueError, match="timeout_ms"):
            build_service_config(wf)

    def test_hooks_timeout_negative_raises(self, tmp_path):
        content = "---\nhooks:\n  timeout_ms: -100\n---\nPrompt\n"
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        with pytest.raises(ValueError, match="timeout_ms"):
            build_service_config(wf)

    def test_per_state_concurrency_normalizes_lowercase(self, tmp_path):
        """max_concurrent_agents_by_state keys are lowercased."""
        content = (
            "---\nagent:\n  max_concurrent_agents_by_state:\n"
            "    Todo: 2\n    In Progress: 3\n---\nPrompt\n"
        )
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        cfg = build_service_config(wf)
        by_state = cfg.agent.max_concurrent_agents_by_state
        assert "todo" in by_state
        assert "in progress" in by_state
        assert by_state["todo"] == 2
        assert by_state["in progress"] == 3

    def test_knowledge_config_parsed(self, tmp_path):
        """Knowledge block is fully parsed."""
        content = (
            "---\nknowledge:\n"
            "  enabled: true\n"
            "  akms_root: /some/path\n"
            "  inject_context: false\n"
            "  ingest_results: false\n"
            "  context_max_tokens: 4000\n"
            "  context_sections:\n"
            "    - architecture\n"
            "    - api\n"
            "---\nPrompt\n"
        )
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        cfg = build_service_config(wf)
        k = cfg.knowledge
        assert k.enabled is True
        assert k.akms_root == "/some/path"
        assert k.inject_context is False
        assert k.ingest_results is False
        assert k.context_max_tokens == 4000
        assert k.context_sections == ["architecture", "api"]

    def test_knowledge_config_path_defaults_from_akms_root(self, tmp_path):
        """When config_path is absent, it is derived from akms_root."""
        content = "---\nknowledge:\n  enabled: true\n  akms_root: /my/akms\n---\nPrompt\n"
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        cfg = build_service_config(wf)
        assert cfg.knowledge.config_path == "/my/akms/akms_config.yaml"

    def test_agents_registry_parsed(self, tmp_path):
        """agents.registry entries are parsed into AgentEntry objects."""
        content = (
            "---\nagents:\n"
            "  default: specialized\n"
            "  registry:\n"
            "    specialized:\n"
            "      capabilities:\n"
            "        - Python\n"
            "        - Testing\n"
            "      weight: 8\n"
            "    fallback:\n"
            "      capabilities: []\n"
            "      weight: 1\n"
            "---\nPrompt\n"
        )
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        cfg = build_service_config(wf)
        assert cfg.agents.default == "specialized"
        registry = cfg.agents.registry
        assert "specialized" in registry
        assert "fallback" in registry
        # capabilities are lowercased
        assert "python" in registry["specialized"].capabilities
        assert "testing" in registry["specialized"].capabilities
        assert registry["specialized"].weight == 8
        assert registry["fallback"].weight == 1

    def test_runner_commands_parsed(self, tmp_path):
        content = (
            "---\nrunner:\n  kind: cli\n  commands:\n"
            "    codex: 'codex run'\n    gemma: 'gemma run'\n---\nPrompt\n"
        )
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        cfg = build_service_config(wf)
        assert cfg.runner.kind == "cli"
        assert cfg.runner.commands["codex"] == "codex run"
        assert cfg.runner.commands["gemma"] == "gemma run"

    def test_workflow_dir_is_set(self, tmp_path):
        f = _minimal_workflow(tmp_path)
        wf = load_workflow(f)
        cfg = build_service_config(wf)
        assert cfg.workflow_dir == tmp_path


# ---------------------------------------------------------------------------
# validate_dispatch_config tests
# ---------------------------------------------------------------------------

class TestValidateDispatchConfig:
    def _build(self, tmp_path: Path, extra_front_matter: str) -> object:
        content = f"---\n{extra_front_matter}---\nPrompt\n"
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        return build_service_config(wf)

    def test_validate_dispatch_config_linear_missing_key(self, tmp_path):
        cfg = self._build(
            tmp_path,
            "tracker:\n  kind: linear\n  project_slug: myproject\n  api_key: ''\n",
        )
        errors = validate_dispatch_config(cfg)
        assert any("api_key" in e or "missing_tracker_api_key" in e for e in errors)

    def test_validate_dispatch_config_linear_missing_slug(self, tmp_path):
        cfg = self._build(
            tmp_path,
            "tracker:\n  kind: linear\n  api_key: tok\n",
        )
        errors = validate_dispatch_config(cfg)
        assert any("project_slug" in e or "missing_tracker_project_slug" in e for e in errors)

    def test_validate_dispatch_config_file_missing_path(self, tmp_path):
        """file tracker without path → validation error."""
        content = "---\ntracker:\n  kind: file\n---\nPrompt\n"
        f = _write(tmp_path, content)
        wf = load_workflow(f)
        cfg = build_service_config(wf)
        errors = validate_dispatch_config(cfg)
        assert any("path" in e for e in errors)

    def test_validate_dispatch_config_unknown_kind(self, tmp_path):
        cfg = self._build(tmp_path, "tracker:\n  kind: unknown_tracker\n")
        errors = validate_dispatch_config(cfg)
        assert any("unsupported_tracker_kind" in e for e in errors)

    def test_validate_dispatch_config_no_kind_errors(self, tmp_path):
        """Missing tracker.kind → 'tracker.kind is required' error."""
        cfg = self._build(tmp_path, "tracker:\n  endpoint: https://example.com\n")
        errors = validate_dispatch_config(cfg)
        assert any("tracker.kind" in e for e in errors)

    def test_validate_dispatch_config_valid_file_tracker(self, tmp_path):
        """A fully valid file-tracker config produces no errors."""
        f = _minimal_workflow(tmp_path)
        wf = load_workflow(f)
        cfg = build_service_config(wf)
        errors = validate_dispatch_config(cfg)
        assert errors == []

    def test_validate_dispatch_config_valid_linear_tracker(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MY_LINEAR_KEY", "lin_api_xyz")
        cfg = self._build(
            tmp_path,
            "tracker:\n  kind: linear\n  api_key: $MY_LINEAR_KEY\n  project_slug: my-project\n",
        )
        errors = validate_dispatch_config(cfg)
        assert errors == []

    def test_validate_dispatch_config_github_missing_repo(self, tmp_path):
        cfg = self._build(
            tmp_path,
            "tracker:\n  kind: github\n  api_key: ghp_token\n",
        )
        errors = validate_dispatch_config(cfg)
        assert any("repo" in e for e in errors)

    def test_validate_dispatch_config_github_missing_token(self, tmp_path):
        cfg = self._build(
            tmp_path,
            "tracker:\n  kind: github\n  repo: owner/repo\n  api_key: ''\n",
        )
        errors = validate_dispatch_config(cfg)
        assert any("api_key" in e or "missing_tracker_api_key" in e for e in errors)
