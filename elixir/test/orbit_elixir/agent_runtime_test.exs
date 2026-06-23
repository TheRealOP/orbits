defmodule OrbitElixir.AgentRuntimeTest do
  use OrbitElixir.TestSupport

  alias OrbitElixir.AgentRuntime
  alias OrbitElixir.AgentRuntime.CodexAppServer, as: CodexRuntime

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

  test "handles runtime event validation and normalization edge cases" do
    assert {:error, :event_must_be_map} = AgentRuntime.validate_event(:not_a_map)
    assert {:error, :message_must_be_map} = AgentRuntime.normalize_message(:not_a_map)
    assert AgentRuntime.attach_runtime_event(:not_a_map) == :not_a_map

    assert {:ok, %{event: :turn_completed, timestamp: "2026-05-17T00:00:00Z"}} =
             AgentRuntime.new_event("turn_completed", %{
               "timestamp" => "2026-05-17T00:00:00Z",
               "session_id" => "session-1",
               "provider" => "codex",
               "harness" => "codex_app_server",
               "payload" => %{"ok" => true}
             })

    assert {:error, {:unknown_event_type, "unknown"}} =
             AgentRuntime.new_event("unknown", %{
               session_id: "session-1",
               provider: "codex",
               harness: "codex_app_server",
               payload: %{}
             })

    assert {:error, {:missing_or_invalid, :payload}} =
             AgentRuntime.new_event(:turn_completed, %{
               session_id: "session-1",
               provider: "codex",
               harness: "codex_app_server",
               payload: nil
             })

    assert {:error, {:missing_or_invalid, :timestamp}} =
             AgentRuntime.validate_event(%{
               event: :turn_completed,
               timestamp: nil,
               session_id: "session-1",
               provider: "codex",
               harness: "codex_app_server",
               payload: %{}
             })

    assert :ok =
             AgentRuntime.validate_event(%{
               event: :turn_completed,
               timestamp: "2026-05-17T00:00:00Z",
               session_id: "session-1",
               provider: "codex",
               harness: "codex_app_server",
               payload: %{}
             })

    assert {:ok, %{event: :diff_changed}} =
             AgentRuntime.normalize_message(%{
               event: :notification,
               session_id: "session-1",
               provider: "codex",
               harness: "codex_app_server",
               payload: %{"method" => "workspace/diffChanged"}
             })

    assert {:ok, %{session_id: "123"}} =
             AgentRuntime.normalize_message(%{
               event: :turn_completed,
               session_id: 123,
               provider: "codex",
               harness: "codex_app_server",
               payload: %{}
             })

    assert {:ok,
            %{
              timestamp: "2026-05-17T00:00:00Z",
              session_id: "session_atom",
              provider: "codex",
              harness: "codex_app_server"
            }} =
             AgentRuntime.normalize_message(%{
               event: :turn_completed,
               timestamp: "2026-05-17T00:00:00Z",
               session_id: :session_atom,
               provider: :codex,
               harness: :codex_app_server,
               payload: %{}
             })

    assert {:ok, event_without_turn_id} =
             AgentRuntime.normalize_message(%{
               event: :turn_completed,
               session_id: "session-1",
               provider: "codex",
               harness: "codex_app_server",
               turn_id: [],
               payload: %{}
             })

    assert %DateTime{} = event_without_turn_id.timestamp
    refute Map.has_key?(event_without_turn_id, :turn_id)

    assert {:error, {:missing_or_invalid, :session_id}} =
             AgentRuntime.normalize_message(%{
               event: :turn_completed,
               provider: "codex",
               harness: "codex_app_server",
               payload: %{}
             })

    assert {:error, {:missing_or_invalid, :provider}} =
             AgentRuntime.normalize_message(%{
               event: :turn_completed,
               session_id: "session-1",
               harness: "codex_app_server",
               payload: %{}
             })

    assert {:error, {:missing_or_invalid, :harness}} =
             AgentRuntime.normalize_message(%{
               event: :turn_completed,
               session_id: "session-1",
               provider: "codex",
               payload: %{}
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

  test "Codex runtime adapter preserves app-server messages and normalized details" do
    test_root =
      Path.join(
        System.tmp_dir!(),
        "orbit-elixir-runtime-adapter-codex-#{System.unique_integer([:positive])}"
      )

    try do
      workspace_root = Path.join(test_root, "workspaces")
      workspace = Path.join(workspace_root, "DEV-RUNTIME-ADAPTER")
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
            printf '%s\\n' '{"id":2,"result":{"thread":{"id":"thread-adapter"}}}'
            ;;
          3)
            printf '%s\\n' '{"id":3,"result":{"turn":{"id":"turn-adapter"}}}'
            ;;
          4)
            printf '%s\\n' '{"method":"item/updated","params":{"message":"adapter output","kind":"codex-specific"}}'
            printf '%s\\n' '{"method":"turn/completed","usage":{"input_tokens":3,"output_tokens":5},"details":{"provider":"codex"}}'
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

      provider = %{
        "name" => "codex",
        "display_name" => "Codex",
        "harness" => "codex_app_server",
        "command" => "#{codex_binary} app-server",
        "model" => "gpt-test"
      }

      issue = %Issue{
        id: "issue-runtime-adapter-codex",
        identifier: "DEV-RUNTIME-ADAPTER",
        title: "Runtime Codex Adapter",
        description: "Normalize Codex events through adapter.",
        labels: []
      }

      parent = self()
      on_message = fn message -> send(parent, {:runtime_message, message}) end

      assert {:ok, session} =
               CodexRuntime.start_session(%{
                 workspace: workspace,
                 provider: "codex",
                 harness: "codex_app_server",
                 model: "gpt-test",
                 config: %{provider: provider}
               })

      try do
        assert {:ok, turn} =
                 CodexRuntime.send_turn(session, %{
                   prompt: "prompt",
                   issue: issue
                 })

        assert {:ok, %{status: :completed, provider_result: %{session_id: "thread-adapter-turn-adapter"}}} =
                 CodexRuntime.stream_events(session, turn, on_message)
      after
        CodexRuntime.stop_session(session)
      end

      assert_received {:runtime_message,
                       %{
                         event: :notification,
                         payload: %{
                           "method" => "item/updated",
                           "params" => %{
                             "kind" => "codex-specific",
                             "message" => "adapter output"
                           }
                         },
                         raw: raw_notification,
                         runtime_event: %{
                           event: :output_delta,
                           source_event: :notification,
                           provider: "codex",
                           harness: "codex_app_server",
                           model: "gpt-test",
                           raw: runtime_raw,
                           payload: %{
                             "method" => "item/updated",
                             "params" => %{"kind" => "codex-specific"}
                           }
                         }
                       }}

      assert runtime_raw == raw_notification

      assert_received {:runtime_message,
                       %{
                         event: :turn_completed,
                         usage: %{"input_tokens" => 3, "output_tokens" => 5},
                         runtime_event: %{
                           event: :turn_completed,
                           session_id: "thread-adapter-turn-adapter",
                           turn_id: "turn-adapter",
                           provider: "codex",
                           harness: "codex_app_server",
                           payload: %{
                             "method" => "turn/completed",
                             "usage" => %{"input_tokens" => 3, "output_tokens" => 5},
                             "details" => %{"provider" => "codex"}
                           }
                         }
                       }}
    after
      File.rm_rf(test_root)
    end
  end
end
