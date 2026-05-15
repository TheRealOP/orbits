"""Tests for orbit.knowledge — KnowledgeClient with mocked subprocess calls."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from orbit.knowledge import KnowledgeClient
from orbit.config import KnowledgeConfig
from orbit.models import Issue


def make_cfg(enabled=True, inject=True, ingest=True) -> KnowledgeConfig:
    cfg = KnowledgeConfig()
    cfg.enabled = enabled
    cfg.akms_root = "/fake/akms"
    cfg.config_path = "/fake/akms/config.yaml"
    cfg.inject_context = inject
    cfg.ingest_results = ingest
    cfg.context_max_tokens = 100
    cfg.ingest_on_states = ["orbit:review"]
    return cfg


def simple_issue() -> Issue:
    return Issue(id="1", identifier="T-1", title="Fix auth bug", description="Auth is broken", state="todo")


@pytest.mark.asyncio
async def test_disabled_returns_false():
    cfg = make_cfg(enabled=False)
    client = KnowledgeClient(cfg)
    result = await client.check_availability()
    assert result is False
    assert not client.available


@pytest.mark.asyncio
async def test_no_akms_binary_returns_false(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    cfg = make_cfg()
    client = KnowledgeClient(cfg)
    result = await client.check_availability()
    assert result is False


@pytest.mark.asyncio
async def test_get_context_returns_empty_when_not_available():
    cfg = make_cfg()
    client = KnowledgeClient(cfg)
    # _available is False by default
    context, nodes = await client.get_context(simple_issue())
    assert context == ""
    assert nodes == []


@pytest.mark.asyncio
async def test_get_context_truncates_to_max_tokens(monkeypatch):
    cfg = make_cfg()
    cfg.context_max_tokens = 10  # very small → 40 chars
    client = KnowledgeClient(cfg)
    client._available = True  # force available

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        # First call: akms search → returns a section name
        search_proc = MagicMock()
        search_proc.returncode = 0
        search_proc.communicate = AsyncMock(return_value=(b"some_section\n", b""))

        # Second call: akms ask → returns 1000 chars of context
        ask_proc = MagicMock()
        ask_proc.returncode = 0
        ask_proc.communicate = AsyncMock(return_value=(b"x" * 1000, b""))

        mock_exec.side_effect = [search_proc, ask_proc]
        context, nodes = await client.get_context(simple_issue())
        # Should be truncated to context_max_tokens * 4 = 40 chars
        assert len(context) <= 40 + 1  # slight tolerance


@pytest.mark.asyncio
async def test_ingest_result_no_error_when_unavailable():
    cfg = make_cfg()
    client = KnowledgeClient(cfg)
    # Should return None without error
    result = await client.ingest_result(simple_issue(), "claude", 3, 1000, "/tmp/ws")
    assert result is None


@pytest.mark.asyncio
async def test_ingest_failure_returns_none_not_raises(monkeypatch):
    cfg = make_cfg()
    client = KnowledgeClient(cfg)
    client._available = True

    with patch("asyncio.create_subprocess_exec", side_effect=Exception("akms down")):
        result = await client.ingest_result(simple_issue(), "claude", 2, 500, "/tmp/ws")
    assert result is None


@pytest.mark.asyncio
async def test_available_property_false_when_disabled():
    cfg = make_cfg(enabled=False)
    client = KnowledgeClient(cfg)
    client._available = True  # underlying flag is True but config says disabled
    assert client.available is False


@pytest.mark.asyncio
async def test_available_property_true_when_enabled_and_flag_set():
    cfg = make_cfg(enabled=True)
    client = KnowledgeClient(cfg)
    client._available = True
    assert client.available is True


@pytest.mark.asyncio
async def test_get_context_disabled_inject_returns_empty():
    # inject_context=False → should return empty even if available
    cfg = make_cfg(inject=False)
    client = KnowledgeClient(cfg)
    client._available = True
    context, nodes = await client.get_context(simple_issue())
    assert context == ""
    assert nodes == []


@pytest.mark.asyncio
async def test_ingest_disabled_returns_none():
    # ingest_results=False → ingest_result returns None without calling akms
    cfg = make_cfg(ingest=False)
    client = KnowledgeClient(cfg)
    client._available = True
    result = await client.ingest_result(simple_issue(), "claude", 1, 100, "/tmp/ws")
    assert result is None


@pytest.mark.asyncio
async def test_check_availability_no_config_file_returns_false(monkeypatch, tmp_path):
    # Binary exists but config file does not
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/akms" if name == "akms" else None)
    cfg = make_cfg()
    cfg.config_path = str(tmp_path / "nonexistent_config.yaml")
    client = KnowledgeClient(cfg)
    result = await client.check_availability()
    assert result is False
    assert not client.available


@pytest.mark.asyncio
async def test_check_availability_status_nonzero_returns_false(monkeypatch, tmp_path):
    # akms binary exists, config file exists, but status returns non-zero
    config_file = tmp_path / "config.yaml"
    config_file.write_text("dummy: config\n")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/akms" if name == "akms" else None)
    cfg = make_cfg()
    cfg.config_path = str(config_file)

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))
        mock_exec.return_value = mock_proc

        client = KnowledgeClient(cfg)
        result = await client.check_availability()
    assert result is False
    assert not client.available


@pytest.mark.asyncio
async def test_check_availability_status_zero_returns_true(monkeypatch, tmp_path):
    # akms binary exists, config file exists, status returns zero
    config_file = tmp_path / "config.yaml"
    config_file.write_text("dummy: config\n")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/akms" if name == "akms" else None)
    cfg = make_cfg()
    cfg.config_path = str(config_file)

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
        mock_exec.return_value = mock_proc

        client = KnowledgeClient(cfg)
        result = await client.check_availability()
    assert result is True
    assert client.available is True


@pytest.mark.asyncio
async def test_get_context_search_failure_returns_empty():
    cfg = make_cfg()
    client = KnowledgeClient(cfg)
    client._available = True

    with patch("asyncio.create_subprocess_exec", side_effect=Exception("search failed")):
        context, nodes = await client.get_context(simple_issue())
    assert context == ""
    assert nodes == []


@pytest.mark.asyncio
async def test_get_context_empty_search_output_returns_empty():
    cfg = make_cfg()
    client = KnowledgeClient(cfg)
    client._available = True

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        # search returns empty output → no section found
        search_proc = MagicMock()
        search_proc.returncode = 0
        search_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_exec.return_value = search_proc

        context, nodes = await client.get_context(simple_issue())
    assert context == ""
    assert nodes == []
