defmodule OrbitElixir.AgentClaudeSDKTest do
  use OrbitElixir.TestSupport

  alias OrbitElixir.AgentRuntime.ClaudeAgentSDK, as: ClaudeRuntime

  test "Claude SDK adapter streams normalized events and resumes the SDK session" do
    test_root =
      Path.join(
        System.tmp_dir!(),
        "orbit-elixir-claude-sdk-#{System.unique_integer([:positive])}"
      )

    try do
      workspace_root = Path.join(test_root, "workspaces")
      workspace = Path.join(workspace_root, "DEV-CLAUDE-SDK")
      bridge = Path.join(test_root, "fake-claude-sdk")
      trace_file = Path.join(test_root, "sdk-requests.jsonl")

      File.mkdir_p!(workspace)

      write_workflow_file!(Workflow.workflow_file_path(), workspace_root: workspace_root)

      write_executable!(bridge, """
      #!/bin/sh
      trace_file=#{shell_escape(trace_file)}
      IFS= read -r request
      printf '%s\\n' "$request" >> "$trace_file"
      printf '%s\\n' '{"orbit_event":"sdk_message","message":{"type":"system","subtype":"init","session_id":"sdk-session-1","cwd":"/workspace","model":"sonnet","permissionMode":"acceptEdits","tools":["Read","Write"],"apiKeySource":"env","claude_code_version":"test"}}'
      printf '%s\\n' '{"orbit_event":"sdk_message","message":{"type":"assistant","session_id":"sdk-session-1","message":{"content":[{"type":"text","text":"edited file"},{"type":"tool_use","id":"toolu_1","name":"Write","input":{"file_path":"README.md"}}]}}}'
      printf '%s\\n' '{"orbit_event":"sdk_message","message":{"type":"user","session_id":"sdk-session-1","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_1","content":"ok","is_error":false}]}}}'
      printf '%s\\n' '{"orbit_event":"sdk_message","message":{"type":"result","subtype":"success","session_id":"sdk-session-1","is_error":false,"num_turns":1,"result":"done","usage":{"input_tokens":2,"output_tokens":3,"total_tokens":5}}}'
      """)

      issue = issue("DEV-CLAUDE-SDK")
      provider = provider(bridge)

      assert {:ok, session} =
               ClaudeRuntime.start_session(%{
                 workspace: workspace,
                 issue: issue,
                 config: %{provider: provider}
               })

      try do
        parent = self()
        emit = fn event -> send(parent, {:runtime_event, event}) end

        assert {:ok, first_turn} =
                 ClaudeRuntime.send_turn(session, %{
                   prompt: "first prompt",
                   issue: issue
                 })

        assert {:ok, %{status: :completed, provider_result: %{"session_id" => "sdk-session-1"}}} =
                 ClaudeRuntime.stream_events(session, first_turn, emit)

        assert {:ok, second_turn} =
                 ClaudeRuntime.send_turn(session, %{
                   prompt: "second prompt",
                   issue: issue
                 })

        assert second_turn.provider_turn.resume == "sdk-session-1"

        assert {:ok, %{status: :completed, usage: %{"total_tokens" => 5}}} =
                 ClaudeRuntime.stream_events(session, second_turn, emit)

        assert_received {:runtime_event,
                         %{
                           event: :session_started,
                           session_id: "sdk-session-1",
                           provider: "claude",
                           harness: "claude_agent_sdk",
                           payload: %{"tools" => ["Read", "Write"]}
                         }}

        assert_received {:runtime_event,
                         %{
                           event: :output_delta,
                           session_id: "sdk-session-1",
                           payload: %{"stream" => "assistant", "text" => "edited file"}
                         }}

        assert_received {:runtime_event,
                         %{
                           event: :tool_event,
                           session_id: "sdk-session-1",
                           payload: %{"type" => "tool_use", "tool_name" => "Write", "tool_use_id" => "toolu_1"}
                         }}

        assert_received {:runtime_event,
                         %{
                           event: :turn_completed,
                           session_id: "sdk-session-1",
                           payload: %{"usage" => %{"input_tokens" => 2, "output_tokens" => 3, "total_tokens" => 5}}
                         }}

        [first_request, second_request] =
          trace_file
          |> File.read!()
          |> String.split("\n", trim: true)
          |> Enum.map(&Jason.decode!/1)

        assert first_request["cwd"] == workspace
        assert first_request["prompt"] == "first prompt"
        refute Map.has_key?(first_request, "resume")
        assert first_request["permissionMode"] == "acceptEdits"
        assert first_request["allowedTools"] == ["Read", "Write", "Edit", "MultiEdit", "Bash", "Glob", "Grep", "LS"]

        assert second_request["prompt"] == "second prompt"
        assert second_request["resume"] == "sdk-session-1"
      after
        ClaudeRuntime.stop_session(session)
      end
    after
      File.rm_rf(test_root)
    end
  end

  test "Claude SDK adapter reports SDK result failures as normalized turn failures" do
    test_root =
      Path.join(
        System.tmp_dir!(),
        "orbit-elixir-claude-sdk-failure-#{System.unique_integer([:positive])}"
      )

    try do
      workspace_root = Path.join(test_root, "workspaces")
      workspace = Path.join(workspace_root, "DEV-CLAUDE-SDK-FAIL")
      bridge = Path.join(test_root, "fake-claude-sdk-fail")

      File.mkdir_p!(workspace)
      write_workflow_file!(Workflow.workflow_file_path(), workspace_root: workspace_root)

      write_executable!(bridge, """
      #!/bin/sh
      IFS= read -r _request
      printf '%s\\n' '{"orbit_event":"sdk_message","message":{"type":"system","subtype":"init","session_id":"sdk-session-fail","model":"sonnet","permissionMode":"acceptEdits","tools":["Read"]}}'
      printf '%s\\n' '{"orbit_event":"sdk_message","message":{"type":"result","subtype":"error_during_execution","session_id":"sdk-session-fail","is_error":true,"num_turns":1,"stop_reason":"error","total_cost_usd":0,"usage":{"input_tokens":1,"output_tokens":0},"errors":["boom"]}}'
      """)

      issue = issue("DEV-CLAUDE-SDK-FAIL")
      provider = provider(bridge)

      assert {:ok, session} =
               ClaudeRuntime.start_session(%{
                 workspace: workspace,
                 issue: issue,
                 config: %{provider: provider}
               })

      try do
        assert {:ok, turn} = ClaudeRuntime.send_turn(session, %{prompt: "prompt", issue: issue})

        parent = self()

        assert {:error, {:claude_agent_sdk_result_error, "error_during_execution"}} =
                 ClaudeRuntime.stream_events(session, turn, fn event -> send(parent, {:runtime_event, event}) end)

        assert_received {:runtime_event,
                         %{
                           event: :turn_failed,
                           session_id: "sdk-session-fail",
                           payload: %{
                             "subtype" => "error_during_execution",
                             "is_error" => true,
                             "stop_reason" => "error",
                             "usage" => %{"input_tokens" => 1, "output_tokens" => 0}
                           }
                         }}
      after
        ClaudeRuntime.stop_session(session)
      end
    after
      File.rm_rf(test_root)
    end
  end

  test "Claude SDK adapter aborts an active turn and reports cancellation" do
    test_root =
      Path.join(
        System.tmp_dir!(),
        "orbit-elixir-claude-sdk-stop-#{System.unique_integer([:positive])}"
      )

    try do
      workspace_root = Path.join(test_root, "workspaces")
      workspace = Path.join(workspace_root, "DEV-CLAUDE-SDK-STOP")
      bridge = Path.join(test_root, "fake-claude-sdk-stop")

      File.mkdir_p!(workspace)
      write_workflow_file!(Workflow.workflow_file_path(), workspace_root: workspace_root)

      write_executable!(bridge, """
      #!/bin/sh
      IFS= read -r _request
      printf '%s\\n' '{"orbit_event":"sdk_message","message":{"type":"system","subtype":"init","session_id":"sdk-session-stop","model":"sonnet","permissionMode":"acceptEdits","tools":["Read"]}}'
      if IFS= read -r _control; then
        printf '%s\\n' '{"orbit_event":"sdk_stopped","error":{"code":"sdk_aborted","name":"AbortError","message":"aborted"}}'
      fi
      """)

      issue = issue("DEV-CLAUDE-SDK-STOP")
      provider = provider(bridge)

      assert {:ok, session} =
               ClaudeRuntime.start_session(%{
                 workspace: workspace,
                 issue: issue,
                 config: %{provider: provider}
               })

      try do
        assert {:ok, turn} = ClaudeRuntime.send_turn(session, %{prompt: "prompt", issue: issue})

        parent = self()

        assert :ok = ClaudeRuntime.stop_session(session)

        assert {:error, {:turn_cancelled, %{"code" => "sdk_aborted", "message" => "aborted", "name" => "AbortError"}}} =
                 ClaudeRuntime.stream_events(session, turn, fn event -> send(parent, {:runtime_event, event}) end)

        assert_received {:runtime_event, %{event: :turn_started}}

        assert_received {:runtime_event,
                         %{
                           event: :turn_failed,
                           session_id: "sdk-session-stop",
                           payload: %{"reason" => "turn_cancelled", "type" => "cancelled"}
                         }}
      after
        ClaudeRuntime.stop_session(session)
      end
    after
      File.rm_rf(test_root)
    end
  end

  test "agent runner falls back to Claude CLI when the SDK bridge fails" do
    test_root =
      Path.join(
        System.tmp_dir!(),
        "orbit-elixir-claude-sdk-fallback-#{System.unique_integer([:positive])}"
      )

    try do
      workspace_root = Path.join(test_root, "workspaces")
      bridge = Path.join(test_root, "fake-claude-sdk-error")
      cli_trace = Path.join(test_root, "cli-fallback.trace")

      File.mkdir_p!(workspace_root)

      write_executable!(bridge, """
      #!/bin/sh
      IFS= read -r _request
      printf '%s\\n' '{"orbit_event":"sdk_error","error":{"code":"sdk_import_failed","name":"Error","message":"missing sdk"}}'
      exit 1
      """)

      write_workflow_file!(Workflow.workflow_file_path(),
        workspace_root: workspace_root,
        providers: %{
          claude: %{
            harness: "claude_agent_sdk",
            sdk_command: bridge,
            command: "printf 'cli fallback\\n' > #{shell_escape(cli_trace)} && printf 'done\\n'",
            model: "sonnet",
            timeout_ms: 1_000
          }
        }
      )

      issue = %Issue{
        id: "issue-claude-sdk-fallback",
        identifier: "DEV-CLAUDE-SDK-FALLBACK",
        title: "Write architecture docs",
        description: "Use Claude for documentation.",
        state: "In Progress",
        labels: []
      }

      assert :ok =
               AgentRunner.run(
                 issue,
                 self(),
                 issue_state_fetcher: fn [_issue_id] -> {:ok, [%{issue | state: "Done"}]} end
               )

      assert File.read!(cli_trace) == "cli fallback\n"

      assert_received {:codex_worker_update, "issue-claude-sdk-fallback", %{event: :turn_failed, harness: "claude_agent_sdk", provider: "claude"}}

      assert_received {:codex_worker_update, "issue-claude-sdk-fallback", %{event: :turn_completed, agent_harness: "cli", agent_provider: "claude"}}
    after
      File.rm_rf(test_root)
    end
  end

  defp provider(bridge) do
    %{
      "name" => "claude",
      "display_name" => "Claude",
      "harness" => "claude_agent_sdk",
      "command" => "claude -p \"$ORBIT_AGENT_PROMPT\"",
      "sdk_command" => bridge,
      "model" => "sonnet",
      "timeout_ms" => 1_000
    }
  end

  defp issue(identifier) do
    %Issue{
      id: "issue-#{String.downcase(identifier)}",
      identifier: identifier,
      title: "Claude SDK",
      description: "Run through Claude Agent SDK.",
      state: "In Progress",
      labels: []
    }
  end

  defp write_executable!(path, content) do
    File.write!(path, content)
    File.chmod!(path, 0o755)
  end

  defp shell_escape(value) when is_binary(value) do
    "'" <> String.replace(value, "'", "'\"'\"'") <> "'"
  end
end
