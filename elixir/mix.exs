defmodule OrbitElixir.MixProject do
  use Mix.Project

  def project do
    [
      app: :orbit_elixir,
      version: "0.1.0",
      elixir: "~> 1.19",
      compilers: [:phoenix_live_view] ++ Mix.compilers(),
      start_permanent: Mix.env() == :prod,
      test_coverage: [
        summary: [
          threshold: 100
        ],
        ignore_modules: [
          OrbitElixir.Config,
          OrbitElixir.AgentProvider,
          OrbitElixir.AgentHarness.CLI,
          OrbitElixir.Linear.Client,
          OrbitElixir.SpecsCheck,
          OrbitElixir.Orchestrator,
          OrbitElixir.Orchestrator.State,
          OrbitElixir.AgentRunner,
          OrbitElixir.CLI,
          OrbitElixir.Codex.AppServer,
          OrbitElixir.Codex.DynamicTool,
          OrbitElixir.HttpServer,
          OrbitElixir.StatusDashboard,
          OrbitElixir.LogFile,
          OrbitElixir.Workspace,
          OrbitElixirWeb.DashboardLive,
          OrbitElixirWeb.Endpoint,
          OrbitElixirWeb.ErrorHTML,
          OrbitElixirWeb.ErrorJSON,
          OrbitElixirWeb.Layouts,
          OrbitElixirWeb.ObservabilityApiController,
          OrbitElixirWeb.Presenter,
          OrbitElixirWeb.StaticAssetController,
          OrbitElixirWeb.StaticAssets,
          OrbitElixirWeb.Router,
          OrbitElixirWeb.Router.Helpers
        ]
      ],
      test_ignore_filters: [
        "test/support/snapshot_support.exs",
        "test/support/test_support.exs"
      ],
      dialyzer: [
        plt_add_apps: [:mix]
      ],
      escript: escript(),
      aliases: aliases(),
      deps: deps()
    ]
  end

  # Run "mix help compile.app" to learn about applications.
  def application do
    [
      mod: {OrbitElixir.Application, []},
      extra_applications: [:logger]
    ]
  end

  # Run "mix help deps" to learn about dependencies.
  defp deps do
    [
      {:bandit, "~> 1.8"},
      {:floki, ">= 0.30.0", only: :test},
      {:lazy_html, ">= 0.1.0", only: :test},
      {:phoenix, "~> 1.8.0"},
      {:phoenix_html, "~> 4.2"},
      {:phoenix_live_view, "~> 1.1.0"},
      {:req, "~> 0.5"},
      {:jason, "~> 1.4"},
      {:yaml_elixir, "~> 2.12"},
      {:solid, "~> 1.2"},
      {:ecto, "~> 3.13"},
      {:credo, "~> 1.7", only: [:dev, :test], runtime: false},
      {:dialyxir, "~> 1.4", only: [:dev], runtime: false}
    ]
  end

  defp aliases do
    [
      setup: ["deps.get"],
      build: ["escript.build"],
      lint: ["specs.check", "credo --strict"]
    ]
  end

  defp escript do
    [
      app: nil,
      main_module: OrbitElixir.CLI,
      name: "orbit",
      path: "bin/orbit"
    ]
  end
end
