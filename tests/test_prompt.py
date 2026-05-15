"""Tests for orbit.prompt — strict Jinja2 rendering (Symphony §5.4 + Orbit §7)."""
import pytest

from orbit.models import Issue
from orbit.prompt import render_prompt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render(template: str, issue: Issue, **kwargs) -> str:
    return render_prompt(template, issue, **kwargs)


# ---------------------------------------------------------------------------
# Basic field rendering
# ---------------------------------------------------------------------------

class TestRenderBasicIssueFields:
    def test_render_identifier(self, simple_issue):
        result = _render("{{ issue.identifier }}", simple_issue)
        assert result == "TASK-1"

    def test_render_title(self, simple_issue):
        result = _render("{{ issue.title }}", simple_issue)
        assert result == "Test task"

    def test_render_state(self, simple_issue):
        result = _render("{{ issue.state }}", simple_issue)
        assert result == "Todo"

    def test_render_id(self, simple_issue):
        result = _render("{{ issue.id }}", simple_issue)
        assert result == "1"

    def test_render_description_none(self, simple_issue):
        result = _render("{{ issue.description }}", simple_issue)
        assert result == "None"

    def test_render_description_set(self):
        issue = Issue(id="2", identifier="TASK-2", title="With desc", description="Some desc")
        result = _render("{{ issue.description }}", issue)
        assert result == "Some desc"

    def test_render_multiple_fields(self, simple_issue):
        result = _render(
            "Working on {{ issue.identifier }}: {{ issue.title }} (state={{ issue.state }})",
            simple_issue,
        )
        assert result == "Working on TASK-1: Test task (state=Todo)"


# ---------------------------------------------------------------------------
# attempt variable
# ---------------------------------------------------------------------------

class TestRenderAttempt:
    def test_render_attempt_null(self, simple_issue):
        result = _render("attempt={{ attempt }}", simple_issue, attempt=None)
        assert result == "attempt=None"

    def test_render_attempt_integer(self, simple_issue):
        result = _render("attempt={{ attempt }}", simple_issue, attempt=3)
        assert result == "attempt=3"

    def test_render_attempt_default_is_none(self, simple_issue):
        """Default attempt value is None."""
        result = _render("{{ attempt }}", simple_issue)
        assert result == "None"


# ---------------------------------------------------------------------------
# Strict undefined — unknown variables raise
# ---------------------------------------------------------------------------

class TestRenderStrictUndefined:
    def test_render_unknown_variable_raises(self, simple_issue):
        """Accessing an undefined variable must raise ValueError (strict mode)."""
        with pytest.raises(ValueError, match="template_render_error"):
            _render("{{ undefined_var }}", simple_issue)

    def test_render_unknown_issue_field_raises(self, simple_issue):
        with pytest.raises(ValueError, match="template_render_error"):
            _render("{{ issue.nonexistent_field }}", simple_issue)

    def test_render_nested_undefined_raises(self, simple_issue):
        with pytest.raises(ValueError, match="template_render_error"):
            _render("{{ issue.labels.nonexistent }}", simple_issue)


# ---------------------------------------------------------------------------
# Named keyword variables
# ---------------------------------------------------------------------------

class TestRenderNamedVariables:
    def test_render_agent_variable(self, simple_issue):
        result = _render("agent={{ agent }}", simple_issue, agent="codex")
        assert result == "agent=codex"

    def test_render_agent_default_empty(self, simple_issue):
        result = _render("agent={{ agent }}", simple_issue)
        assert result == "agent="

    def test_render_context_variable(self, simple_issue):
        result = _render("ctx={{ context }}", simple_issue, context="my context")
        assert result == "ctx=my context"

    def test_render_context_default_empty(self, simple_issue):
        result = _render("ctx={{ context }}", simple_issue)
        assert result == "ctx="

    def test_render_graph_nodes_variable(self, simple_issue):
        result = _render("nodes={{ graph_nodes }}", simple_issue, graph_nodes=["a", "b"])
        assert "a" in result
        assert "b" in result

    def test_render_graph_nodes_default_empty_list(self, simple_issue):
        result = _render("nodes={{ graph_nodes }}", simple_issue)
        assert result == "nodes=[]"

    def test_render_workspace_path(self, simple_issue):
        result = _render("ws={{ workspace_path }}", simple_issue, workspace_path="/tmp/ws/TASK-1")
        assert result == "ws=/tmp/ws/TASK-1"


# ---------------------------------------------------------------------------
# Conditional ({% if %}) blocks
# ---------------------------------------------------------------------------

