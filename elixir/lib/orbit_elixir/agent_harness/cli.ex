defmodule OrbitElixir.AgentHarness.CLI do
  @moduledoc """
  Compatibility wrapper for the CLI runtime adapter.
  """

  alias OrbitElixir.AgentRuntime.CLIAdapter

  @spec run_turn(map(), Path.t(), String.t(), map(), keyword()) :: {:ok, map()} | {:error, term()}
  defdelegate run_turn(provider, workspace, prompt, issue, opts \\ []), to: CLIAdapter
end
