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

  test "extracts Gemini JSON success output and reports workspace diffs" do
    test_root =
      Path.join(
        System.tmp_dir!(),
        "orbit-elixir-cli-provider-gemini-success-#{System.unique_integer([:positive])}"
      )

    workspace = Path.join(test_root, "DEV-CLI-GEMINI-SUCCESS")
    tracked_file = Path.join(workspace, "tracked.txt")

    try do
      File.mkdir_p!(workspace)
      assert {_output, 0} = System.cmd("git", ["init"], cd: workspace, stderr_to_stdout: true)
      File.write!(tracked_file, "before\n")
      assert {_output, 0} = System.cmd("git", ["add", "tracked.txt"], cd: workspace, stderr_to_stdout: true)

      provider = %{
        "name" => "gemini",
        "display_name" => "Gemini",
        "harness" => "cli",
        "command" =>
          "printf 'after\\n' > tracked.txt && " <>
            "printf '%s\\n' 'Gemini notice' '{\"response\":\"Gemini completed\",\"stats\":{\"tools\":{\"totalCalls\":1}}}'",
        "model" => "auto",
        "output_format" => "json",
        "timeout_ms" => 1_000
      }

      issue = %Issue{
        id: "issue-cli-gemini-success",
        identifier: "DEV-CLI-GEMINI-SUCCESS",
        title: "CLI Gemini success",
        description: "Parse Gemini JSON output.",
        labels: []
      }

      assert {:ok, session} =
               CLIAdapter.start_session(%{
                 workspace: workspace,
                 issue: issue,
                 config: %{provider: provider}
               })

      assert {:ok, turn} = CLIAdapter.send_turn(session, %{prompt: "prompt", issue: issue})

      parent = self()

      assert {:ok,
              %{
                status: :completed,
                message: "Gemini completed",
                usage: %{"tools" => %{"totalCalls" => 1}},
                provider_result: %{response: "Gemini completed", output_format: "json"}
              }} =
               CLIAdapter.stream_events(session, turn, fn event -> send(parent, {:runtime_event, event}) end)

      assert_receive {:runtime_event, %{event: :turn_started, payload: %{"output_format" => "json", "timeout_ms" => 1_000}}}

      assert_receive {:runtime_event,
                      %{
                        event: :output_delta,
                        payload: %{
                          "line" => "Gemini completed",
                          "source" => "provider_response",
                          "stream" => "stdout",
                          "structured" => true
                        },
                        raw: "Gemini completed"
                      }}

      assert_receive {:runtime_event,
                      %{
                        event: :diff_changed,
                        payload: %{
                          "method" => "workspace/diffChanged",
                          "files" => ["tracked.txt"],
                          "diff" => diff
                        }
                      }}

      assert diff =~ "-before"
      assert diff =~ "+after"

      assert_receive {:runtime_event,
                      %{
                        event: :turn_completed,
                        raw: %{"response" => "Gemini completed"},
                        payload: %{
                          "result" => "turn_completed",
                          "output_format" => "json",
                          "response" => "Gemini completed",
                          "stats" => %{"tools" => %{"totalCalls" => 1}},
                          "diff" => %{"files" => ["tracked.txt"]}
                        }
                      }}
    after
      File.rm_rf(test_root)
    end
  end

  test "returns Gemini provider errors from structured output" do
    test_root =
      Path.join(
        System.tmp_dir!(),
        "orbit-elixir-cli-provider-gemini-provider-error-#{System.unique_integer([:positive])}"
      )

    workspace = Path.join(test_root, "DEV-CLI-GEMINI-PROVIDER-ERROR")

    try do
      File.mkdir_p!(workspace)

      provider = %{
        "name" => "gemini",
        "display_name" => "Gemini",
        "harness" => "cli",
        "command" => "printf '%s\\n' '{\"error\":{\"message\":\"quota exceeded\"}}'",
        "model" => "auto",
        "output_format" => "json",
        "timeout_ms" => 1_000
      }

      issue = %Issue{
        id: "issue-cli-gemini-provider-error",
        identifier: "DEV-CLI-GEMINI-PROVIDER-ERROR",
        title: "CLI Gemini provider error",
        description: "Surface Gemini JSON errors.",
        labels: []
      }

      parent = self()
      on_message = fn message -> send(parent, {:agent_message, message}) end

      assert {:error, {:provider_error, "gemini", %{"message" => "quota exceeded"}}} =
               AgentCLI.run_turn(provider, workspace, "prompt", issue, on_message: on_message)

      assert_receive {:agent_message,
                      %{
                        event: :turn_ended_with_error,
                        reason: {:provider_error, "gemini", %{"message" => "quota exceeded"}},
                        runtime_event: %{
                          event: :turn_failed,
                          raw: {:provider_error, "gemini", %{"message" => "quota exceeded"}},
                          payload: %{
                            "type" => "provider_error",
                            "reason" => "provider_error",
                            "provider" => "gemini",
                            "error" => %{"message" => "quota exceeded"},
                            "output_format" => "json"
                          }
                        }
                      }}
    after
      File.rm_rf(test_root)
    end
  end

  test "preserves Gemini exit status and structured failure context" do
    test_root =
      Path.join(
        System.tmp_dir!(),
        "orbit-elixir-cli-provider-gemini-exit-#{System.unique_integer([:positive])}"
      )

    workspace = Path.join(test_root, "DEV-CLI-GEMINI-EXIT")

    try do
      File.mkdir_p!(workspace)

      provider = %{
        "name" => "gemini",
        "display_name" => "Gemini",
        "harness" => "cli",
        "command" => "printf '%s\\n' '{\"error\":{\"message\":\"bad auth\"}}' && exit 7",
        "model" => "auto",
        "output_format" => "json",
        "timeout_ms" => 1_000
      }

      issue = %Issue{
        id: "issue-cli-gemini-exit",
        identifier: "DEV-CLI-GEMINI-EXIT",
        title: "CLI Gemini exit",
        description: "Preserve exit status.",
        labels: []
      }

      parent = self()
      on_message = fn message -> send(parent, {:agent_message, message}) end

      assert {:error, {:provider_exit, "gemini", 7}} =
               AgentCLI.run_turn(provider, workspace, "prompt", issue, on_message: on_message)

      assert_receive {:agent_message,
                      %{
                        event: :turn_ended_with_error,
                        reason: {:provider_exit, "gemini", 7},
                        runtime_event: %{
                          event: :turn_failed,
                          payload: %{
                            "type" => "exit",
                            "reason" => "provider_exit",
                            "provider" => "gemini",
                            "exit_status" => 7,
                            "provider_error" => %{"message" => "bad auth"},
                            "output_format" => "json"
                          }
                        }
                      }}
    after
      File.rm_rf(test_root)
    end
  end

  test "fails Gemini JSON success with parse details when output is malformed" do
    test_root =
      Path.join(
        System.tmp_dir!(),
        "orbit-elixir-cli-provider-gemini-parse-#{System.unique_integer([:positive])}"
      )

    workspace = Path.join(test_root, "DEV-CLI-GEMINI-PARSE")

    try do
      File.mkdir_p!(workspace)

      provider = %{
        "name" => "gemini",
        "display_name" => "Gemini",
        "harness" => "cli",
        "command" => "printf '%s\\n' 'not json'",
        "model" => "auto",
        "output_format" => "json",
        "timeout_ms" => 1_000
      }

      issue = %Issue{
        id: "issue-cli-gemini-parse",
        identifier: "DEV-CLI-GEMINI-PARSE",
        title: "CLI Gemini parse",
        description: "Fail malformed Gemini JSON output.",
        labels: []
      }

      parent = self()
      on_message = fn message -> send(parent, {:agent_message, message}) end

      assert {:error, {:provider_output_parse_failed, "gemini", {:json_decode_failed, _message}}} =
               AgentCLI.run_turn(provider, workspace, "prompt", issue, on_message: on_message)

      assert_receive {:agent_message,
                      %{
                        event: :turn_ended_with_error,
                        reason: {:provider_output_parse_failed, "gemini", {:json_decode_failed, _message}},
                        runtime_event: %{
                          event: :turn_failed,
                          payload: %{
                            "type" => "output_parse",
                            "reason" => "provider_output_parse_failed",
                            "provider" => "gemini",
                            "output_format" => "json",
                            "output_tail" => "not json",
                            "parse_error" => parse_error
                          }
                        }
                      }}

      assert parse_error =~ "json_decode_failed"
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

  test "returns Gemini JSON timeout failures with deadline context" do
    test_root =
      Path.join(
        System.tmp_dir!(),
        "orbit-elixir-cli-provider-gemini-timeout-#{System.unique_integer([:positive])}"
      )

    workspace = Path.join(test_root, "DEV-CLI-GEMINI-TIMEOUT")

    try do
      File.mkdir_p!(workspace)

      provider = %{
        "name" => "gemini",
        "display_name" => "Gemini",
        "harness" => "cli",
        "command" => "printf '{\"response\":\"partial' && sleep 0.2",
        "model" => "auto",
        "output_format" => "json",
        "timeout_ms" => 20
      }

      issue = %Issue{
        id: "issue-cli-gemini-timeout",
        identifier: "DEV-CLI-GEMINI-TIMEOUT",
        title: "CLI Gemini timeout",
        description: "Timeout Gemini JSON mode.",
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
                        runtime_event: %{
                          event: :turn_failed,
                          payload: %{
                            "type" => "timeout",
                            "reason" => "turn_timeout",
                            "timeout_ms" => 20,
                            "output_format" => "json"
                          }
                        }
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
