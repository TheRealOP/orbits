defmodule OrbitElixir.AgentRuntime.ClaudeAgentSDK do
  @moduledoc """
  Runtime adapter for Claude Agent SDK sessions.

  The adapter keeps Orbit's provider-neutral runtime contract in Elixir while a
  small Node.js helper owns the TypeScript SDK `query()` stream for each turn.
  Claude session continuity is preserved by storing the SDK `session_id` and
  passing it back as `resume` on subsequent turns.
  """

  @behaviour OrbitElixir.AgentRuntime

  require Logger

  alias OrbitElixir.{AgentRuntime, Config, PathSafety}

  @port_line_bytes 1_048_576
  @default_tools ["Read", "Write", "Edit", "MultiEdit", "Bash", "Glob", "Grep", "LS"]

  @spec run_turn(map(), Path.t(), String.t(), map(), keyword()) :: {:ok, map()} | {:error, term()}
  def run_turn(provider, workspace, prompt, issue, opts \\ []) do
    worker_host = Keyword.get(opts, :worker_host)
    on_message = Keyword.get(opts, :on_message, &default_on_message/1)
    request = %{workspace: workspace, issue: issue, config: %{provider: provider, worker_host: worker_host}}

    with {:ok, session} <- start_session(request),
         {:ok, turn} <- send_turn(session, %{prompt: prompt, issue: issue}) do
      try do
        case stream_events(session, turn, &emit_legacy_message(on_message, &1)) do
          {:ok, summary} ->
            {:ok,
             %{
               result: Map.get(summary, :provider_result, :turn_completed),
               session_id: session_id_from_summary(summary, session.session_id),
               provider: session.provider
             }}

          {:error, reason} ->
            {:error, reason}
        end
      after
        stop_session(session)
      end
    end
  end

  @impl true
  def start_session(request) when is_map(request) do
    with {:ok, workspace} <- request_workspace(request),
         {:ok, provider} <- request_provider(request),
         {:ok, worker_host} <- request_worker_host(request),
         {:ok, workspace} <- validate_workspace_cwd(workspace, worker_host),
         {:ok, state_pid} <- start_session_state(provider) do
      session_id = Map.get(provider, "session_id") || synthetic_session_id(provider)

      {:ok,
       %{
         session_id: session_id,
         provider: provider["name"],
         harness: provider["harness"],
         model: provider["model"],
         workspace: workspace,
         provider_session: %{
           provider: provider,
           issue: request_value(request, :issue),
           worker_host: worker_host,
           state_pid: state_pid,
           sdk_command: sdk_command(provider)
         }
       }}
    end
  end

  @impl true
  def send_turn(session, request) when is_map(session) and is_map(request) do
    provider_session = Map.get(session, :provider_session, %{})
    provider = Map.fetch!(provider_session, :provider)
    state_pid = Map.fetch!(provider_session, :state_pid)
    issue = request_value(request, :issue) || Map.get(provider_session, :issue) || %{}
    prompt = request_value(request, :prompt) || ""
    turn_id = "claude-turn-#{System.unique_integer([:positive, :monotonic])}"
    sdk_session_id = current_sdk_session_id(state_pid)

    with {:ok, port} <-
           start_port(
             Map.fetch!(provider_session, :sdk_command),
             session.workspace,
             provider,
             issue,
             Map.get(provider_session, :worker_host)
           ),
         :ok <-
           send_sdk_request(port, provider, session.workspace, prompt, issue, sdk_session_id) do
      mark_turn_active(state_pid, port, turn_id)

      {:ok,
       %{
         turn_id: turn_id,
         session_id: session.session_id,
         provider_turn: %{
           port: port,
           command: Map.fetch!(provider_session, :sdk_command),
           issue: issue,
           prompt: prompt,
           resume: sdk_session_id,
           worker_host: Map.get(provider_session, :worker_host)
         }
       }}
    end
  end

  @impl true
  def stream_events(session, turn, emit_event) when is_map(session) and is_map(turn) and is_function(emit_event, 1) do
    provider_turn = Map.get(turn, :provider_turn, %{})
    port = Map.fetch!(provider_turn, :port)

    emit_runtime_event(emit_event, :turn_started, session, turn, %{
      "command" => Map.get(provider_turn, :command),
      "resume" => Map.get(provider_turn, :resume),
      "worker_host" => Map.get(provider_turn, :worker_host)
    })

    Logger.info(
      "Claude SDK turn started for #{issue_context(Map.get(provider_turn, :issue))} " <>
        "session_id=#{session.session_id} turn_id=#{turn.turn_id}"
    )

    case await_completion(port, session, turn, emit_event, %{}) do
      {:ok, summary} ->
        Logger.info(
          "Claude SDK turn completed for #{issue_context(Map.get(provider_turn, :issue))} " <>
            "session_id=#{session_id_from_summary(summary, session.session_id)} turn_id=#{turn.turn_id}"
        )

        {:ok, summary}

      {:error, reason} ->
        Logger.warning(
          "Claude SDK turn failed for #{issue_context(Map.get(provider_turn, :issue))} " <>
            "session_id=#{session.session_id} turn_id=#{turn.turn_id}: #{inspect(reason)}"
        )

        {:error, reason}
    end
  after
    clear_turn_active(session)
  end

  @impl true
  def stop_session(session) when is_map(session) do
    case session_state_pid(session) do
      nil ->
        :ok

      state_pid ->
        case stop_active_turn(state_pid) do
          :active ->
            :ok

          :idle ->
            stop_state(state_pid)
        end
    end
  end

  @impl true
  def read_diff(%{workspace: workspace}) when is_binary(workspace) do
    case System.cmd("git", ["-C", workspace, "diff", "--"], stderr_to_stdout: true) do
      {diff, 0} -> {:ok, diff}
      {output, status} -> {:error, {:git_diff_failed, status, output}}
    end
  rescue
    error -> {:error, {:git_diff_failed, Exception.message(error)}}
  end

  def read_diff(_session), do: {:error, {:invalid_runtime_session, :workspace}}

  @impl true
  def summarize_result(%{"is_error" => true} = result) do
    %{
      status: :failed,
      message: result_message(result),
      usage: usage_from_result(result),
      provider_result: result
    }
  end

  def summarize_result(%{"subtype" => "error"} = result) do
    %{
      status: :failed,
      message: result_message(result),
      usage: usage_from_result(result),
      provider_result: result
    }
  end

  def summarize_result(%{} = result) do
    %{
      status: :completed,
      message: result_message(result),
      usage: usage_from_result(result),
      provider_result: result
    }
  end

  def summarize_result(:turn_cancelled), do: %{status: :cancelled, message: "turn cancelled", provider_result: :turn_cancelled}
  def summarize_result(reason), do: %{status: :failed, message: inspect(reason), provider_result: reason}

  defp start_port(_command, _workspace, _provider, _issue, worker_host) when is_binary(worker_host) do
    {:error, :remote_claude_agent_sdk_unsupported}
  end

  defp start_port(command, workspace, provider, issue, nil) do
    executable = System.find_executable("bash")

    cond do
      is_nil(executable) ->
        {:error, :bash_not_found}

      not is_binary(command) or String.trim(command) == "" ->
        {:error, :missing_claude_agent_sdk_command}

      true ->
        port =
          Port.open(
            {:spawn_executable, String.to_charlist(executable)},
            [
              :binary,
              :exit_status,
              :stderr_to_stdout,
              args: [~c"-lc", String.to_charlist(command)],
              cd: String.to_charlist(workspace),
              env: port_env(provider, issue),
              line: @port_line_bytes
            ]
          )

        {:ok, port}
    end
  end

  defp send_sdk_request(port, provider, workspace, prompt, _issue, resume) when is_port(port) do
    request =
      %{
        prompt: prompt,
        cwd: workspace,
        model: provider["model"] || "sonnet",
        resume: resume,
        permissionMode: provider["permission_mode"] || "acceptEdits",
        allowedTools: provider["allowed_tools"] || @default_tools,
        disallowedTools: provider["disallowed_tools"],
        settingSources: Map.get(provider, "setting_sources", []),
        includePartialMessages: Map.get(provider, "include_partial_messages", true),
        maxTurns: provider["sdk_max_turns"] || provider["max_turns"],
        pathToClaudeCodeExecutable: provider["path_to_claude_code_executable"],
        env: provider["env"] || %{}
      }
      |> compact_map()

    Port.command(port, Jason.encode!(request) <> "\n")
    :ok
  rescue
    error -> {:error, {:claude_agent_sdk_request_failed, Exception.message(error)}}
  end

  defp await_completion(port, session, turn, emit_event, acc) do
    timeout_ms = provider_timeout_ms(session)

    receive do
      {^port, {:data, {:eol, chunk}}} ->
        chunk
        |> to_string()
        |> handle_bridge_line(session, turn, emit_event, acc)
        |> case do
          {:cont, acc} -> await_completion(port, session, turn, emit_event, acc)
          {:halt, result} -> result
        end

      {^port, {:data, {:noeol, chunk}}} ->
        chunk
        |> to_string()
        |> handle_bridge_line(session, turn, emit_event, acc)
        |> case do
          {:cont, acc} -> await_completion(port, session, turn, emit_event, acc)
          {:halt, result} -> result
        end

      {^port, {:exit_status, 0}} ->
        completion_from_acc(acc)

      {^port, {:exit_status, status}} ->
        exit_reason = if stop_requested?(session), do: :turn_cancelled, else: {:claude_agent_sdk_exit, status}
        failure = failure_summary(exit_reason)

        if terminal_emitted?(acc) do
          {:error, exit_reason}
        else
          emit_runtime_event(emit_event, :turn_failed, session, turn, failure.payload, raw: exit_reason)
          {:error, exit_reason}
        end
    after
      timeout_ms ->
        failure = failure_summary(:turn_timeout)
        emit_runtime_event(emit_event, :turn_failed, session, turn, failure.payload, raw: :turn_timeout)
        {:error, :turn_timeout}
    end
  end

  defp handle_bridge_line(line, session, turn, emit_event, acc) do
    case Jason.decode(line) do
      {:ok, %{"orbit_event" => "sdk_message", "message" => message}} ->
        handle_sdk_message(message, session, turn, emit_event, acc)

      {:ok, %{"orbit_event" => "sdk_error", "error" => error}} ->
        reason = {:claude_agent_sdk_error, error}
        failure = failure_summary(reason)
        emit_runtime_event(emit_event, :turn_failed, session, turn, failure.payload, raw: error)
        {:halt, {:error, reason}}

      {:ok, %{"orbit_event" => "sdk_stopped", "error" => error}} ->
        reason = {:turn_cancelled, error}
        failure = failure_summary(:turn_cancelled)
        emit_runtime_event(emit_event, :turn_failed, session, turn, failure.payload, raw: error)
        {:halt, {:error, reason}}

      {:ok, frame} ->
        emit_runtime_event(emit_event, :tool_event, session, turn, %{"type" => "bridge_frame", "frame" => frame}, raw: frame)
        {:cont, acc}

      {:error, _reason} ->
        emit_runtime_event(
          emit_event,
          :output_delta,
          session,
          turn,
          %{"provider" => session.provider, "stream" => "stdout", "line" => line},
          raw: line
        )

        {:cont, acc}
    end
  end

  defp handle_sdk_message(%{"type" => "system", "subtype" => "init"} = message, session, turn, emit_event, acc) do
    maybe_store_sdk_session_id(session, message)

    emit_runtime_event(
      emit_event,
      :session_started,
      session,
      turn,
      %{
        "session_id" => Map.get(message, "session_id"),
        "cwd" => Map.get(message, "cwd"),
        "model" => Map.get(message, "model"),
        "permission_mode" => Map.get(message, "permissionMode"),
        "tools" => Map.get(message, "tools"),
        "api_key_source" => Map.get(message, "apiKeySource"),
        "claude_code_version" => Map.get(message, "claude_code_version")
      }
      |> compact_map(),
      raw: message,
      source_event: "system:init"
    )

    {:cont, acc}
  end

  defp handle_sdk_message(%{"type" => "assistant"} = message, session, turn, emit_event, acc) do
    maybe_store_sdk_session_id(session, message)

    message
    |> content_blocks()
    |> Enum.each(fn
      %{"type" => "text", "text" => text} = block when is_binary(text) ->
        emit_runtime_event(
          emit_event,
          :output_delta,
          session,
          turn,
          %{"provider" => session.provider, "stream" => "assistant", "text" => text},
          raw: block,
          source_event: "assistant:text"
        )

      %{"type" => "tool_use"} = block ->
        emit_runtime_event(
          emit_event,
          :tool_event,
          session,
          turn,
          %{
            "type" => "tool_use",
            "tool_name" => Map.get(block, "name"),
            "tool_use_id" => Map.get(block, "id"),
            "input" => Map.get(block, "input")
          }
          |> compact_map(),
          raw: block,
          source_event: "assistant:tool_use"
        )

      block ->
        emit_runtime_event(
          emit_event,
          :tool_event,
          session,
          turn,
          %{"type" => "assistant_block", "block" => block},
          raw: block,
          source_event: "assistant:block"
        )
    end)

    {:cont, acc}
  end

  defp handle_sdk_message(%{"type" => "user"} = message, session, turn, emit_event, acc) do
    maybe_store_sdk_session_id(session, message)

    message
    |> content_blocks()
    |> Enum.each(fn
      %{"type" => "tool_result"} = block ->
        emit_runtime_event(
          emit_event,
          :tool_event,
          session,
          turn,
          %{
            "type" => "tool_result",
            "tool_use_id" => Map.get(block, "tool_use_id"),
            "content" => Map.get(block, "content"),
            "is_error" => Map.get(block, "is_error")
          }
          |> compact_map(),
          raw: block,
          source_event: "user:tool_result"
        )

      block ->
        emit_runtime_event(
          emit_event,
          :tool_event,
          session,
          turn,
          %{"type" => "user_block", "block" => block},
          raw: block,
          source_event: "user:block"
        )
    end)

    {:cont, acc}
  end

  defp handle_sdk_message(%{"type" => "stream_event"} = message, session, turn, emit_event, acc) do
    maybe_store_sdk_session_id(session, message)
    stream_event = Map.get(message, "event", %{})

    case stream_text_delta(stream_event) do
      text when is_binary(text) and text != "" ->
        emit_runtime_event(
          emit_event,
          :output_delta,
          session,
          turn,
          %{"provider" => session.provider, "stream" => "assistant", "text" => text, "partial" => true},
          raw: message,
          source_event: "stream_event:text_delta"
        )

      _ ->
        emit_runtime_event(
          emit_event,
          :tool_event,
          session,
          turn,
          %{
            "type" => "stream_event",
            "event_type" => Map.get(stream_event, "type"),
            "parent_tool_use_id" => Map.get(message, "parent_tool_use_id")
          }
          |> compact_map(),
          raw: message,
          source_event: "stream_event"
        )
    end

    {:cont, acc}
  end

  defp handle_sdk_message(%{"type" => "system", "subtype" => "permission_denied"} = message, session, turn, emit_event, acc) do
    maybe_store_sdk_session_id(session, message)

    emit_runtime_event(
      emit_event,
      :approval_needed,
      session,
      turn,
      %{
        "type" => "permission_denied",
        "tool_name" => Map.get(message, "tool_name"),
        "tool_input" => Map.get(message, "tool_input")
      }
      |> compact_map(),
      raw: message,
      source_event: "system:permission_denied"
    )

    {:cont, acc}
  end

  defp handle_sdk_message(%{"type" => "result"} = message, session, turn, emit_event, acc) do
    maybe_store_sdk_session_id(session, message)
    summary = summarize_result(message)
    event = if summary.status == :completed, do: :turn_completed, else: :turn_failed
    reason = {:claude_agent_sdk_result_error, result_message(message)}

    emit_runtime_event(
      emit_event,
      event,
      session,
      turn,
      result_payload(message),
      raw: message,
      source_event: "result"
    )

    if summary.status == :completed do
      {:cont, Map.merge(acc, %{summary: summary, terminal_emitted?: true})}
    else
      {:halt, {:error, reason}}
    end
  end

  defp handle_sdk_message(%{"type" => type} = message, session, turn, emit_event, acc) do
    maybe_store_sdk_session_id(session, message)

    emit_runtime_event(
      emit_event,
      :tool_event,
      session,
      turn,
      %{"type" => type, "message" => Map.drop(message, ["type"])},
      raw: message,
      source_event: type
    )

    {:cont, acc}
  end

  defp handle_sdk_message(message, session, turn, emit_event, acc) do
    emit_runtime_event(emit_event, :tool_event, session, turn, %{"type" => "sdk_message", "message" => message}, raw: message)
    {:cont, acc}
  end

  defp completion_from_acc(%{summary: summary}), do: {:ok, summary}

  defp completion_from_acc(acc) do
    if Map.get(acc, :terminal_emitted?) do
      {:error, :claude_agent_sdk_missing_summary}
    else
      {:error, :claude_agent_sdk_missing_result}
    end
  end

  defp failure_summary(:turn_timeout), do: %{payload: %{"reason" => "turn_timeout", "type" => "timeout"}}
  defp failure_summary(:turn_cancelled), do: %{payload: %{"reason" => "turn_cancelled", "type" => "cancelled"}}

  defp failure_summary({:claude_agent_sdk_error, %{"error" => error}}) do
    failure_summary({:claude_agent_sdk_error, error})
  end

  defp failure_summary({:claude_agent_sdk_error, error}) when is_map(error) do
    %{
      payload:
        %{
          "reason" => Map.get(error, "message") || inspect(error),
          "type" => Map.get(error, "code") || "sdk_error",
          "error" => error
        }
        |> compact_map()
    }
  end

  defp failure_summary(reason), do: %{payload: %{"reason" => inspect(reason), "type" => "failure"}}

  defp terminal_emitted?(acc), do: Map.get(acc, :terminal_emitted?) == true

  defp emit_runtime_event(emit_event, event, session, turn, payload, opts \\ []) do
    runtime_event = runtime_event!(event, session, turn, payload, opts)
    emit_event.(runtime_event)
    runtime_event
  end

  defp runtime_event!(event, session, turn, payload, opts) do
    attrs =
      %{
        session_id: runtime_session_id(session),
        provider: session.provider,
        harness: session.harness,
        model: Map.get(session, :model),
        payload: payload
      }
      |> maybe_put(:turn_id, turn && Map.get(turn, :turn_id))
      |> maybe_put(:raw, Keyword.get(opts, :raw))
      |> maybe_put(:source_event, Keyword.get(opts, :source_event))

    case AgentRuntime.new_event(event, attrs) do
      {:ok, runtime_event} -> runtime_event
      {:error, reason} -> raise ArgumentError, "invalid runtime event #{inspect(event)}: #{inspect(reason)}"
    end
  end

  defp emit_legacy_message(on_message, runtime_event) do
    on_message.(legacy_message(runtime_event))
  end

  defp legacy_message(%{event: :output_delta} = runtime_event) do
    runtime_event
    |> legacy_base(:notification)
    |> Map.put(:payload, runtime_event.payload)
    |> Map.put(:raw, Map.get(runtime_event, :raw))
  end

  defp legacy_message(%{event: :tool_event} = runtime_event) do
    runtime_event
    |> legacy_base(:notification)
    |> Map.put(:payload, runtime_event.payload)
    |> Map.put(:raw, Map.get(runtime_event, :raw))
  end

  defp legacy_message(%{event: :approval_needed} = runtime_event) do
    runtime_event
    |> legacy_base(:approval_required)
    |> Map.put(:payload, runtime_event.payload)
    |> Map.put(:raw, Map.get(runtime_event, :raw))
  end

  defp legacy_message(%{event: :turn_completed} = runtime_event) do
    runtime_event
    |> legacy_base(:turn_completed)
    |> Map.put(:result, :turn_completed)
    |> maybe_put(:usage, get_in(runtime_event.payload, ["usage"]))
  end

  defp legacy_message(%{event: :turn_failed, payload: %{"type" => "cancelled"}} = runtime_event) do
    runtime_event
    |> legacy_base(:turn_cancelled)
    |> Map.put(:reason, :turn_cancelled)
  end

  defp legacy_message(%{event: :turn_failed} = runtime_event) do
    runtime_event
    |> legacy_base(:turn_ended_with_error)
    |> Map.put(:reason, legacy_failure_reason(runtime_event))
  end

  defp legacy_message(%{event: event} = runtime_event) do
    legacy_base(runtime_event, event)
  end

  defp legacy_base(runtime_event, event) do
    %{
      event: event,
      timestamp: runtime_event.timestamp,
      session_id: runtime_event.session_id,
      agent_provider: runtime_event.provider,
      agent_harness: runtime_event.harness,
      agent_model: Map.get(runtime_event, :model),
      payload: runtime_event.payload,
      runtime_event: runtime_event
    }
  end

  defp legacy_failure_reason(%{raw: reason}), do: reason
  defp legacy_failure_reason(%{payload: %{"reason" => reason}}), do: reason
  defp legacy_failure_reason(_runtime_event), do: :unknown

  defp result_payload(message) do
    %{
      "method" => "turn/completed",
      "session_id" => Map.get(message, "session_id"),
      "subtype" => Map.get(message, "subtype"),
      "is_error" => Map.get(message, "is_error"),
      "result" => Map.get(message, "result"),
      "num_turns" => Map.get(message, "num_turns"),
      "stop_reason" => Map.get(message, "stop_reason"),
      "total_cost_usd" => Map.get(message, "total_cost_usd"),
      "usage" => usage_from_result(message),
      "model_usage" => Map.get(message, "modelUsage"),
      "permission_denials" => Map.get(message, "permission_denials")
    }
    |> compact_map()
  end

  defp usage_from_result(message) when is_map(message) do
    cond do
      is_map(Map.get(message, "usage")) ->
        Map.get(message, "usage")

      is_map(Map.get(message, "modelUsage")) ->
        message
        |> Map.get("modelUsage")
        |> aggregate_model_usage()

      true ->
        %{}
    end
  end

  defp aggregate_model_usage(model_usage) when is_map(model_usage) do
    Enum.reduce(model_usage, %{}, fn {_model, usage}, acc ->
      if is_map(usage) do
        acc
        |> add_usage("input_tokens", token_value(usage, ["input_tokens", "inputTokens", "prompt_tokens", "promptTokens"]))
        |> add_usage("output_tokens", token_value(usage, ["output_tokens", "outputTokens", "completion_tokens", "completionTokens"]))
        |> add_usage("total_tokens", token_value(usage, ["total_tokens", "totalTokens", "total"]))
      else
        acc
      end
    end)
  end

  defp aggregate_model_usage(_model_usage), do: %{}

  defp add_usage(acc, _key, nil), do: acc
  defp add_usage(acc, key, value), do: Map.update(acc, key, value, &(&1 + value))

  defp token_value(map, keys) do
    Enum.find_value(keys, fn key ->
      case Map.get(map, key) do
        value when is_integer(value) and value >= 0 -> value
        _ -> nil
      end
    end)
  end

  defp result_message(%{"result" => result}) when is_binary(result) and result != "", do: result
  defp result_message(%{"subtype" => subtype}) when is_binary(subtype), do: subtype
  defp result_message(_result), do: nil

  defp content_blocks(%{"message" => %{"content" => blocks}}) when is_list(blocks), do: blocks
  defp content_blocks(%{"content" => blocks}) when is_list(blocks), do: blocks
  defp content_blocks(_message), do: []

  defp stream_text_delta(%{"delta" => %{"type" => "text_delta", "text" => text}}), do: text
  defp stream_text_delta(%{"delta" => %{"text" => text}}), do: text
  defp stream_text_delta(_stream_event), do: nil

  defp maybe_store_sdk_session_id(session, message) do
    case Map.get(message, "session_id") do
      session_id when is_binary(session_id) and session_id != "" ->
        state_pid = session_state_pid(session)

        if is_pid(state_pid) and Process.alive?(state_pid) do
          Agent.update(state_pid, &Map.put(&1, :sdk_session_id, session_id))
        end

        :ok

      _ ->
        :ok
    end
  end

  defp current_sdk_session_id(state_pid) when is_pid(state_pid) do
    Agent.get(state_pid, &Map.get(&1, :sdk_session_id))
  end

  defp mark_turn_active(state_pid, port, turn_id) do
    Agent.update(state_pid, fn state ->
      state
      |> Map.put(:active_port, port)
      |> Map.put(:active_turn_id, turn_id)
      |> Map.put(:stop_requested, false)
    end)
  end

  defp clear_turn_active(session) do
    case live_state_pid(session) do
      state_pid when is_pid(state_pid) ->
        Agent.update(state_pid, fn state ->
          state
          |> Map.put(:active_port, nil)
          |> Map.put(:active_turn_id, nil)
        end)

      _ ->
        :ok
    end
  end

  defp stop_active_turn(state_pid) when is_pid(state_pid) do
    Agent.get_and_update(state_pid, fn state ->
      port = Map.get(state, :active_port)
      next_state = Map.put(state, :stop_requested, true)
      {port, next_state}
    end)
    |> case do
      port when is_port(port) ->
        safe_port_command(port, Jason.encode!(%{type: "abort"}) <> "\n")
        :active

      _ ->
        :idle
    end
  catch
    :exit, _reason -> :idle
  end

  defp stop_requested?(session) do
    case live_state_pid(session) do
      state_pid when is_pid(state_pid) ->
        Agent.get(state_pid, &(Map.get(&1, :stop_requested) == true))

      _ ->
        false
    end
  end

  defp safe_port_command(port, data) when is_port(port) do
    Port.command(port, data)
    :ok
  rescue
    _error -> :ok
  end

  defp stop_state(state_pid) when is_pid(state_pid) do
    if Process.alive?(state_pid), do: Agent.stop(state_pid, :normal, 100), else: :ok
  catch
    :exit, _reason -> :ok
  end

  defp start_session_state(provider) do
    Agent.start_link(fn ->
      %{
        sdk_session_id: Map.get(provider, "session_id"),
        active_port: nil,
        active_turn_id: nil,
        stop_requested: false
      }
    end)
  end

  defp session_state_pid(session) do
    session
    |> Map.get(:provider_session, %{})
    |> Map.get(:state_pid)
  end

  defp runtime_session_id(session) do
    case live_state_pid(session) do
      state_pid when is_pid(state_pid) ->
        Agent.get(state_pid, &(Map.get(&1, :sdk_session_id) || session.session_id))

      _ ->
        session.session_id
    end
  end

  defp live_state_pid(session) do
    case session_state_pid(session) do
      state_pid when is_pid(state_pid) ->
        if Process.alive?(state_pid), do: state_pid

      _ ->
        nil
    end
  end

  defp request_workspace(request) do
    case request_value(request, :workspace) do
      workspace when is_binary(workspace) and workspace != "" -> {:ok, workspace}
      _ -> {:error, {:missing_or_invalid, :workspace}}
    end
  end

  defp request_provider(request) do
    case request_config_value(request, :provider) do
      provider when is_map(provider) -> {:ok, normalize_provider(provider)}
      _ -> {:error, :missing_claude_agent_sdk_provider}
    end
  end

  defp request_worker_host(request) do
    {:ok, request_config_value(request, :worker_host)}
  end

  defp normalize_provider(provider) do
    Enum.reduce(provider, %{}, fn {key, value}, acc ->
      Map.put(acc, to_string(key), value)
    end)
  end

  defp validate_workspace_cwd(workspace, nil) when is_binary(workspace) do
    expanded_workspace = Path.expand(workspace)
    expanded_root = Path.expand(Config.settings!().workspace.root)
    expanded_root_prefix = expanded_root <> "/"

    with {:ok, canonical_workspace} <- PathSafety.canonicalize(expanded_workspace),
         {:ok, canonical_root} <- PathSafety.canonicalize(expanded_root) do
      canonical_root_prefix = canonical_root <> "/"

      cond do
        canonical_workspace == canonical_root ->
          {:error, {:invalid_workspace_cwd, :workspace_root, canonical_workspace}}

        String.starts_with?(canonical_workspace <> "/", canonical_root_prefix) ->
          {:ok, canonical_workspace}

        String.starts_with?(expanded_workspace <> "/", expanded_root_prefix) ->
          {:error, {:invalid_workspace_cwd, :symlink_escape, expanded_workspace, canonical_root}}

        true ->
          {:error, {:invalid_workspace_cwd, :outside_workspace_root, canonical_workspace, canonical_root}}
      end
    else
      {:error, {:path_canonicalize_failed, path, reason}} ->
        {:error, {:invalid_workspace_cwd, :path_unreadable, path, reason}}
    end
  end

  defp validate_workspace_cwd(workspace, worker_host) when is_binary(workspace) and is_binary(worker_host) do
    cond do
      String.trim(workspace) == "" ->
        {:error, {:invalid_workspace_cwd, :empty_remote_workspace, worker_host}}

      String.contains?(workspace, ["\n", "\r", <<0>>]) ->
        {:error, {:invalid_workspace_cwd, :invalid_remote_workspace, worker_host, workspace}}

      true ->
        {:ok, workspace}
    end
  end

  defp request_config_value(request, key) do
    config = request_value(request, :config) || %{}
    request_value(config, key)
  end

  defp request_value(map, key) when is_map(map) and is_atom(key) do
    Map.get(map, key) || Map.get(map, Atom.to_string(key))
  end

  defp provider_timeout_ms(session) do
    session
    |> Map.get(:provider_session, %{})
    |> Map.get(:provider, %{})
    |> Map.get("timeout_ms", Config.settings!().codex.turn_timeout_ms)
  end

  defp sdk_command(%{"sdk_command" => command}) when is_binary(command) and command != "", do: command

  defp sdk_command(_provider) do
    "node #{shell_escape(default_bridge_path())}"
  end

  defp default_bridge_path do
    case :code.priv_dir(:orbit_elixir) do
      path when is_list(path) ->
        Path.join([to_string(path), "claude_agent_sdk", "bridge.mjs"])

      {:error, _reason} ->
        Path.expand("../../../priv/claude_agent_sdk/bridge.mjs", __DIR__)
    end
  end

  defp port_env(provider, issue) do
    [
      {"ORBIT_AGENT_PROVIDER", provider["name"]},
      {"ORBIT_AGENT_HARNESS", provider["harness"]},
      {"ORBIT_AGENT_MODEL", provider["model"] || ""},
      {"ORBIT_ISSUE_IDENTIFIER", issue_identifier(issue)}
    ]
    |> Enum.map(fn {key, value} -> {String.to_charlist(key), String.to_charlist(to_string(value))} end)
  end

  defp synthetic_session_id(provider) do
    "#{provider["name"]}-sdk-#{System.unique_integer([:positive, :monotonic])}"
  end

  defp session_id_from_summary(%{provider_result: %{"session_id" => session_id}}, _fallback) when is_binary(session_id),
    do: session_id

  defp session_id_from_summary(_summary, fallback), do: fallback

  defp issue_identifier(%{identifier: identifier}) when is_binary(identifier), do: identifier
  defp issue_identifier(_issue), do: ""

  defp issue_context(%{id: issue_id, identifier: identifier}) do
    "issue_id=#{issue_id} issue_identifier=#{identifier}"
  end

  defp issue_context(_issue), do: "issue_id=unknown issue_identifier=unknown"

  defp compact_map(map) when is_map(map) do
    map
    |> Enum.reject(fn {_key, value} -> is_nil(value) end)
    |> Map.new(fn {key, value} -> {key, compact_value(value)} end)
  end

  defp compact_value(value) when is_map(value), do: compact_map(value)
  defp compact_value(value) when is_list(value), do: Enum.map(value, &compact_value/1)
  defp compact_value(value), do: value

  defp maybe_put(map, _key, nil), do: map
  defp maybe_put(map, key, value), do: Map.put(map, key, value)

  defp default_on_message(_message), do: :ok

  defp shell_escape(value) when is_binary(value) do
    "'" <> String.replace(value, "'", "'\"'\"'") <> "'"
  end
end
