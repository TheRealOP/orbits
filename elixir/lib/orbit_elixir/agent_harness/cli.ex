defmodule OrbitElixir.AgentHarness.CLI do
  @moduledoc """
  Runs non-Codex providers through a configurable one-shot CLI command.
  """

  require Logger

  alias OrbitElixir.SSH

  @port_line_bytes 1_048_576

  @spec run_turn(map(), Path.t(), String.t(), map(), keyword()) :: {:ok, map()} | {:error, term()}
  def run_turn(provider, workspace, prompt, issue, opts \\ []) do
    worker_host = Keyword.get(opts, :worker_host)
    on_message = Keyword.get(opts, :on_message, &default_on_message/1)
    session_id = session_id(provider)
    metadata = metadata(provider, worker_host, session_id)

    with {:ok, port} <- start_port(provider, workspace, prompt, issue, worker_host) do
      emit_message(on_message, :session_started, metadata)
      Logger.info("CLI agent session started for #{issue_context(issue)} provider=#{provider["name"]} session_id=#{session_id}")

      case await_completion(port, provider, on_message, metadata, "") do
        {:ok, result} ->
          emit_message(on_message, :turn_completed, Map.put(metadata, :result, result))
          Logger.info("CLI agent session completed for #{issue_context(issue)} provider=#{provider["name"]} session_id=#{session_id}")
          {:ok, %{result: result, session_id: session_id, provider: provider["name"]}}

        {:error, reason} ->
          emit_message(on_message, :turn_ended_with_error, Map.merge(metadata, %{reason: reason}))
          Logger.warning("CLI agent session failed for #{issue_context(issue)} provider=#{provider["name"]} session_id=#{session_id}: #{inspect(reason)}")
          {:error, reason}
      end
    end
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

  defp await_completion(port, provider, on_message, metadata, pending_line) do
    receive do
      {^port, {:data, {:eol, chunk}}} ->
        line = pending_line <> to_string(chunk)
        emit_output_line(on_message, metadata, line)
        await_completion(port, provider, on_message, metadata, "")

      {^port, {:data, {:noeol, chunk}}} ->
        await_completion(port, provider, on_message, metadata, pending_line <> to_string(chunk))

      {^port, {:exit_status, 0}} ->
        if pending_line != "", do: emit_output_line(on_message, metadata, pending_line)
        {:ok, :turn_completed}

      {^port, {:exit_status, status}} ->
        if pending_line != "", do: emit_output_line(on_message, metadata, pending_line)
        {:error, {:provider_exit, provider["name"], status}}
    after
      provider["timeout_ms"] ->
        Port.close(port)
        {:error, :turn_timeout}
    end
  end

  defp emit_output_line(on_message, metadata, line) do
    emit_message(
      on_message,
      :notification,
      Map.merge(metadata, %{
        payload: %{
          "provider" => metadata.agent_provider,
          "stream" => "stdout",
          "line" => line
        },
        raw: line
      })
    )
  end

  defp emit_message(on_message, event, payload) do
    payload =
      payload
      |> Map.put(:event, event)
      |> Map.put(:timestamp, DateTime.utc_now())

    on_message.(payload)
  end

  defp metadata(provider, worker_host, session_id) do
    %{
      session_id: session_id,
      agent_provider: provider["name"],
      agent_harness: provider["harness"],
      agent_model: provider["model"]
    }
    |> maybe_put_worker_host(worker_host)
  end

  defp maybe_put_worker_host(metadata, worker_host) when is_binary(worker_host), do: Map.put(metadata, :worker_host, worker_host)
  defp maybe_put_worker_host(metadata, _worker_host), do: metadata

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

  defp default_on_message(_message), do: :ok

  defp shell_escape(value) when is_binary(value) do
    "'" <> String.replace(value, "'", "'\"'\"'") <> "'"
  end
end
