import Config

config :phoenix, :json_library, Jason

config :orbit_elixir, OrbitElixirWeb.Endpoint,
  adapter: Bandit.PhoenixAdapter,
  url: [host: "localhost"],
  render_errors: [
    formats: [html: OrbitElixirWeb.ErrorHTML, json: OrbitElixirWeb.ErrorJSON],
    layout: false
  ],
  pubsub_server: OrbitElixir.PubSub,
  live_view: [signing_salt: "orbit-live-view"],
  secret_key_base: String.duplicate("s", 64),
  check_origin: false,
  server: false
