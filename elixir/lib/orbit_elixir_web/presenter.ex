defmodule OrbitElixirWeb.Presenter do
  @moduledoc """
  Shared projections for the observability API and dashboard.
  """

  alias OrbitElixir.{Config, Orchestrator, StatusDashboard}

  @spec state_payload(GenServer.name(), timeout()) :: map()
  def state_payload(orchestrator, snapshot_timeout_ms) do
    generated_at = DateTime.utc_now() |> DateTime.truncate(:second) |> DateTime.to_iso8601()

    case Orchestrator.snapshot(orchestrator, snapshot_timeout_ms) do
      %{} = snapshot ->
        %{
          generated_at: generated_at,
          counts: %{
            running: length(snapshot.running),
            retrying: length(snapshot.retrying)
          },
          running: Enum.map(snapshot.running, &running_entry_payload/1),
          retrying: Enum.map(snapshot.retrying, &retry_entry_payload/1),
          codex_totals: snapshot.codex_totals,
          rate_limits: snapshot.rate_limits
        }

      :timeout ->
        %{generated_at: generated_at, error: %{code: "snapshot_timeout", message: "Snapshot timed out"}}

      :unavailable ->
        %{generated_at: generated_at, error: %{code: "snapshot_unavailable", message: "Snapshot unavailable"}}
    end
  end

  @spec issue_payload(String.t(), GenServer.name(), timeout()) :: {:ok, map()} | {:error, :issue_not_found}
  def issue_payload(issue_identifier, orchestrator, snapshot_timeout_ms) when is_binary(issue_identifier) do
    case Orchestrator.snapshot(orchestrator, snapshot_timeout_ms) do
      %{} = snapshot ->
        running = Enum.find(snapshot.running, &(&1.identifier == issue_identifier))
        retry = Enum.find(snapshot.retrying, &(&1.identifier == issue_identifier))

        if is_nil(running) and is_nil(retry) do
          {:error, :issue_not_found}
        else
          {:ok, issue_payload_body(issue_identifier, running, retry)}
        end

      _ ->
        {:error, :issue_not_found}
    end
  end

  @spec refresh_payload(GenServer.name()) :: {:ok, map()} | {:error, :unavailable}
  def refresh_payload(orchestrator) do
    case Orchestrator.request_refresh(orchestrator) do
      :unavailable ->
        {:error, :unavailable}

      payload ->
        {:ok, Map.update!(payload, :requested_at, &DateTime.to_iso8601/1)}
    end
  end

  defp issue_payload_body(issue_identifier, running, retry) do
    %{
      issue_identifier: issue_identifier,
      issue_id: issue_id_from_entries(running, retry),
      status: issue_status(running, retry),
      workspace: %{
        path: workspace_path(issue_identifier, running, retry),
        host: workspace_host(running, retry)
      },
      attempts: %{
        restart_count: restart_count(retry),
        current_retry_attempt: retry_attempt(retry)
      },
      running: running && running_issue_payload(running),
      retry: retry && retry_issue_payload(retry),
      logs: %{
        codex_session_logs: []
      },
      recent_events: (running && recent_events_payload(running)) || [],
      last_error: retry && retry.error,
      tracked: %{}
    }
  end

  defp issue_id_from_entries(running, retry),
    do: (running && running.issue_id) || (retry && retry.issue_id)

  defp restart_count(retry), do: max(retry_attempt(retry) - 1, 0)
  defp retry_attempt(nil), do: 0
  defp retry_attempt(retry), do: retry.attempt || 0

  defp issue_status(_running, nil), do: "running"
  defp issue_status(nil, _retry), do: "retrying"
  defp issue_status(_running, _retry), do: "running"

  defp running_entry_payload(entry) do
    runtime_status = runtime_status_payload(entry)

    %{
      issue_id: entry.issue_id,
      issue_identifier: entry.identifier,
      state: entry.state,
      worker_host: Map.get(entry, :worker_host),
      workspace_path: Map.get(entry, :workspace_path),
      provider_name: runtime_status.provider_name,
      adapter_type: runtime_status.adapter_type,
      provider: provider_payload(entry),
      runtime: runtime_payload(entry),
      runtime_status: runtime_status,
      session_id: entry.session_id,
      turn_count: Map.get(entry, :turn_count, 0),
      last_event: entry.last_codex_event,
      last_message: summarize_message(entry.last_codex_message),
      latest_event: runtime_status.latest_event,
      latest_message: runtime_status.latest_message,
      error_state: runtime_status.error_state,
      error_message: runtime_status.error_message,
      started_at: iso8601(entry.started_at),
      last_event_at: iso8601(entry.last_codex_timestamp),
      tokens: %{
        input_tokens: entry.codex_input_tokens,
        output_tokens: entry.codex_output_tokens,
        total_tokens: entry.codex_total_tokens
      }
    }
  end

  defp retry_entry_payload(entry) do
    %{
      issue_id: entry.issue_id,
      issue_identifier: entry.identifier,
      attempt: entry.attempt,
      due_at: due_at_iso8601(entry.due_in_ms),
      error: entry.error,
      worker_host: Map.get(entry, :worker_host),
      workspace_path: Map.get(entry, :workspace_path)
    }
  end

  defp running_issue_payload(running) do
    runtime_status = runtime_status_payload(running)

    %{
      worker_host: Map.get(running, :worker_host),
      workspace_path: Map.get(running, :workspace_path),
      provider_name: runtime_status.provider_name,
      adapter_type: runtime_status.adapter_type,
      provider: provider_payload(running),
      runtime: runtime_payload(running),
      runtime_status: runtime_status,
      session_id: running.session_id,
      turn_count: Map.get(running, :turn_count, 0),
      state: running.state,
      started_at: iso8601(running.started_at),
      last_event: running.last_codex_event,
      last_message: summarize_message(running.last_codex_message),
      latest_event: runtime_status.latest_event,
      latest_message: runtime_status.latest_message,
      error_state: runtime_status.error_state,
      error_message: runtime_status.error_message,
      last_event_at: iso8601(running.last_codex_timestamp),
      tokens: %{
        input_tokens: running.codex_input_tokens,
        output_tokens: running.codex_output_tokens,
        total_tokens: running.codex_total_tokens
      }
    }
  end

  defp retry_issue_payload(retry) do
    %{
      attempt: retry.attempt,
      due_at: due_at_iso8601(retry.due_in_ms),
      error: retry.error,
      worker_host: Map.get(retry, :worker_host),
      workspace_path: Map.get(retry, :workspace_path)
    }
  end

  defp provider_payload(entry) do
    provider_name = Map.get(entry, :agent_provider)
    adapter_type = Map.get(entry, :agent_harness)

    %{
      name: provider_name,
      provider_name: provider_name,
      harness: adapter_type,
      adapter_type: adapter_type,
      model: Map.get(entry, :agent_model)
    }
  end

  defp runtime_payload(entry) do
    runtime_status = runtime_status_payload(entry)

    %{
      session_id: Map.get(entry, :session_id),
      turn_count: Map.get(entry, :turn_count, 0),
      last_event: Map.get(entry, :last_codex_event),
      last_event_at: iso8601(Map.get(entry, :last_codex_timestamp)),
      latest_event: runtime_status.latest_event,
      latest_message: runtime_status.latest_message,
      error_state: runtime_status.error_state,
      error_message: runtime_status.error_message,
      last_runtime_event: json_safe(Map.get(entry, :last_runtime_event))
    }
  end

  defp runtime_status_payload(entry) do
    error_message = runtime_error_message(entry)

    %{
      provider_name: Map.get(entry, :agent_provider),
      adapter_type: Map.get(entry, :agent_harness),
      session_id: Map.get(entry, :session_id),
      workspace_path: normalized_workspace_path(entry),
      latest_event: latest_event(entry),
      latest_message: latest_message(entry),
      error_state: runtime_error_state(entry, error_message),
      error_message: error_message
    }
  end

  defp normalized_workspace_path(entry) do
    case Map.get(entry, :workspace_path) do
      path when is_binary(path) and path != "" ->
        path

      _ ->
        case Map.get(entry, :identifier) do
          identifier when is_binary(identifier) and identifier != "" ->
            workspace_path(identifier, entry, nil)

          _ ->
            nil
        end
    end
  end

  defp latest_event(entry) do
    runtime_event = Map.get(entry, :last_runtime_event)

    runtime_event
    |> runtime_event_value(:event)
    |> event_name()
    |> case do
      nil -> event_name(Map.get(entry, :last_codex_event))
      value -> value
    end
  end

  defp latest_message(entry) do
    runtime_event_message(Map.get(entry, :last_runtime_event)) ||
      summarize_message(Map.get(entry, :last_codex_message))
  end

  defp runtime_event_message(%{} = runtime_event) do
    payload = runtime_event_value(runtime_event, :payload)

    payload_text(payload, [
      :text,
      "text",
      :line,
      "line",
      :delta,
      "delta",
      :response,
      "response",
      :result,
      "result",
      :message,
      "message"
    ]) ||
      runtime_error_reason(payload) ||
      runtime_event_fallback_message(runtime_event, payload)
  end

  defp runtime_event_message(_runtime_event), do: nil

  defp runtime_event_fallback_message(runtime_event, payload) do
    case event_name(runtime_event_value(runtime_event, :event)) do
      "session_started" -> session_started_message(payload)
      "turn_started" -> turn_started_message(payload)
      event -> runtime_event_default_message(event)
    end
  end

  defp session_started_message(payload) do
    case payload_text(payload, [:session_id, "session_id"]) do
      nil -> "session started"
      session_id -> "session started (#{session_id})"
    end
  end

  defp turn_started_message(payload) do
    case payload_text(payload, [:command, "command"]) do
      nil -> "turn started"
      command -> "turn started (#{command})"
    end
  end

  defp runtime_event_default_message("turn_completed"), do: "turn completed"
  defp runtime_event_default_message(nil), do: nil
  defp runtime_event_default_message(event), do: event

  defp runtime_error_state(_entry, error_message) when is_binary(error_message), do: "error"

  defp runtime_error_state(entry, _error_message) do
    event = latest_event(entry)

    cond do
      event in ["turn_cancelled", "turn_canceled"] ->
        "cancelled"

      event in ["turn_failed", "turn_ended_with_error", "startup_failed", "tool_call_failed", "malformed"] ->
        "error"

      true ->
        "ok"
    end
  end

  defp runtime_error_message(entry) do
    runtime_event = Map.get(entry, :last_runtime_event)
    payload = runtime_event_value(runtime_event, :payload)
    event = latest_event(entry)

    cond do
      is_binary(runtime_error_reason(payload)) ->
        runtime_error_reason(payload)

      event in ["turn_failed", "turn_ended_with_error", "startup_failed", "tool_call_failed", "malformed"] ->
        latest_message(entry)

      true ->
        nil
    end
  end

  defp runtime_error_reason(%{} = payload) do
    payload_text(payload, [
      :reason,
      "reason",
      :error_message,
      "error_message",
      :message,
      "message"
    ]) ||
      payload_error_text(Map.get(payload, :error) || Map.get(payload, "error"))
  end

  defp runtime_error_reason(_payload), do: nil

  defp payload_error_text(error) when is_binary(error), do: error

  defp payload_error_text(%{} = error) do
    payload_text(error, [:message, "message", :reason, "reason", :code, "code"]) ||
      inspect(error, pretty: true, limit: 20)
  end

  defp payload_error_text(_error), do: nil

  defp payload_text(%{} = payload, fields) when is_list(fields) do
    Enum.find_value(fields, fn field ->
      case Map.get(payload, field) do
        value when is_binary(value) and value != "" -> value
        value when is_atom(value) and not is_nil(value) -> Atom.to_string(value)
        value when is_integer(value) -> Integer.to_string(value)
        value when is_float(value) -> Float.to_string(value)
        _ -> nil
      end
    end)
  end

  defp payload_text(_payload, _fields), do: nil

  defp runtime_event_value(%{} = runtime_event, key) when is_atom(key) do
    Map.get(runtime_event, key) || Map.get(runtime_event, Atom.to_string(key))
  end

  defp runtime_event_value(_runtime_event, _key), do: nil

  defp event_name(nil), do: nil
  defp event_name(value) when is_binary(value), do: value
  defp event_name(value) when is_atom(value), do: Atom.to_string(value)
  defp event_name(value), do: to_string(value)

  defp workspace_path(issue_identifier, running, retry) do
    (running && Map.get(running, :workspace_path)) ||
      (retry && Map.get(retry, :workspace_path)) ||
      Path.join(Config.settings!().workspace.root, issue_identifier)
  end

  defp workspace_host(running, retry) do
    (running && Map.get(running, :worker_host)) || (retry && Map.get(retry, :worker_host))
  end

  defp recent_events_payload(running) do
    [
      %{
        at: iso8601(running.last_codex_timestamp),
        event: running.last_codex_event,
        message: summarize_message(running.last_codex_message)
      }
    ]
    |> Enum.reject(&is_nil(&1.at))
  end

  defp summarize_message(nil), do: nil
  defp summarize_message(message), do: StatusDashboard.humanize_codex_message(message)

  defp json_safe(nil), do: nil
  defp json_safe(%DateTime{} = datetime), do: iso8601(datetime)
  defp json_safe(value) when is_binary(value) or is_number(value) or is_boolean(value), do: value
  defp json_safe(value) when is_atom(value), do: Atom.to_string(value)
  defp json_safe(value) when is_list(value), do: Enum.map(value, &json_safe/1)

  defp json_safe(value) when is_tuple(value), do: inspect(value)

  defp json_safe(%{} = value) do
    Map.new(value, fn {key, map_value} -> {to_string(key), json_safe(map_value)} end)
  end

  defp json_safe(value), do: inspect(value)

  defp due_at_iso8601(due_in_ms) when is_integer(due_in_ms) do
    DateTime.utc_now()
    |> DateTime.add(div(due_in_ms, 1_000), :second)
    |> DateTime.truncate(:second)
    |> DateTime.to_iso8601()
  end

  defp due_at_iso8601(_due_in_ms), do: nil

  defp iso8601(%DateTime{} = datetime) do
    datetime
    |> DateTime.truncate(:second)
    |> DateTime.to_iso8601()
  end

  defp iso8601(_datetime), do: nil
end
