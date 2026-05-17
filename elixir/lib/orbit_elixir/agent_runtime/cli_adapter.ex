defmodule OrbitElixir.AgentRuntime.CLIAdapter do
  @moduledoc """
  Runtime adapter for one-shot CLI-based agent providers.

  Claude, Gemini, and custom CLI providers keep their configured shell commands,
  while Orbit sees a provider-neutral session, turn, and event stream.
  """

  @behaviour OrbitElixir.AgentRuntime

  require Logger

  alias OrbitElixir.AgentRuntime
  alias OrbitElixir.SSH

  @port_line_bytes 1_048_576

  @spec run_turn(map(), Path.t(), String.t(), map(), keyword()) :: {:ok, map()} | {:error, term()}
  def run_turn(provider, workspace, prompt, issue, opts \\ []) do
    worker_host = Keyword.get(opts, :worker_host)
    on_message = Keyword.get(opts, :on_message, &default_on_message/1)
    request = %{workspace: workspace, issue: issue, config: %{provider: provider, worker_host: worker_host}}

    with {:ok, session} <- start_session(request),
         {:ok, turn} <- send_turn_or_emit_failure(session, prompt, issue, on_message) do
      stream_turn(session, turn, on_message)
    end
  end

  defp send_turn_or_emit_failure(session, prompt, issue, on_message) do
    case send_turn(session, %{prompt: prompt, issue: issue}) do
      {:ok, turn} ->
        {:ok, turn}

      {:error, reason} ->
        failure_event = runtime_event!(:turn_failed, session, nil, failure_payload(reason), raw: reason)
        emit_legacy_message(on_message, failure_event)

        Logger.warning(
          "CLI agent startup failed for #{issue_context(issue)} " <>
            "provider=#{session.provider} session_id=#{session.session_id}: #{inspect(reason)}"
        )

        {:error, reason}
    end
  end

  defp stream_turn(session, turn, on_message) do
    case stream_events(session, turn, &emit_legacy_message(on_message, &1)) do
      {:ok, summary} ->
        {:ok,
         %{
           result: Map.get(summary, :provider_result, :turn_completed),
           session_id: session.session_id,
           provider: session.provider
         }}

      {:error, reason} ->
        {:error, reason}
    end
  end

  @impl true
  def start_session(request) when is_map(request) do
    with {:ok, workspace} <- request_workspace(request),
         {:ok, provider} <- request_provider(request) do
      session_id = session_id(provider)

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
           worker_host: request_config_value(request, :worker_host)
         }
       }}
    end
  end

  @impl true
  def send_turn(session, request) when is_map(session) and is_map(request) do
    provider_session = Map.get(session, :provider_session, %{})
    provider = Map.fetch!(provider_session, :provider)
    issue = request_value(request, :issue) || Map.get(provider_session, :issue) || %{}
    prompt = request_value(request, :prompt) || ""
    worker_host = Map.get(provider_session, :worker_host)

    case start_port(provider, session.workspace, prompt, issue, worker_host) do
      {:ok, port} ->
        {:ok,
         %{
           turn_id: session.session_id,
           session_id: session.session_id,
           provider_turn: %{
             port: port,
             command: provider["command"],
             issue: issue,
             prompt: prompt,
             worker_host: worker_host
           }
         }}

      {:error, reason} ->
        {:error, reason}
    end
  end

  @impl true
  def stream_events(session, turn, emit_event) when is_map(session) and is_map(turn) and is_function(emit_event, 1) do
    provider_turn = Map.get(turn, :provider_turn, %{})
    provider_session = Map.get(session, :provider_session, %{})
    provider = Map.fetch!(provider_session, :provider)
    issue = Map.get(provider_turn, :issue, %{})
    port = Map.fetch!(provider_turn, :port)

    emit_runtime_event(emit_event, :session_started, session, turn, %{
      "session_id" => session.session_id,
      "worker_host" => Map.get(provider_turn, :worker_host)
    })

    emit_runtime_event(emit_event, :turn_started, session, turn, %{
      "command" => Map.get(provider_turn, :command),
      "worker_host" => Map.get(provider_turn, :worker_host)
    })

    Logger.info("CLI agent session started for #{issue_context(issue)} provider=#{provider["name"]} session_id=#{session.session_id}")

    case await_completion(port, provider, emit_event, session, turn, "") do
      {:ok, result} ->
        emit_runtime_event(emit_event, :turn_completed, session, turn, %{"result" => Atom.to_string(result)}, raw: result)
        Logger.info("CLI agent session completed for #{issue_context(issue)} provider=#{provider["name"]} session_id=#{session.session_id}")
        {:ok, summarize_result(result)}

      {:error, reason} ->
        emit_runtime_event(emit_event, :turn_failed, session, turn, failure_payload(reason), raw: reason)
        Logger.warning("CLI agent session failed for #{issue_context(issue)} provider=#{provider["name"]} session_id=#{session.session_id}: #{inspect(reason)}")
        {:error, reason}
    end
  end

  @impl true
  def stop_session(_session), do: :ok

  @impl true
  def read_diff(%{workspace: workspace}) when is_binary(workspace) do
    case System.cmd("git", ["diff", "--"], cd: workspace, stderr_to_stdout: true) do
      {diff, 0} -> {:ok, diff}
      {output, status} -> {:error, {:git_diff_failed, status, output}}
    end
  rescue
    error -> {:error, {:git_diff_failed, Exception.message(error)}}
  end

  @impl true
  def summarize_result(:turn_completed), do: %{status: :completed, provider_result: :turn_completed}
  def summarize_result({:provider_exit, provider, status}), do: %{status: :failed, message: "#{provider} exited with status #{status}", provider_result: {:provider_exit, provider, status}}
  def summarize_result(:turn_timeout), do: %{status: :failed, message: "turn timed out", provider_result: :turn_timeout}
  def summarize_result(reason), do: %{status: :failed, message: inspect(reason), provider_result: reason}

  defp start_port(provider, workspace, prompt, issue, nil) do
    executable = System.find_executable("bash")

    if is_nil(executable) do
      {:error, :bash_not_found}
    else
      port =
        Port.open(
          {:spawn_executable, String.to_charlist(executable)},
          [
            :binary,
            :exit_status,
            :stderr_to_stdout,
            args: [~c"-lc", String.to_charlist(provider["command"])],
            cd: String.to_charlist(workspace),
            env: port_env(provider, prompt, issue),
            line: @port_line_bytes
          ]
        )

      {:ok, port}
    end
  end

  defp start_port(provider, workspace, prompt, issue, worker_host) when is_binary(worker_host) do
    command =
      [
        "cd #{shell_escape(workspace)}",
        remote_export("ORBIT_AGENT_PROVIDER", provider["name"]),
        remote_export("ORBIT_AGENT_HARNESS", provider["harness"]),
        remote_export("ORBIT_AGENT_MODEL", provider["model"] || ""),
        remote_export("ORBIT_AGENT_PROMPT", prompt),
        remote_export("ORBIT_ISSUE_IDENTIFIER", issue_identifier(issue)),
        "exec #{provider["command"]}"
      ]
      |> Enum.join("\n")

    SSH.start_port(worker_host, command, line: @port_line_bytes)
  end

  defp await_completion(port, provider, emit_event, session, turn, pending_line) do
    receive do
      {^port, {:data, {:eol, chunk}}} ->
        line = pending_line <> to_string(chunk)
        emit_output_line(emit_event, session, turn, line)
        await_completion(port, provider, emit_event, session, turn, "")

      {^port, {:data, {:noeol, chunk}}} ->
        await_completion(port, provider, emit_event, session, turn, pending_line <> to_string(chunk))

      {^port, {:exit_status, 0}} ->
        pending_line = drain_final_output(port, emit_event, session, turn, pending_line)
        if pending_line != "", do: emit_output_line(emit_event, session, turn, pending_line)
        {:ok, :turn_completed}

      {^port, {:exit_status, status}} ->
        pending_line = drain_final_output(port, emit_event, session, turn, pending_line)
        if pending_line != "", do: emit_output_line(emit_event, session, turn, pending_line)
        {:error, {:provider_exit, provider["name"], status}}
    after
      provider["timeout_ms"] ->
        Port.close(port)
        {:error, :turn_timeout}
    end
  end

  defp drain_final_output(port, emit_event, session, turn, pending_line) do
    receive do
      {^port, {:data, {:eol, chunk}}} ->
        line = pending_line <> to_string(chunk)
        emit_output_line(emit_event, session, turn, line)
        drain_final_output(port, emit_event, session, turn, "")

      {^port, {:data, {:noeol, chunk}}} ->
        drain_final_output(port, emit_event, session, turn, pending_line <> to_string(chunk))
    after
      10 -> pending_line
    end
  end

  defp emit_output_line(emit_event, session, turn, line) do
    emit_runtime_event(
      emit_event,
      :output_delta,
      session,
      turn,
      %{
        "provider" => session.provider,
        "stream" => "stdout",
        "line" => line
      },
      raw: line
    )
  end

  defp emit_runtime_event(emit_event, event, session, turn, payload, opts \\ []) do
    runtime_event = runtime_event!(event, session, turn, payload, opts)
    emit_event.(runtime_event)
    runtime_event
  end

  defp runtime_event!(event, session, turn, payload, opts) do
    attrs =
      %{
        session_id: session.session_id,
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

  defp legacy_message(%{event: :turn_completed} = runtime_event) do
    runtime_event
    |> legacy_base(:turn_completed)
    |> Map.put(:result, :turn_completed)
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
  defp legacy_failure_reason(%{payload: %{reason: reason}}), do: reason
  defp legacy_failure_reason(_runtime_event), do: :unknown

  defp failure_payload(:turn_timeout), do: %{"reason" => "turn_timeout", "type" => "timeout"}

  defp failure_payload({:provider_exit, provider, status}) do
    %{
      "reason" => "provider_exit",
      "provider" => provider,
      "exit_status" => status,
      "type" => "exit"
    }
  end

  defp failure_payload(reason), do: %{"reason" => inspect(reason), "type" => "failure"}

  defp request_workspace(request) do
    case request_value(request, :workspace) do
      workspace when is_binary(workspace) and workspace != "" -> {:ok, workspace}
      _ -> {:error, {:missing_or_invalid, :workspace}}
    end
  end

  defp request_provider(request) do
    case request_config_value(request, :provider) do
      provider when is_map(provider) -> {:ok, normalize_provider(provider)}
      _ -> {:error, :missing_cli_provider}
    end
  end

  defp normalize_provider(provider) do
    Enum.reduce(provider, %{}, fn {key, value}, acc ->
      Map.put(acc, to_string(key), value)
    end)
  end

  defp request_config_value(request, key) do
    config = request_value(request, :config) || %{}
    request_value(config, key)
  end

  defp request_value(map, key) when is_map(map) and is_atom(key) do
    Map.get(map, key) || Map.get(map, Atom.to_string(key))
  end

  defp maybe_put(map, _key, nil), do: map
  defp maybe_put(map, key, value), do: Map.put(map, key, value)

  defp port_env(provider, prompt, issue) do
    [
      {"ORBIT_AGENT_PROVIDER", provider["name"]},
      {"ORBIT_AGENT_HARNESS", provider["harness"]},
      {"ORBIT_AGENT_MODEL", provider["model"] || ""},
      {"ORBIT_AGENT_PROMPT", prompt},
      {"ORBIT_ISSUE_IDENTIFIER", issue_identifier(issue)}
    ]
    |> Enum.map(fn {key, value} -> {String.to_charlist(key), String.to_charlist(to_string(value))} end)
  end

  defp remote_export(key, value), do: "export #{key}=#{shell_escape(to_string(value || ""))}"

  defp session_id(provider) do
    "#{provider["name"]}-#{System.unique_integer([:positive, :monotonic])}"
  end

  defp issue_identifier(%{identifier: identifier}) when is_binary(identifier), do: identifier
  defp issue_identifier(_issue), do: ""

  defp issue_context(%{id: issue_id, identifier: identifier}) do
    "issue_id=#{issue_id} issue_identifier=#{identifier}"
  end

  defp issue_context(_issue), do: "issue_id=unknown issue_identifier=unknown"

  defp default_on_message(_message), do: :ok

  defp shell_escape(value) when is_binary(value) do
    "'" <> String.replace(value, "'", "'\"'\"'") <> "'"
  end
end