class TestRenderConditionalBlocks:
    def test_render_if_context_block_with_context(self, simple_issue):
        template = "{% if context %}Context: {{ context }}{% endif %}"
        result = _render(template, simple_issue, context="some knowledge")
        assert result == "Context: some knowledge"

    def test_render_if_context_block_without_context(self, simple_issue):
        template = "{% if context %}Context: {{ context }}{% endif %}"
        result = _render(template, simple_issue, context="")
        assert result == ""

    def test_render_if_attempt_block(self, simple_issue):
        template = "{% if attempt %}Retry #{{ attempt }}{% else %}First run{% endif %}"
        assert "First run" in _render(template, simple_issue, attempt=None)
        assert "Retry #2" in _render(template, simple_issue, attempt=2)

    def test_render_if_graph_nodes_block(self, simple_issue):
        template = "{% if graph_nodes %}Nodes: {{ graph_nodes | join(', ') }}{% endif %}"
        result_with = _render(template, simple_issue, graph_nodes=["x", "y"])
        assert "Nodes: x, y" in result_with
        result_empty = _render(template, simple_issue, graph_nodes=[])
        assert result_empty == ""


# ---------------------------------------------------------------------------
# Iteration ({% for %}) blocks
# ---------------------------------------------------------------------------

class TestRenderIterableFields:
    def test_render_issue_labels_iterable_empty(self, simple_issue):
        template = "{% for label in issue.labels %}{{ label }},{% endfor %}"
        assert _render(template, simple_issue) == ""

    def test_render_issue_labels_iterable_with_values(self):
        issue = Issue(id="1", identifier="T-1", title="x", labels=["bug", "urgent"])
        template = "{% for label in issue.labels %}{{ label }}{% if not loop.last %},{% endif %}{% endfor %}"
        result = render_prompt(template, issue)
        assert result == "bug,urgent"

    def test_render_issue_blocked_by_iterable(self):
        from orbit.models import BlockerRef
        issue = Issue(
            id="1", identifier="T-1", title="x",
            blocked_by=[BlockerRef(id="99", identifier="TASK-99", state="Todo")]
        )
        template = "{% for b in issue.blocked_by %}{{ b.identifier }}{% endfor %}"
        result = render_prompt(template, issue)
        assert result == "TASK-99"

    def test_render_graph_nodes_for_loop(self, simple_issue):
        template = "{% for n in graph_nodes %}{{ n }}{% endfor %}"
        result = _render(template, simple_issue, graph_nodes=["n1", "n2"])
        assert result == "n1n2"


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

class TestRenderErrors:
    def test_render_invalid_syntax_raises(self, simple_issue):
        with pytest.raises(ValueError, match="template_parse_error"):
            _render("{% if %}", simple_issue)

    def test_render_unclosed_block_raises(self, simple_issue):
        with pytest.raises(ValueError, match="template_parse_error"):
            _render("{% for x in issue.labels %}", simple_issue)

    def test_render_invalid_filter_raises(self, simple_issue):
        with pytest.raises(ValueError):
            _render("{{ issue.identifier | nonexistent_filter }}", simple_issue)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestRenderEdgeCases:
    def test_render_empty_template_returns_empty(self, simple_issue):
        result = _render("", simple_issue)
        assert result == ""

    def test_render_whitespace_only_template(self, simple_issue):
        result = _render("   ", simple_issue)
        assert result == "   "

    def test_render_plain_text_no_variables(self, simple_issue):
        result = _render("Hello world", simple_issue)
        assert result == "Hello world"

    def test_render_issue_dict_has_expected_keys(self, simple_issue):
        """Smoke test that issue dict exposes all model fields to template."""
        template = (
            "{{ issue.id }}-{{ issue.identifier }}-{{ issue.title }}"
            "-{{ issue.state }}-{{ issue.priority }}-{{ issue.url }}"
        )
        result = _render(template, simple_issue)
        assert "TASK-1" in result
        assert "Test task" in result
        assert "Todo" in result

    def test_render_selected_agent_field(self):
        """Orbit §7: selected_agent is accessible on issue dict."""
        issue = Issue(id="1", identifier="O-1", title="t", selected_agent="gemma")
        result = render_prompt("{{ issue.selected_agent }}", issue)
        assert result == "gemma"

    def test_render_knowledge_context_field(self):
        """Orbit §7: knowledge_context is accessible on issue dict."""
        issue = Issue(id="1", identifier="O-1", title="t", knowledge_context="ctx text")
        result = render_prompt("{{ issue.knowledge_context }}", issue)
        assert result == "ctx text"
