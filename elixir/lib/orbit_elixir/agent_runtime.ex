defmodule OrbitElixir.AgentRuntime do
  @moduledoc """
  Provider-neutral contract for agent runtime implementations.

  The runtime interface describes the lifecycle Orbit needs from any coding
  agent provider without requiring that provider to mimic the Codex app-server
  protocol. Codex can expose long-lived threads and turn ids, while Claude,
  Gemini, or another provider can expose a one-shot CLI process and still fit
  the same session/turn/event shape.

  ## Operations

  Runtime implementations expose these operations:

  - `c:start_session/1` prepares a provider session in a workspace.
  - `c:send_turn/2` starts one unit of work for a prompt.
  - `c:stream_events/3` streams provider updates as normalized runtime events.
  - `c:stop_session/1` terminates or releases provider resources.
  - `c:read_diff/1` returns the current workspace diff.
  - `c:summarize_result/1` converts provider-specific completion data into a
    stable Orbit result summary.

  ## Normalized Events

  Every normalized event has a provider-neutral `:event` value, a timestamp,
  a `:session_id`, provider metadata, and a map payload. Provider-specific
  details remain representable through `:payload`, `:raw`, and `:source_event`.
  `:turn_id` is optional because not every provider exposes a separate turn
  identity.

  The normalized event types are:

  - `:session_started`
  - `:turn_started`
  - `:output_delta`
  - `:tool_event`
  - `:diff_changed`
  - `:approval_needed`
  - `:turn_completed`
  - `:turn_failed`

  Existing Codex app-server and CLI harness messages can be decorated with
  `:runtime_event` via `attach_runtime_event/1` while preserving their legacy
  `:event` values for current dashboard/orchestrator consumers.
  """

  @operations [:start_session, :send_turn, :stream_events, :stop_session, :read_diff, :summarize_result]
  @event_types [
    :session_started,
    :turn_started,
    :output_delta,
    :tool_event,
    :diff_changed,
    :approval_needed,
    :turn_completed,
    :turn_failed
  ]
  @source_event_types %{
    :session_started => :session_started,
    "session_started" => :session_started,
    :turn_started => :turn_started,
    "turn_started" => :turn_started,
    :output_delta => :output_delta,
    "output_delta" => :output_delta,
    :diff_changed => :diff_changed,
    "diff_changed" => :diff_changed,
    :turn_completed => :turn_completed,
    "turn_completed" => :turn_completed,
    :turn_failed => :turn_failed,
    "turn_failed" => :turn_failed,
    :turn_cancelled => :turn_failed,
    "turn_cancelled" => :turn_failed,
    :turn_ended_with_error => :turn_failed,
    "turn_ended_with_error" => :turn_failed,
    :startup_failed => :turn_failed,
    "startup_failed" => :turn_failed,
    :turn_input_required => :turn_failed,
    "turn_input_required" => :turn_failed,
    :approval_required => :approval_needed,
    "approval_required" => :approval_needed,
    :approval_needed => :approval_needed,
    "approval_needed" => :approval_needed,
    :tool_event => :tool_event,
    "tool_event" => :tool_event,
    :approval_auto_approved => :tool_event,
    "approval_auto_approved" => :tool_event,
    :tool_input_auto_answered => :tool_event,
    "tool_input_auto_answered" => :tool_event,
    :tool_call_completed => :tool_event,
    "tool_call_completed" => :tool_event,
    :tool_call_failed => :tool_event,
    "tool_call_failed" => :tool_event,
    :unsupported_tool_call => :tool_event,
    "unsupported_tool_call" => :tool_event,
    :notification => :notification,
    "notification" => :notification
  }

  @typedoc "Provider runtime operation names Orbit expects."
  @type operation ::
          :start_session
          | :send_turn
          | :stream_events
          | :stop_session
          | :read_diff
          | :summarize_result

  @typedoc "Provider-neutral runtime event types."
  @type event_type ::
          :session_started
          | :turn_started
          | :output_delta
          | :tool_event
          | :diff_changed
          | :approval_needed
          | :turn_completed
          | :turn_failed

  @typedoc "Request used to prepare a provider session."
  @type start_session_request :: %{
          required(:workspace) => Path.t(),
          optional(:provider) => String.t(),
          optional(:harness) => String.t(),
          optional(:issue) => map(),
          optional(:config) => map()
        }

  @typedoc "Provider session descriptor. Provider-specific state belongs under `:provider_session`."
  @type session :: %{
          required(:session_id) => String.t(),
          required(:provider) => String.t(),
          required(:harness) => String.t(),
          required(:workspace) => Path.t(),
          optional(:model) => String.t() | nil,
          optional(:provider_session) => term()
        }

  @typedoc "Request used to start a provider turn."
  @type turn_request :: %{
          required(:prompt) => String.t(),
          optional(:issue) => map(),
          optional(:metadata) => map()
        }

  @typedoc "Provider turn descriptor. `:provider_turn` carries protocol-specific identity if present."
  @type turn :: %{
          required(:turn_id) => String.t(),
          required(:session_id) => String.t(),
          optional(:provider_turn) => term()
        }

  @typedoc "Normalized runtime event emitted upstream to Orbit."
  @type runtime_event :: %{
          required(:event) => event_type(),
          required(:timestamp) => DateTime.t() | String.t(),
          required(:session_id) => String.t(),
          required(:provider) => String.t(),
          required(:harness) => String.t(),
          required(:payload) => map(),
          optional(:turn_id) => String.t(),
          optional(:model) => String.t() | nil,
          optional(:source_event) => atom() | String.t(),
          optional(:raw) => term()
        }

  @typedoc "Provider-neutral result summary consumed by the runner/orchestrator."
  @type result_summary :: %{
          required(:status) => :completed | :failed | :cancelled,
          optional(:message) => String.t(),
          optional(:usage) => map(),
          optional(:provider_result) => term()
        }

  @callback start_session(start_session_request()) :: {:ok, session()} | {:error, term()}
  @callback send_turn(session(), turn_request()) :: {:ok, turn()} | {:error, term()}
  @callback stream_events(session(), turn(), (runtime_event() -> term())) ::
              {:ok, result_summary()} | {:error, term()}
  @callback stop_session(session()) :: :ok | {:error, term()}
  @callback read_diff(session()) :: {:ok, String.t()} | {:error, term()}
  @callback summarize_result(term()) :: result_summary()

  @spec operations() :: [operation()]
  def operations, do: @operations

  @spec event_types() :: [event_type()]
  def event_types, do: @event_types

  @spec validate_event(map()) :: :ok | {:error, term()}
  def validate_event(event) when is_map(event) do
    with {:ok, _event_type} <- event_type_from(event),
         :ok <- require_binary(event, :session_id),
         :ok <- require_binary(event, :provider),
         :ok <- require_binary(event, :harness),
         :ok <- require_payload(event) do
      require_timestamp(event)
    end
  end

  def validate_event(_event), do: {:error, :event_must_be_map}

  @spec new_event(event_type(), map()) :: {:ok, runtime_event()} | {:error, term()}
  def new_event(event_type, attrs) when is_map(attrs) do
    with {:ok, normalized_type} <- normalize_event_type(event_type) do
      event =
        attrs
        |> normalize_contract_keys()
        |> Map.put(:event, normalized_type)
        |> Map.put_new(:timestamp, DateTime.utc_now())
        |> Map.put_new(:payload, %{})

      case validate_event(event) do
        :ok -> {:ok, event}
        {:error, reason} -> {:error, reason}
      end
    end
  end

  @spec normalize_message(map()) :: {:ok, runtime_event()} | {:error, term()}
  def normalize_message(message) when is_map(message) do
    with {:ok, event_type} <- runtime_event_type(message),
         {:ok, session_id} <- message_session_id(message),
         {:ok, provider} <- message_provider(message),
         {:ok, harness} <- message_harness(message) do
      attrs =
        %{
          timestamp: message_timestamp(message),
          session_id: session_id,
          provider: provider,
          harness: harness,
          payload: message_payload(message),
          source_event: message_value(message, :event)
        }
        |> maybe_put(:turn_id, message_string_value(message, :turn_id))
        |> maybe_put(:model, message_value(message, :agent_model))
        |> maybe_put(:raw, message_value(message, :raw))

      new_event(event_type, attrs)
    end
  end

  def normalize_message(_message), do: {:error, :message_must_be_map}

  @spec attach_runtime_event(map()) :: map()
  def attach_runtime_event(message) when is_map(message) do
    case normalize_message(message) do
      {:ok, runtime_event} -> Map.put(message, :runtime_event, runtime_event)
      {:error, _reason} -> message
    end
  end

  def attach_runtime_event(message), do: message

  defp event_type_from(event) do
    event
    |> message_value(:event)
    |> normalize_event_type()
  end

  defp normalize_event_type(event_type) when event_type in @event_types, do: {:ok, event_type}

  defp normalize_event_type(event_type) when is_binary(event_type) do
    case Enum.find(@event_types, &(Atom.to_string(&1) == event_type)) do
      nil -> {:error, {:unknown_event_type, event_type}}
      normalized -> {:ok, normalized}
    end
  end

  defp normalize_event_type(event_type), do: {:error, {:unknown_event_type, event_type}}

  defp require_binary(event, key) do
    case message_value(event, key) do
      value when is_binary(value) and value != "" -> :ok
      _ -> {:error, {:missing_or_invalid, key}}
    end
  end

  defp require_payload(event) do
    case message_value(event, :payload) do
      payload when is_map(payload) -> :ok
      _ -> {:error, {:missing_or_invalid, :payload}}
    end
  end

  defp require_timestamp(event) do
    case message_value(event, :timestamp) do
      %DateTime{} -> :ok
      timestamp when is_binary(timestamp) and timestamp != "" -> :ok
      _ -> {:error, {:missing_or_invalid, :timestamp}}
    end
  end

  defp normalize_contract_keys(attrs) do
    Enum.reduce(attrs, %{}, fn {key, value}, acc ->
      Map.put(acc, normalize_contract_key(key), value)
    end)
  end

  defp normalize_contract_key(key)
       when key in [
              "event",
              "timestamp",
              "session_id",
              "provider",
              "harness",
              "payload",
              "turn_id",
              "model",
              "source_event",
              "raw"
            ] do
    String.to_existing_atom(key)
  end

  defp normalize_contract_key(key), do: key

  defp runtime_event_type(message) do
    source_event = message_value(message, :event)
    payload = message_payload(message)

    case Map.fetch(@source_event_types, source_event) do
      {:ok, :notification} -> notification_runtime_event_type(payload)
      {:ok, event_type} -> {:ok, event_type}
      :error -> {:error, {:unsupported_source_event, source_event}}
    end
  end

  defp notification_runtime_event_type(payload) do
    method = Map.get(payload, "method") || Map.get(payload, :method)

    cond do
      diff_method?(method) -> {:ok, :diff_changed}
      tool_or_command_method?(method) -> {:ok, :tool_event}
      true -> {:ok, :output_delta}
    end
  end

  defp diff_method?(method) when is_binary(method), do: String.contains?(String.downcase(method), "diff")
  defp diff_method?(_method), do: false

  defp tool_or_command_method?(method) when is_binary(method) do
    normalized = String.downcase(method)

    Enum.any?(["approval", "command", "exec", "filechange", "patch", "tool"], fn marker ->
      String.contains?(normalized, marker)
    end)
  end

  defp tool_or_command_method?(_method), do: false

  defp message_session_id(message) do
    case message_string_value(message, :session_id) do
      nil -> {:error, {:missing_or_invalid, :session_id}}
      session_id -> {:ok, session_id}
    end
  end

  defp message_provider(message) do
    case message_string_value(message, :agent_provider) || message_string_value(message, :provider) do
      nil -> {:error, {:missing_or_invalid, :provider}}
      provider -> {:ok, provider}
    end
  end

  defp message_harness(message) do
    case message_string_value(message, :agent_harness) || message_string_value(message, :harness) do
      nil -> {:error, {:missing_or_invalid, :harness}}
      harness -> {:ok, harness}
    end
  end

  defp message_timestamp(message) do
    case message_value(message, :timestamp) do
      %DateTime{} = timestamp -> timestamp
      timestamp when is_binary(timestamp) and timestamp != "" -> timestamp
      _ -> DateTime.utc_now()
    end
  end

  defp message_payload(message) do
    cond do
      is_map(message_value(message, :payload)) ->
        message_value(message, :payload)

      not is_nil(message_value(message, :payload)) ->
        %{value: message_value(message, :payload)}

      not is_nil(message_value(message, :reason)) ->
        %{reason: message_value(message, :reason)}

      not is_nil(message_value(message, :result)) ->
        %{result: message_value(message, :result)}

      true ->
        %{}
    end
  end

  defp message_string_value(message, key) do
    case message_value(message, key) do
      nil -> nil
      value when is_binary(value) and value != "" -> value
      value when is_atom(value) -> Atom.to_string(value)
      value when is_integer(value) -> Integer.to_string(value)
      _ -> nil
    end
  end

  defp message_value(message, key) when is_map(message) and is_atom(key) do
    Map.get(message, key) || Map.get(message, Atom.to_string(key))
  end

  defp maybe_put(map, _key, nil), do: map
  defp maybe_put(map, key, value), do: Map.put(map, key, value)
end
