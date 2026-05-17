defmodule OrbitElixir.AgentRuntime.CodexAppServer do
  @moduledoc """
  Runtime adapter for Codex app-server sessions.

  The adapter keeps the existing Codex app-server client as the provider-native
  implementation while exposing it through the shared Orbit runtime lifecycle.
  """

  @behaviour OrbitElixir.AgentRuntime

  alias OrbitElixir.Codex.AppServer

  @impl true
  def start_session(%{workspace: workspace} = request) when is_binary(workspace) do
    provider = provider_config(request)
    worker_host = request_value(request, :worker_host) || config_value(request, :worker_host)

    opts =
      []
      |> maybe_put_opt(:worker_host, worker_host)
      |> maybe_put_opt(:command, Map.get(provider, "command"))
      |> Keyword.put(:agent_provider, provider)

    with {:ok, app_session} <- AppServer.start_session(workspace, opts) do
      {:ok,
       %{
         session_id: app_session.thread_id,
         provider: Map.get(provider, "name", "codex"),
         harness: Map.get(provider, "harness", "codex_app_server"),
         workspace: app_session.workspace,
         model: Map.get(provider, "model"),
         provider_session: app_session,
         worker_host: worker_host
       }}
    end
  end

  def start_session(_request), do: {:error, {:invalid_runtime_request, :workspace}}

  @impl true
  def send_turn(%{session_id: session_id}, %{prompt: prompt} = request) when is_binary(prompt) do
    {:ok,
     %{
       turn_id: pending_turn_id(),
       session_id: session_id,
       provider_turn: %{
         prompt: prompt,
         issue: request_value(request, :issue),
         opts: request_value(request, :opts) || []
       }
     }}
  end

  def send_turn(_session, _request), do: {:error, {:invalid_turn_request, :prompt}}

  @impl true
  def stream_events(%{provider_session: app_session}, %{provider_turn: provider_turn}, emit)
      when is_function(emit, 1) do
    opts =
      provider_turn
      |> Map.get(:opts, [])
      |> Keyword.put(:on_message, emit)

    case AppServer.run_turn(
           app_session,
           Map.fetch!(provider_turn, :prompt),
           Map.fetch!(provider_turn, :issue),
           opts
         ) do
      {:ok, result} -> {:ok, summarize_result(result)}
      {:error, reason} -> {:error, reason}
    end
  end

  def stream_events(_session, _turn, _emit), do: {:error, {:invalid_runtime_stream, :codex_app_server}}

  @impl true
  def stop_session(%{provider_session: app_session}), do: AppServer.stop_session(app_session)
  def stop_session(_session), do: :ok

  @impl true
  def read_diff(%{workspace: workspace, worker_host: nil}) when is_binary(workspace) do
    case System.cmd("git", ["-C", workspace, "diff", "--"], stderr_to_stdout: true) do
      {diff, 0} -> {:ok, diff}
      {output, status} -> {:error, {:git_diff_failed, status, output}}
    end
  end

  def read_diff(%{worker_host: worker_host}) when is_binary(worker_host), do: {:error, :remote_diff_unsupported}
  def read_diff(_session), do: {:error, {:invalid_runtime_session, :workspace}}

  @impl true
  def summarize_result(%{result: :turn_completed} = result) do
    %{status: :completed, provider_result: result}
  end

  def summarize_result({:error, reason}), do: %{status: :failed, message: inspect(reason), provider_result: {:error, reason}}
  def summarize_result(result), do: %{status: :completed, provider_result: result}

  defp provider_config(request) do
    case config_value(request, :provider) || request_value(request, :provider_config) do
      provider when is_map(provider) ->
        provider

      _ ->
        %{
          "name" => request_value(request, :provider) || "codex",
          "harness" => request_value(request, :harness) || "codex_app_server",
          "model" => request_value(request, :model)
        }
    end
  end

  defp request_value(request, key) when is_atom(key) do
    Map.get(request, key) || Map.get(request, Atom.to_string(key))
  end

  defp config_value(request, key) when is_atom(key) do
    case request_value(request, :config) do
      config when is_map(config) -> Map.get(config, key) || Map.get(config, Atom.to_string(key))
      _ -> nil
    end
  end

  defp maybe_put_opt(opts, _key, nil), do: opts
  defp maybe_put_opt(opts, _key, ""), do: opts
  defp maybe_put_opt(opts, key, value), do: Keyword.put(opts, key, value)

  defp pending_turn_id do
    "pending-codex-turn-#{System.unique_integer([:positive, :monotonic])}"
  end
end
