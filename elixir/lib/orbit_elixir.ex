defmodule OrbitElixir do
  @moduledoc """
  Entry point for the Orbit orchestrator.
  """

  @doc """
  Start the orchestrator in the current BEAM node.
  """
  @spec start_link(keyword()) :: GenServer.on_start()
  def start_link(opts \\ []) do
    OrbitElixir.Orchestrator.start_link(opts)
  end
end

defmodule OrbitElixir.Application do
  @moduledoc """
  OTP application entrypoint that starts core supervisors and workers.
  """

  use Application

  @impl true
  def start(_type, _args) do
    :ok = OrbitElixir.LogFile.configure()

    children = [
      {Phoenix.PubSub, name: OrbitElixir.PubSub},
      {Task.Supervisor, name: OrbitElixir.TaskSupervisor},
      OrbitElixir.WorkflowStore,
      OrbitElixir.Orchestrator,
      OrbitElixir.HttpServer,
      OrbitElixir.StatusDashboard
    ]

    Supervisor.start_link(
      children,
      strategy: :one_for_one,
      name: OrbitElixir.Supervisor
    )
  end

  @impl true
  def stop(_state) do
    OrbitElixir.StatusDashboard.render_offline_status()
    :ok
  end
end
