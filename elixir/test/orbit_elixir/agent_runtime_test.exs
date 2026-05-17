defmodule OrbitElixir.AgentRuntimeTest do
  use OrbitElixir.TestSupport

  alias OrbitElixir.AgentRuntime

  test "declares the provider-neutral runtime operations" do
    assert AgentRuntime.operations() == [
             :start_session,
             :send_turn,
             :stream_events,
             :stop_session,
             :read_diff,
             :summarize_result
           ]
  end

  test "validates every normalized event type shape" do
    assert AgentRuntime.event_types() == [
             :session_started,
             :turn_started,
             :output_delta,
             :tool_event,
             :diff_changed,
             :approval_needed,
             :turn_completed,
             :turn_failed
           ]

    for event_type <- AgentRuntime.event_types() do
      assert {:ok, event} =
               AgentRuntime.new_event(event_type, %{
                 session_id: "session-1",
                 turn_id: "turn-1",
                 provider: "codex",
                 harness: "codex_app_server",
                 payload: %{}
               })

      assert event.event == event_type
      assert :ok = AgentRuntime.validate_event(event)
    end

    assert {:error, {:unknown_event_type, :unknown}} =
             AgentRuntime.new_event(:unknown, %{
               session_id: "session-1",
               provider: "codex",
               harness: "codex_app_server"
             })

    assert {:error, {:missing_or_invalid, :session_id}} =
             AgentRuntime.validate_event(%{
               event: :session_started,
               timestamp: DateTime.utc_now(),
               provider: "codex",
               harness: "codex_app_server",
               payload: %{}
             })
  end

  test "normalizes current approval and tool messages" do
    assert {:ok, %{event: :approval_needed}} =
             AgentRuntime.normalize_message(%{
               event: :approval_required,
               timestamp: DateTime.utc_now(),
               session_id: "thread-turn",
               agent_provider: "codex",
               agent_harness: "codex_app_server",
               payload: %{"method" => "item/commandExecution/requestApproval"}
             })

    assert {:ok, %{event: :tool_event}} =
             AgentRuntime.normalize_message(%{
               event: :tool_call_completed,
               timestamp: DateTime.utc_now(),
               session_id: "thread-turn",
               agent_provider: "codex",
               agent_harness: "codex_app_server",
               payload: %{"method" => "item/tool/call"}
             })
  end

  test "CLI harness messages carry normalized runtime events" do
    test_root =
      Path.join(
        System.tmp_dir!(),
        "orbit-elixir-runtime-cli-#{System.unique_integer([:positive])}"
      )

    workspace = Path.join(test_root, "DEV-RUNTIME-CLI")

    try do
      File.mkdir_p!(workspace)

      provider = %{
        "name" => "gemini",
        "display_name" => "Gemini",
        "harness" => "cli",
        "command" => "printf 'done\\n'",
        "model" => "auto",
        "timeout_ms" => 1_000
      }

      issue = %Issue{
        id: "issue-runtime-cli",
        identifier: "DEV-RUNTIME-CLI",
        title: "Runtime CLI",
        description: "Normalize CLI events.",
        labels: []
      }

      parent = self()
      on_message = fn message -> send(parent, {:runtime_message, message}) end

      assert {:ok, _result} =
               AgentCLI.run_turn(provider, workspace, "prompt", issue, on_message: on_message)

      assert_receive {:runtime_message,
                      %{
                        event: :session_started,
                        runtime_event: %{
                          event: :session_started,
                          provider: "gemini",
                          harness: "cli"
                        }
                      }}

      assert_receive {:runtime_message,
                      %{
                        event: :notification,
                        runtime_event: %{
                          event: :output_delta,
                          provider: "gemini",
                          harness: "cli",
                          payload: %{"line" => "done"}
                        }
                      }}

      assert_receive {:runtime_message,
                      %{
                        event: :turn_completed,
                        runtime_event: %{
                          event: :turn_completed,
                          provider: "gemini",
                          harness: "cli"
                        }
                      }}
    after
      File.rm_rf(test_root)
    end
  end

  test "Codex app-server messages carry normalized runtime events" do
    test_root =
      Path.join(
        System.tmp_dir!(),
        "orbit-elixir-runtime-codex-#{System.unique_integer([:positive])}"
      )

    try do
      workspace_root = Path.join(test_root, "workspaces")
      workspace = Path.join(workspace_root, "DEV-RUNTIME-CODEX")
      codex_binary = Path.join(test_root, "fake-codex")
      File.mkdir_p!(workspace)

      File.write!(codex_binary, """
      #!/bin/sh
      count=0
      while IFS= read -r _line; do
        count=$((count + 1))

        case "$count" in
          1)
            printf '%s\\n' '{"id":1,"result":{}}'
            ;;
          2)
            printf '%s\\n' '{"id":2,"result":{"thread":{"id":"thread-runtime"}}}'
            ;;
          3)
            printf '%s\\n' '{"id":3,"result":{"turn":{"id":"turn-runtime"}}}'
            ;;
          4)
            printf '%s\\n' '{"method":"item/updated","params":{"message":"hello"}}'
            printf '%s\\n' '{"method":"turn/completed","usage":{"input_tokens":1,"output_tokens":2}}'
            exit 0
            ;;
          *)
            exit 0
            ;;
        esac
      done
      """)

      File.chmod!(codex_binary, 0o755)

      write_workflow_file!(Workflow.workflow_file_path(),
        workspace_root: workspace_root,
        codex_command: "#{codex_binary} app-server"
      )

      issue = %Issue{
        id: "issue-runtime-codex",
        identifier: "DEV-RUNTIME-CODEX",
        title: "Runtime Codex",
        description: "Normalize Codex events.",
        labels: []
      }

      parent = self()
      on_message = fn message -> send(parent, {:runtime_message, message}) end

      assert {:ok, %{session_id: "thread-runtime-turn-runtime"}} =
               AppServer.run(workspace, "prompt", issue, on_message: on_message)

      assert_received {:runtime_message,
                       %{
                         event: :session_started,
                         runtime_event: %{
                           event: :session_started,
                           session_id: "thread-runtime-turn-runtime",
                           turn_id: "turn-runtime",
                           provider: "codex",
                           harness: "codex_app_server"
                         }
                       }}

      assert_received {:runtime_message,
                       %{
                         event: :notification,
                         runtime_event: %{
                           event: :output_delta,
                           session_id: "thread-runtime-turn-runtime",
                           turn_id: "turn-runtime",
                           provider: "codex",
                           harness: "codex_app_server"
                         }
                       }}

      assert_received {:runtime_message,
                       %{
                         event: :turn_completed,
                         runtime_event: %{
                           event: :turn_completed,
                           session_id: "thread-runtime-turn-runtime",
                           turn_id: "turn-runtime",
                           provider: "codex",
                           harness: "codex_app_server"
                         }
                       }}
    after
      File.rm_rf(test_root)
    end
  end
end
