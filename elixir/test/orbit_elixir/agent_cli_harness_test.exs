defmodule OrbitElixir.AgentCLIHarnessTest do
  use OrbitElixir.TestSupport

  alias OrbitElixir.AgentRuntime.CLIAdapter

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
      assert_receive {:agent_message, %{event: :turn_started, runtime_event: %{event: :turn_started, provider: "gemini"}}}
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

      assert_receive {:agent_message,
                      %{
                        event: :turn_ended_with_error,
                        reason: {:provider_exit, "claude", 42},
                        runtime_event: %{event: :turn_failed, payload: %{"type" => "exit", "exit_status" => 42}}
                      }}
    after
      File.rm_rf(test_root)
    end
  end

  test "implements the runtime adapter callbacks for a successful CLI process" do
    test_root =
      Path.join(
        System.tmp_dir!(),
        "orbit-elixir-cli-adapter-#{System.unique_integer([:positive])}"
      )

    workspace = Path.join(test_root, "DEV-CLI-ADAPTER")

    try do
      File.mkdir_p!(workspace)

      provider = %{
        "name" => "gemini",
        "display_name" => "Gemini",
        "harness" => "cli",
        "command" => "printf 'adapter ok\\n'",
        "model" => "auto",
        "timeout_ms" => 1_000
      }

      issue = %Issue{
        id: "issue-cli-adapter",
        identifier: "DEV-CLI-ADAPTER",
        title: "CLI adapter",
        description: "Run through runtime callbacks.",
        labels: []
      }

      assert {:ok, session} =
               CLIAdapter.start_session(%{
                 workspace: workspace,
                 issue: issue,
                 config: %{provider: provider}
               })

      assert session.provider == "gemini"
      assert session.harness == "cli"

      assert {:ok, turn} = CLIAdapter.send_turn(session, %{prompt: "prompt", issue: issue})

      parent = self()

      assert {:ok, %{status: :completed, provider_result: :turn_completed}} =
               CLIAdapter.stream_events(session, turn, fn event -> send(parent, {:runtime_event, event}) end)

      assert_receive {:runtime_event, %{event: :session_started, provider: "gemini", harness: "cli"}}
      assert_receive {:runtime_event, %{event: :turn_started, provider: "gemini", payload: %{"command" => "printf 'adapter ok\\n'"}}}
      assert_receive {:runtime_event, %{event: :output_delta, payload: %{"line" => "adapter ok"}, raw: "adapter ok"}}
      assert_receive {:runtime_event, %{event: :turn_completed, payload: %{"result" => "turn_completed"}}}
    after
      File.rm_rf(test_root)
    end
  end

  test "returns timeout failures with normalized runtime events" do
    test_root =
      Path.join(
        System.tmp_dir!(),
        "orbit-elixir-cli-provider-timeout-#{System.unique_integer([:positive])}"
      )

    workspace = Path.join(test_root, "DEV-CLI-TIMEOUT")

    try do
      File.mkdir_p!(workspace)

      provider = %{
        "name" => "gemini",
        "display_name" => "Gemini",
        "harness" => "cli",
        "command" => "sleep 0.2",
        "model" => "auto",
        "timeout_ms" => 20
      }

      issue = %Issue{
        id: "issue-cli-timeout",
        identifier: "DEV-CLI-TIMEOUT",
        title: "CLI harness timeout",
        description: "Timeout the CLI harness.",
        labels: []
      }

      parent = self()
      on_message = fn message -> send(parent, {:agent_message, message}) end

      assert {:error, :turn_timeout} =
               AgentCLI.run_turn(provider, workspace, "prompt", issue, on_message: on_message)

      assert_receive {:agent_message,
                      %{
                        event: :turn_ended_with_error,
                        reason: :turn_timeout,
                        runtime_event: %{event: :turn_failed, payload: %{"type" => "timeout", "reason" => "turn_timeout"}}
                      }}
    after
      File.rm_rf(test_root)
    end
  end

  test "emits malformed partial CLI output as raw output delta" do
    test_root =
      Path.join(
        System.tmp_dir!(),
        "orbit-elixir-cli-provider-partial-#{System.unique_integer([:positive])}"
      )

    workspace = Path.join(test_root, "DEV-CLI-PARTIAL")

    try do
      File.mkdir_p!(workspace)

      provider = %{
        "name" => "claude",
        "display_name" => "Claude",
        "harness" => "cli",
        "command" => "printf '{\"partial\":'",
        "model" => "sonnet",
        "timeout_ms" => 1_000
      }

      issue = %Issue{
        id: "issue-cli-partial",
        identifier: "DEV-CLI-PARTIAL",
        title: "CLI harness partial output",
        description: "Emit partial malformed output.",
        labels: []
      }

      parent = self()
      on_message = fn message -> send(parent, {:agent_message, message}) end

      assert {:ok, %{provider: "claude"}} =
               AgentCLI.run_turn(provider, workspace, "prompt", issue, on_message: on_message)

      assert_receive {:agent_message,
                      %{
                        event: :notification,
                        payload: %{"line" => "{\"partial\":"},
                        raw: "{\"partial\":",
                        runtime_event: %{event: :output_delta, payload: %{"line" => "{\"partial\":"}, raw: "{\"partial\":"}
                      }}

      assert_receive {:agent_message, %{event: :turn_completed, runtime_event: %{event: :turn_completed}}}
    after
      File.rm_rf(test_root)
    end
  end
end
