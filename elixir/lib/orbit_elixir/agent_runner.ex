defmodule OrbitElixir.AgentRunner do
  @moduledoc """
  Executes a single Linear issue in its workspace with the selected agent provider.
  """

  require Logger
  alias OrbitElixir.AgentProvider
  alias OrbitElixir.AgentRuntime.ClaudeAgentSDK, as: ClaudeRuntime
  alias OrbitElixir.AgentRuntime.CLIAdapter, as: AgentCLI
  alias OrbitElixir.AgentRuntime.CodexAppServer, as: CodexRuntime
  alias OrbitElixir.{Config, Linear.Issue, PromptBuilder, Tracker, Workspace}

  @type worker_host :: String.t() | nil

  @spec run(map(), pid() | nil, keyword()) :: :ok | no_return()
  def run(issue, codex_update_recipient \\ nil, opts \\ []) do
    # The orchestrator owns host retries so one worker lifetime never hops machines.
    worker_host = selected_worker_host(Keyword.get(opts, :worker_host), Config.settings!().worker.ssh_hosts)

    Logger.info("Starting agent run for #{issue_context(issue)} worker_host=#{worker_host_for_log(worker_host)}")

    case run_on_worker_host(issue, codex_update_recipient, opts, worker_host) do
      :ok ->
        :ok

      {:error, reason} ->
        Logger.error("Agent run failed for #{issue_context(issue)}: #{inspect(reason)}")
        raise RuntimeError, "Agent run failed for #{issue_context(issue)}: #{inspect(reason)}"
    end
  end

  defp run_on_worker_host(issue, codex_update_recipient, opts, worker_host) do
    Logger.info("Starting worker attempt for #{issue_context(issue)} worker_host=#{worker_host_for_log(worker_host)}")

    case Workspace.create_for_issue(issue, worker_host) do
      {:ok, workspace} ->
        send_worker_runtime_info(codex_update_recipient, issue, worker_host, workspace)

        try do
          with :ok <- Workspace.run_before_run_hook(workspace, issue, worker_host) do
            run_agent_turns(workspace, issue, codex_update_recipient, opts, worker_host)
          end
        after
          Workspace.run_after_run_hook(workspace, issue, worker_host)
        end

      {:error, reason} ->
        {:error, reason}
    end
  end

  defp codex_message_handler(recipient, issue) do
    fn message ->
      send_codex_update(recipient, issue, message)
    end
  end

  defp send_codex_update(recipient, %Issue{id: issue_id}, message)
       when is_binary(issue_id) and is_pid(recipient) do
    send(recipient, {:codex_worker_update, issue_id, message})
    :ok
  end

  defp send_codex_update(_recipient, _issue, _message), do: :ok

  defp send_worker_runtime_info(recipient, %Issue{id: issue_id}, worker_host, workspace)
       when is_binary(issue_id) and is_pid(recipient) and is_binary(workspace) do
    send(
      recipient,
      {:worker_runtime_info, issue_id,
       %{
         worker_host: worker_host,
         workspace_path: workspace
       }}
    )

    :ok
  end

  defp send_worker_runtime_info(_recipient, _issue, _worker_host, _workspace), do: :ok

  defp run_agent_turns(workspace, issue, codex_update_recipient, opts, worker_host) do
    with {:ok, provider, reason} <- AgentProvider.select(issue) do
      Logger.info("Selected agent provider for #{issue_context(issue)} provider=#{provider["name"]} harness=#{provider["harness"]} reason=#{reason}")

      case provider["harness"] do
        "codex_app_server" ->
          run_codex_turns(provider, workspace, issue, codex_update_recipient, opts, worker_host)

        "cli" ->
          run_cli_turns(provider, workspace, issue, codex_update_recipient, opts, worker_host)

        "claude_agent_sdk" ->
          run_claude_sdk_turns(provider, workspace, issue, codex_update_recipient, opts, worker_host)

        harness ->
          {:error, {:unsupported_agent_harness, harness}}
      end
    end
  end

  defp run_codex_turns(provider, workspace, issue, codex_update_recipient, opts, worker_host) do
    max_turns = Keyword.get(opts, :max_turns, Config.settings!().agent.max_turns)
    issue_state_fetcher = Keyword.get(opts, :issue_state_fetcher, &Tracker.fetch_issue_states_by_ids/1)
    context = turn_context(workspace, issue, codex_update_recipient, opts, issue_state_fetcher, worker_host)

    with {:ok, session} <-
           CodexRuntime.start_session(%{
             workspace: workspace,
             provider: provider["name"],
             harness: provider["harness"],
             model: provider["model"],
             config: %{
               provider: provider,
               worker_host: worker_host
             }
           }) do
      try do
        do_run_codex_turns(session, provider, context, 1, max_turns)
      after
        CodexRuntime.stop_session(session)
      end
    end
  end

  defp do_run_codex_turns(app_session, provider, context, turn_number, max_turns) do
    prompt = build_turn_prompt(context.issue, context.opts, turn_number, max_turns, provider)

    with {:ok, turn} <-
           CodexRuntime.send_turn(app_session, %{
             prompt: prompt,
             issue: context.issue
           }),
         {:ok, turn_summary} <-
           CodexRuntime.stream_events(
             app_session,
             turn,
             codex_message_handler(context.codex_update_recipient, context.issue)
           ) do
      turn_session = Map.get(turn_summary, :provider_result, %{})

      Logger.info("Completed agent run for #{issue_context(context.issue)} session_id=#{turn_session[:session_id]} workspace=#{context.workspace} turn=#{turn_number}/#{max_turns}")

      case continue_with_issue?(context.issue, context.issue_state_fetcher) do
        {:continue, refreshed_issue} when turn_number < max_turns ->
          Logger.info("Continuing agent run for #{issue_context(refreshed_issue)} after normal turn completion turn=#{turn_number}/#{max_turns}")

          do_run_codex_turns(
            app_session,
            provider,
            %{context | issue: refreshed_issue},
            turn_number + 1,
            max_turns
          )

        {:continue, refreshed_issue} ->
          Logger.info("Reached agent.max_turns for #{issue_context(refreshed_issue)} with issue still active; returning control to orchestrator")

          :ok

        {:done, _refreshed_issue} ->
          :ok

        {:error, reason} ->
          {:error, reason}
      end
    end
  end

  defp run_cli_turns(provider, workspace, issue, codex_update_recipient, opts, worker_host) do
    max_turns = Keyword.get(opts, :max_turns, Config.settings!().agent.max_turns)
    issue_state_fetcher = Keyword.get(opts, :issue_state_fetcher, &Tracker.fetch_issue_states_by_ids/1)
    context = turn_context(workspace, issue, codex_update_recipient, opts, issue_state_fetcher, worker_host)

    do_run_cli_turns(provider, context, 1, max_turns)
  end

  defp do_run_cli_turns(provider, context, turn_number, max_turns) do
    prompt = build_turn_prompt(context.issue, context.opts, turn_number, max_turns, provider)

    with {:ok, turn_session} <-
           AgentCLI.run_turn(
             provider,
             context.workspace,
             prompt,
             context.issue,
             worker_host: context.worker_host,
             on_message: codex_message_handler(context.codex_update_recipient, context.issue)
           ) do
      Logger.info(
        "Completed agent run for #{issue_context(context.issue)} session_id=#{turn_session[:session_id]} workspace=#{context.workspace} provider=#{provider["name"]} turn=#{turn_number}/#{max_turns}"
      )

      case continue_with_issue?(context.issue, context.issue_state_fetcher) do
        {:continue, refreshed_issue} when turn_number < max_turns ->
          Logger.info("Continuing agent run for #{issue_context(refreshed_issue)} after normal turn completion provider=#{provider["name"]} turn=#{turn_number}/#{max_turns}")

          do_run_cli_turns(
            provider,
            %{context | issue: refreshed_issue},
            turn_number + 1,
            max_turns
          )

        {:continue, refreshed_issue} ->
          Logger.info("Reached agent.max_turns for #{issue_context(refreshed_issue)} with issue still active; returning control to orchestrator")

          :ok

        {:done, _refreshed_issue} ->
          :ok

        {:error, reason} ->
          {:error, reason}
      end
    end
  end

  defp run_claude_sdk_turns(provider, workspace, issue, codex_update_recipient, opts, worker_host) do
    max_turns = Keyword.get(opts, :max_turns, Config.settings!().agent.max_turns)
    issue_state_fetcher = Keyword.get(opts, :issue_state_fetcher, &Tracker.fetch_issue_states_by_ids/1)
    context = turn_context(workspace, issue, codex_update_recipient, opts, issue_state_fetcher, worker_host)

    case ClaudeRuntime.start_session(%{
           workspace: workspace,
           provider: provider["name"],
           harness: provider["harness"],
           model: provider["model"],
           config: %{
             provider: provider,
             worker_host: worker_host
           }
         }) do
      {:ok, session} ->
        try do
          do_run_claude_sdk_turns(session, provider, context, 1, max_turns)
        after
          ClaudeRuntime.stop_session(session)
        end

      {:error, reason} ->
        fallback_to_cli(provider, context, max_turns, {:start_session, reason})
    end
  end

  defp do_run_claude_sdk_turns(session, provider, context, turn_number, max_turns) do
    prompt = build_turn_prompt(context.issue, context.opts, turn_number, max_turns, provider)

    with {:ok, turn} <-
           ClaudeRuntime.send_turn(session, %{
             prompt: prompt,
             issue: context.issue
           }),
         {:ok, turn_summary} <-
           ClaudeRuntime.stream_events(
             session,
             turn,
             codex_message_handler(context.codex_update_recipient, context.issue)
           ) do
      turn_session = Map.get(turn_summary, :provider_result, %{})
      session_id = turn_session["session_id"] || turn_session[:session_id] || session.session_id

      Logger.info(
        "Completed agent run for #{issue_context(context.issue)} session_id=#{session_id} workspace=#{context.workspace} provider=#{provider["name"]} harness=#{provider["harness"]} turn=#{turn_number}/#{max_turns}"
      )

      case continue_with_issue?(context.issue, context.issue_state_fetcher) do
        {:continue, refreshed_issue} when turn_number < max_turns ->
          Logger.info(
            "Continuing agent run for #{issue_context(refreshed_issue)} after normal turn completion provider=#{provider["name"]} harness=#{provider["harness"]} turn=#{turn_number}/#{max_turns}"
          )

          do_run_claude_sdk_turns(
            session,
            provider,
            %{context | issue: refreshed_issue},
            turn_number + 1,
            max_turns
          )

        {:continue, refreshed_issue} ->
          Logger.info("Reached agent.max_turns for #{issue_context(refreshed_issue)} with issue still active; returning control to orchestrator")

          :ok

        {:done, _refreshed_issue} ->
          :ok

        {:error, reason} ->
          {:error, reason}
      end
    else
      {:error, {:turn_cancelled, _details} = reason} ->
        {:error, reason}

      {:error, :turn_cancelled = reason} ->
        {:error, reason}

      {:error, reason} ->
        fallback_to_cli(provider, context, max_turns, {:sdk_turn, reason}, turn_number)
    end
  end

  defp fallback_to_cli(provider, context, max_turns, sdk_reason, turn_number \\ 1) do
    cli_provider = Map.put(provider, "harness", "cli")

    Logger.warning("Falling back to Claude CLI provider for #{issue_context(context.issue)} reason=#{inspect(sdk_reason)}")

    do_run_cli_turns(cli_provider, context, turn_number, max_turns)
  end

  defp build_turn_prompt(issue, opts, 1, _max_turns, _provider), do: PromptBuilder.build_prompt(issue, opts)

  defp build_turn_prompt(_issue, _opts, turn_number, max_turns, provider) do
    """
    Continuation guidance:

    - The previous #{provider["display_name"] || provider["name"]} turn completed normally, but the Linear issue is still in an active state.
    - This is continuation turn ##{turn_number} of #{max_turns} for the current agent run.
    - Resume from the current workspace and workpad state instead of restarting from scratch.
    - Use the current files and Linear workpad as the source of truth for prior progress.
    - Focus on the remaining ticket work and do not end the turn while the issue stays active unless you are truly blocked.
    """
  end

  defp turn_context(workspace, issue, codex_update_recipient, opts, issue_state_fetcher, worker_host) do
    %{
      workspace: workspace,
      issue: issue,
      codex_update_recipient: codex_update_recipient,
      opts: opts,
      issue_state_fetcher: issue_state_fetcher,
      worker_host: worker_host
    }
  end

  defp continue_with_issue?(%Issue{id: issue_id} = issue, issue_state_fetcher) when is_binary(issue_id) do
    case issue_state_fetcher.([issue_id]) do
      {:ok, [%Issue{} = refreshed_issue | _]} ->
        if active_issue_state?(refreshed_issue.state) do
          {:continue, refreshed_issue}
        else
          {:done, refreshed_issue}
        end

      {:ok, []} ->
        {:done, issue}

      {:error, reason} ->
        {:error, {:issue_state_refresh_failed, reason}}
    end
  end

  defp continue_with_issue?(issue, _issue_state_fetcher), do: {:done, issue}

  defp active_issue_state?(state_name) when is_binary(state_name) do
    normalized_state = normalize_issue_state(state_name)

    Config.settings!().tracker.active_states
    |> Enum.any?(fn active_state -> normalize_issue_state(active_state) == normalized_state end)
  end

  defp active_issue_state?(_state_name), do: false

  defp selected_worker_host(nil, []), do: nil

  defp selected_worker_host(preferred_host, configured_hosts) when is_list(configured_hosts) do
    hosts =
      configured_hosts
      |> Enum.map(&String.trim/1)
      |> Enum.reject(&(&1 == ""))
      |> Enum.uniq()

    case preferred_host do
      host when is_binary(host) and host != "" -> host
      _ when hosts == [] -> nil
      _ -> List.first(hosts)
    end
  end

  defp worker_host_for_log(nil), do: "local"
  defp worker_host_for_log(worker_host), do: worker_host

  defp normalize_issue_state(state_name) when is_binary(state_name) do
    state_name
    |> String.trim()
    |> String.downcase()
  end

  defp issue_context(%Issue{id: issue_id, identifier: identifier}) do
    "issue_id=#{issue_id} issue_identifier=#{identifier}"
  end
end
