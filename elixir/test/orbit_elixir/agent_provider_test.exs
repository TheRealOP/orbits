defmodule OrbitElixir.AgentProviderTest do
  use OrbitElixir.TestSupport

  test "routes UI and UX work to Gemini by default" do
    issue = %Issue{
      id: "issue-ui",
      identifier: "DEV-UI",
      title: "Polish the dashboard responsive layout",
      description: "Improve the mobile UI and visual spacing.",
      labels: ["frontend"]
    }

    assert {:ok, provider, reason} = AgentProvider.select(issue)
    assert provider["name"] == "gemini"
    assert provider["harness"] == "cli"
    assert provider["command"] =~ "--prompt \"$ORBIT_AGENT_PROMPT\""
    assert provider["command"] =~ "--output-format json"
    assert provider["command"] =~ "--approval-mode=yolo"
    assert provider["output_format"] == "json"
    assert reason =~ "gemini"
  end

  test "routes analysis and documentation work to Claude by default" do
    issue = %Issue{
      id: "issue-docs",
      identifier: "DEV-DOCS",
      title: "Write architecture docs",
      description: "Analyze the orchestration strategy and document it.",
      labels: []
    }

    assert {:ok, provider, reason} = AgentProvider.select(issue)
    assert provider["name"] == "claude"
    assert provider["harness"] == "claude_agent_sdk"
    assert reason =~ "claude"
  end

  test "falls back to Codex and keeps the legacy codex command" do
    write_workflow_file!(Workflow.workflow_file_path(),
      codex_command: "codex --config 'model=\"gpt-5.5\"' app-server"
    )

    issue = %Issue{
      id: "issue-backend",
      identifier: "DEV-BACKEND",
      title: "Fix failing backend tests",
      description: "The retry worker crashes on errors.",
      labels: ["backend"]
    }

    assert {:ok, provider, reason} = AgentProvider.select(issue)
    assert provider["name"] == "codex"
    assert provider["harness"] == "codex_app_server"
    assert provider["command"] == "codex --config 'model=\"gpt-5.5\"' app-server"
    assert reason == "default provider codex"
  end

  test "supports configured future providers and routes" do
    write_workflow_file!(Workflow.workflow_file_path(),
      providers: %{
        opencode: %{
          harness: "cli",
          command: "opencode run \"$ORBIT_AGENT_PROMPT\"",
          model: "future-large",
          timeout_ms: 1234
        }
      },
      agent_provider_routes: [
        %{
          provider: "opencode",
          keywords: ["security hardening"],
          labels: ["security"]
        }
      ]
    )

    issue = %Issue{
      id: "issue-security",
      identifier: "DEV-SEC",
      title: "Security hardening pass",
      description: "Tighten execution defaults.",
      labels: []
    }

    assert :ok = Config.validate!()
    assert {:ok, provider, reason} = AgentProvider.select(issue)
    assert provider["name"] == "opencode"
    assert provider["display_name"] == "Opencode"
    assert provider["harness"] == "cli"
    assert provider["command"] == "opencode run \"$ORBIT_AGENT_PROMPT\""
    assert provider["model"] == "future-large"
    assert provider["timeout_ms"] == 1234
    assert reason =~ "opencode"
  end

  test "validates provider route references" do
    write_workflow_file!(Workflow.workflow_file_path(),
      agent_provider_routes: [
        %{provider: "missing", keywords: ["anything"]}
      ]
    )

    assert {:error, {:invalid_workflow_config, message}} = Config.validate!()
    assert message =~ "agent.provider_routes"
    assert message =~ "missing"
  end
end
