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
      "output_format" => provider_output_format(provider),
      "timeout_ms" => timeout_ms(provider),
      "worker_host" => Map.get(provider_turn, :worker_host)
    })

    Logger.info("CLI agent session started for #{issue_context(issue)} provider=#{provider["name"]} session_id=#{session.session_id}")

    case await_completion(port, provider, emit_event, session, turn, "") do
      {:ok, completion} ->
        case finalize_success(provider, completion) do
          {:ok, completion} ->
            emit_completion_output_delta(emit_event, session, turn, completion)
            completion = emit_diff_event_if_changed(emit_event, session, turn, completion)

            emit_runtime_event(
              emit_event,
              :turn_completed,
              session,
              turn,
              completion_payload(completion),
              raw: completion_raw(completion)
            )

            Logger.info("CLI agent session completed for #{issue_context(issue)} provider=#{provider["name"]} session_id=#{session.session_id}")
            {:ok, summarize_result({:turn_completed, completion})}

          {:error, reason, completion} ->
            completion = emit_diff_event_if_changed(emit_event, session, turn, completion)

            emit_runtime_event(
              emit_event,
              :turn_failed,
              session,
              turn,
              failure_payload(reason, completion),
              raw: reason
            )

            Logger.warning("CLI agent session failed for #{issue_context(issue)} provider=#{provider["name"]} session_id=#{session.session_id}: #{inspect(reason)}")
            {:error, reason}
        end

      {:error, reason, completion} ->
        completion =
          provider
          |> parse_provider_output(completion)
          |> then(&emit_diff_event_if_changed(emit_event, session, turn, &1))

        emit_runtime_event(emit_event, :turn_failed, session, turn, failure_payload(reason, completion), raw: reason)
        Logger.warning("CLI agent session failed for #{issue_context(issue)} provider=#{provider["name"]} session_id=#{session.session_id}: #{inspect(reason)}")
        {:error, reason}
    end
  end

  defp finalize_success(provider, completion) do
    completion = parse_provider_output(provider, completion)

    cond do
      Map.has_key?(completion, :provider_error) ->
        {:error, {:provider_error, provider["name"], completion.provider_error}, completion}

      Map.has_key?(completion, :parse_error) ->
        {:error, {:provider_output_parse_failed, provider["name"], completion.parse_error}, completion}

      true ->
        {:ok, completion}
    end
  end

  defp emit_completion_output_delta(emit_event, session, turn, %{response: response}) when is_binary(response) and response != "" do
    emit_runtime_event(
      emit_event,
      :output_delta,
      session,
      turn,
      %{
        "provider" => session.provider,
        "stream" => "stdout",
        "line" => response,
        "structured" => true,
        "source" => "provider_response"
      },
      raw: response
    )
  end

  defp emit_completion_output_delta(_emit_event, _session, _turn, _completion), do: :ok

  defp emit_diff_event_if_changed(emit_event, session, turn, completion) do
    case read_diff(session) do
      {:ok, diff} when is_binary(diff) and diff != "" ->
        summary = diff_summary(session, diff)

        emit_runtime_event(
          emit_event,
          :diff_changed,
          session,
          turn,
          Map.put(summary, "diff", diff),
          raw: diff
        )

        Map.put(completion, :diff_summary, summary)

      _result ->
        completion
    end
  end

  defp diff_summary(session, diff) do
    %{
      "method" => "workspace/diffChanged",
      "line_count" => line_count(diff),
      "files" => diff_files(Map.get(session, :workspace))
    }
  end

  defp diff_files(workspace) when is_binary(workspace) do
    case System.cmd("git", ["diff", "--name-only", "--"], cd: workspace, stderr_to_stdout: true) do
      {output, 0} ->
        output
        |> String.split("\n", trim: true)
        |> Enum.reject(&(&1 == ""))

      _result ->
        []
    end
  rescue
    _error -> []
  end

  defp diff_files(_workspace), do: []

  @impl true
  def stop_session(_session), do: :ok

  @impl true
  def read_diff(%{provider_session: %{worker_host: worker_host}}) when is_binary(worker_host) do
    {:error, :remote_diff_unsupported}
  end

  def read_diff(%{workspace: workspace}) when is_binary(workspace) do
    case System.cmd("git", ["diff", "--"], cd: workspace, stderr_to_stdout: true) do
      {diff, 0} -> {:ok, diff}
      {output, status} -> {:error, {:git_diff_failed, status, output}}
    end
  rescue
    error -> {:error, {:git_diff_failed, Exception.message(error)}}
  end

  def read_diff(_session), do: {:error, {:invalid_runtime_session, :workspace}}

  @impl true
  def summarize_result(:turn_completed), do: %{status: :completed, provider_result: :turn_completed}
  def summarize_result({:turn_completed, %{provider_result: _provider_result} = completion}), do: completed_summary(completion)
  def summarize_result({:turn_completed, %{response: _response} = completion}), do: completed_summary(completion)
  def summarize_result({:turn_completed, %{diff_summary: _diff_summary} = completion}), do: completed_summary(completion)
  def summarize_result({:turn_completed, _completion}), do: summarize_result(:turn_completed)
  def summarize_result({:provider_exit, provider, status}), do: %{status: :failed, message: "#{provider} exited with status #{status}", provider_result: {:provider_exit, provider, status}}
  def summarize_result(:turn_timeout), do: %{status: :failed, message: "turn timed out", provider_result: :turn_timeout}
  def summarize_result(reason), do: %{status: :failed, message: inspect(reason), provider_result: reason}

  defp completed_summary(completion) do
    %{
      status: :completed,
      provider_result: completion_result(completion)
    }
    |> maybe_put(:message, Map.get(completion, :response))
    |> maybe_put(:usage, Map.get(completion, :stats))
  end

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
    timeout_ms = timeout_ms(provider)
    deadline = System.monotonic_time(:millisecond) + timeout_ms
    await_completion(port, provider, emit_event, session, turn, pending_line, [], deadline)
  end

  defp await_completion(port, provider, emit_event, session, turn, pending_line, output_lines, deadline) do
    receive do
      {^port, {:data, {:eol, chunk}}} ->
        line = pending_line <> to_string(chunk)
        emit_output_line(provider, emit_event, session, turn, line)
        await_completion(port, provider, emit_event, session, turn, "", [line | output_lines], deadline)

      {^port, {:data, {:noeol, chunk}}} ->
        await_completion(
          port,
          provider,
          emit_event,
          session,
          turn,
          pending_line <> to_string(chunk),
          output_lines,
          deadline
        )

      {^port, {:exit_status, 0}} ->
        {pending_line, output_lines} =
          drain_final_output(port, provider, emit_event, session, turn, pending_line, output_lines)

        if pending_line != "", do: emit_output_line(provider, emit_event, session, turn, pending_line)
        {:ok, completion_from_output(provider, output_lines, pending_line)}

      {^port, {:exit_status, status}} ->
        {pending_line, output_lines} =
          drain_final_output(port, provider, emit_event, session, turn, pending_line, output_lines)

        if pending_line != "", do: emit_output_line(provider, emit_event, session, turn, pending_line)

        {:error, {:provider_exit, provider["name"], status}, completion_from_output(provider, output_lines, pending_line)}
    after
      timeout_remaining(deadline) ->
        if pending_line != "", do: emit_output_line(provider, emit_event, session, turn, pending_line)
        Port.close(port)
        {:error, :turn_timeout, completion_from_output(provider, output_lines, pending_line)}
    end
  end

  defp drain_final_output(port, provider, emit_event, session, turn, pending_line, output_lines) do
    receive do
      {^port, {:data, {:eol, chunk}}} ->
        line = pending_line <> to_string(chunk)
        emit_output_line(provider, emit_event, session, turn, line)
        drain_final_output(port, provider, emit_event, session, turn, "", [line | output_lines])

      {^port, {:data, {:noeol, chunk}}} ->
        drain_final_output(port, provider, emit_event, session, turn, pending_line <> to_string(chunk), output_lines)
    after
      10 -> {pending_line, output_lines}
    end
  end

  defp emit_output_line(provider, emit_event, session, turn, line) do
    if structured_output?(provider) do
      :ok
    else
      do_emit_output_line(emit_event, session, turn, line)
    end
  end

  defp do_emit_output_line(emit_event, session, turn, line) do
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

  defp completion_from_output(provider, output_lines, pending_line) do
    output_lines =
      if pending_line == "" do
        output_lines
      else
        [pending_line | output_lines]
      end

    ordered_lines = Enum.reverse(output_lines)

    %{
      output: Enum.join(ordered_lines, "\n"),
      output_format: provider_output_format(provider),
      output_lines: ordered_lines,
      output_tail: output_tail(ordered_lines),
      timeout_ms: timeout_ms(provider)
    }
  end

  defp parse_provider_output(provider, completion) do
    if structured_output?(provider) do
      parse_structured_provider_output(provider, completion)
    else
      completion
    end
  end

  defp parse_structured_provider_output(provider, completion) do
    case completion.output |> to_string() |> String.trim() do
      "" ->
        Map.put(completion, :parse_error, :empty_output)

      output ->
        put_structured_provider_result(provider, completion, output)
    end
  end

  defp put_structured_provider_result(provider, completion, output) do
    case decode_structured_output(provider, output) do
      {:ok, provider_result} ->
        completion
        |> Map.put(:provider_result, provider_result)
        |> maybe_put(:response, provider_response(provider_result))
        |> maybe_put(:stats, provider_stats(provider_result))
        |> maybe_put(:provider_error, provider_error(provider_result))

      {:error, reason} ->
        Map.put(completion, :parse_error, reason)
    end
  end

  defp decode_structured_output(provider, output) do
    case provider_output_format(provider) do
      "stream-json" -> decode_stream_json(output)
      _format -> decode_json_document(output)
    end
  end

  defp decode_stream_json(output) do
    output
    |> String.split("\n", trim: true)
    |> Enum.reduce_while({:ok, []}, fn line, {:ok, acc} ->
      case decode_json_document(line) do
        {:ok, event} -> {:cont, {:ok, [event | acc]}}
        {:error, reason} -> {:halt, {:error, reason}}
      end
    end)
    |> case do
      {:ok, events} -> {:ok, Enum.reverse(events)}
      {:error, reason} -> {:error, reason}
    end
  end

  defp decode_json_document(output) do
    case Jason.decode(output) do
      {:ok, decoded} ->
        {:ok, decoded}

      {:error, error} ->
        decode_extracted_json_document(output, error)
    end
  end

  defp decode_extracted_json_document(output, original_error) do
    case extract_json_document(output) do
      {:ok, candidate} ->
        decode_json_candidate(candidate)

      :error ->
        {:error, {:json_decode_failed, Exception.message(original_error)}}
    end
  end

  defp decode_json_candidate(candidate) do
    case Jason.decode(candidate) do
      {:ok, decoded} -> {:ok, decoded}
      {:error, error} -> {:error, {:json_decode_failed, Exception.message(error)}}
    end
  end

  defp extract_json_document(output) do
    with :error <- json_candidate(output, "{", "}") do
      json_candidate(output, "[", "]")
    end
  end

  defp json_candidate(output, open, close) do
    with {start, _open_length} <- :binary.match(output, open),
         [_match | _rest] = matches <- :binary.matches(output, close),
         {finish, close_length} <- List.last(matches),
         true <- finish >= start do
      {:ok, binary_part(output, start, finish + close_length - start)}
    else
      _result -> :error
    end
  end

  defp provider_response(%{"response" => response}) when is_binary(response), do: response
  defp provider_response(%{"text" => response}) when is_binary(response), do: response
  defp provider_response(%{response: response}) when is_binary(response), do: response
  defp provider_response(results) when is_list(results), do: results |> Enum.reverse() |> Enum.find_value(&provider_response/1)
  defp provider_response(_result), do: nil

  defp provider_stats(%{"stats" => stats}) when is_map(stats), do: stats
  defp provider_stats(%{stats: stats}) when is_map(stats), do: stats
  defp provider_stats(results) when is_list(results), do: results |> Enum.reverse() |> Enum.find_value(&provider_stats/1)
  defp provider_stats(_result), do: nil

  defp provider_error(%{"error" => error}) when is_map(error) and map_size(error) > 0, do: error
  defp provider_error(%{"error" => error}) when is_binary(error) and error != "", do: %{"message" => error}
  defp provider_error(%{error: error}) when is_map(error) and map_size(error) > 0, do: error
  defp provider_error(results) when is_list(results), do: results |> Enum.reverse() |> Enum.find_value(&provider_error/1)
  defp provider_error(_result), do: nil

  defp completion_payload(completion) do
    %{"result" => "turn_completed"}
    |> maybe_put("output_format", Map.get(completion, :output_format))
    |> maybe_put("response", Map.get(completion, :response))
    |> maybe_put("stats", Map.get(completion, :stats))
    |> maybe_put("diff", Map.get(completion, :diff_summary))
  end

  defp completion_result(completion) do
    %{
      result: :turn_completed
    }
    |> maybe_put(:response, Map.get(completion, :response))
    |> maybe_put(:stats, Map.get(completion, :stats))
    |> maybe_put(:provider_result, Map.get(completion, :provider_result))
    |> maybe_put(:output_format, Map.get(completion, :output_format))
    |> maybe_put(:diff, Map.get(completion, :diff_summary))
  end

  defp completion_raw(%{provider_result: provider_result}), do: provider_result
  defp completion_raw(completion), do: completion

  defp provider_output_format(provider) do
    explicit_format = provider["output_format"]
    command = provider["command"] || ""

    cond do
      is_binary(explicit_format) and explicit_format != "" ->
        String.downcase(explicit_format)

      String.contains?(command, "--output-format stream-json") or String.contains?(command, "--output-format=stream-json") ->
        "stream-json"

      String.contains?(command, "--output-format json") or String.contains?(command, "--output-format=json") ->
        "json"

      true ->
        nil
    end
  end

  defp structured_output?(provider), do: provider_output_format(provider) in ["json", "stream-json"]

  defp timeout_ms(%{"timeout_ms" => timeout_ms}) when is_integer(timeout_ms) and timeout_ms > 0, do: timeout_ms
  defp timeout_ms(_provider), do: 60_000

  defp timeout_remaining(deadline) do
    max(deadline - System.monotonic_time(:millisecond), 0)
  end

  defp output_tail(lines) do
    lines
    |> Enum.take(-20)
    |> Enum.join("\n")
    |> blank_to_nil()
  end

  defp line_count(text) do
    text
    |> String.split("\n", trim: true)
    |> length()
  end

  defp blank_to_nil(""), do: nil
  defp blank_to_nil(value), do: value

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

  defp failure_payload(reason, completion \\ %{})

  defp failure_payload(:turn_timeout, completion) do
    %{"reason" => "turn_timeout", "type" => "timeout"}
    |> maybe_put("timeout_ms", Map.get(completion, :timeout_ms))
    |> add_completion_context(completion)
  end

  defp failure_payload({:provider_exit, provider, status}, completion) do
    %{
      "reason" => "provider_exit",
      "provider" => provider,
      "exit_status" => status,
      "type" => "exit"
    }
    |> add_completion_context(completion)
  end

  defp failure_payload({:provider_error, provider, error}, completion) do
    %{
      "reason" => "provider_error",
      "provider" => provider,
      "error" => error,
      "type" => "provider_error"
    }
    |> add_completion_context(completion)
  end

  defp failure_payload({:provider_output_parse_failed, provider, reason}, completion) do
    %{
      "reason" => "provider_output_parse_failed",
      "provider" => provider,
      "parse_error" => inspect(reason),
      "type" => "output_parse"
    }
    |> add_completion_context(completion)
  end

  defp failure_payload(reason, completion) do
    %{"reason" => inspect(reason), "type" => "failure"}
    |> add_completion_context(completion)
  end

  defp add_completion_context(payload, completion) do
    payload
    |> maybe_put("output_format", Map.get(completion, :output_format))
    |> maybe_put("output_tail", Map.get(completion, :output_tail))
    |> maybe_put("provider_error", Map.get(completion, :provider_error))
    |> maybe_put("parse_error", completion_parse_error(completion))
    |> maybe_put("diff", Map.get(completion, :diff_summary))
  end

  defp completion_parse_error(%{parse_error: reason}), do: inspect(reason)
  defp completion_parse_error(_completion), do: nil

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
