defmodule OrbitElixir.AgentCLIHarnessTest do
  use OrbitElixir.TestSupport

  test "runs a configured CLI provider with prompt and provider environment" do
    test_root =
      Path.join(
        System.tmp_dir!(),
        "orbit-elixir-cli-provider-#{System.unique_integer([:positive])}"
      )

    workspace = Path.join(test_root, "DEV-CLI")
    trace_file = Path.join(workspace, "agent.trace")

    try do
      File.mkdir_p!(workspace)

      provider = %{
        "name" => "gemini",
        "display_name" => "Gemini",
        "harness" => "cli",
        "command" =>
          "printf '%s\\n' " <>
            "\"$ORBIT_AGENT_PROVIDER|$ORBIT_AGENT_MODEL|$ORBIT_ISSUE_IDENTIFIER|$ORBIT_AGENT_PROMPT\" " <>
            "> agent.trace && printf 'done\\n'",
        "model" => "auto",
        "timeout_ms" => 1_000
      }

      issue = %Issue{
        id: "issue-cli",
        identifier: "DEV-CLI",
        title: "CLI harness",
        description: "Run the CLI harness.",
        labels: []
      }

      prompt = "Implement the visual layout."
      parent = self()
      on_message = fn message -> send(parent, {:agent_message, message}) end

      assert {:ok, result} =
               AgentCLI.run_turn(provider, workspace, prompt, issue, on_message: on_message)

      assert result.provider == "gemini"
      assert File.read!(trace_file) == "gemini|auto|DEV-CLI|#{prompt}\n"

      assert_receive {:agent_message, %{event: :session_started, agent_provider: "gemini"}}
      assert_receive {:agent_message, %{event: :notification, payload: %{"line" => "done"}}}
      assert_receive {:agent_message, %{event: :turn_completed, agent_provider: "gemini"}}
    after
      File.rm_rf(test_root)
    end
  end

  test "returns provider exit status failures" do
    test_root =
      Path.join(
        System.tmp_dir!(),
        "orbit-elixir-cli-provider-failure-#{System.unique_integer([:positive])}"
      )

    workspace = Path.join(test_root, "DEV-CLI-FAIL")

    try do
      File.mkdir_p!(workspace)

      provider = %{
        "name" => "claude",
        "display_name" => "Claude",
        "harness" => "cli",
        "command" => "printf 'failed\\n' && exit 42",
        "model" => "sonnet",
        "timeout_ms" => 1_000
      }

      issue = %Issue{
        id: "issue-cli-fail",
        identifier: "DEV-CLI-FAIL",
        title: "CLI harness failure",
        description: "Fail the CLI harness.",
        labels: []
      }

      parent = self()
      on_message = fn message -> send(parent, {:agent_message, message}) end

      assert {:error, {:provider_exit, "claude", 42}} =
               AgentCLI.run_turn(provider, workspace, "prompt", issue, on_message: on_message)

      assert_receive {:agent_message, %{event: :notification, payload: %{"line" => "failed"}}}
      assert_receive {:agent_message, %{event: :turn_ended_with_error, reason: {:provider_exit, "claude", 42}}}
    after
      File.rm_rf(test_root)
    end
  end
end
