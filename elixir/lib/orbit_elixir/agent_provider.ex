defmodule OrbitElixir.AgentProvider do
  @moduledoc """
  Selects the best configured agent harness for an issue.
  """

  alias OrbitElixir.Config
  alias OrbitElixir.Config.Schema
  alias OrbitElixir.Linear.Issue

  @type provider :: map()

  @ui_ux_keywords ~w[
    accessibility animation browser component css design figma frontend html interface layout mobile
    polish responsive screenshot styling tailwind ui ux visual viewport wireframe
  ]
  @claude_keywords ~w[
    analysis architecture audit docs documentation explain plan planning research review spec strategy
  ]

  @default_routes [
    %{
      "provider" => "gemini",
      "labels" => ~w[ui ux design frontend],
      "keywords" => @ui_ux_keywords
    },
    %{
      "provider" => "claude",
      "labels" => ~w[analysis architecture docs documentation research review],
      "keywords" => @claude_keywords
    }
  ]

  @spec select(Issue.t() | map(), Schema.t() | nil) ::
          {:ok, provider(), String.t()} | {:error, term()}
  def select(issue, settings \\ nil) do
    settings = settings || Config.settings!()

    with {:ok, providers} <- providers(settings),
         {:ok, provider_name, reason} <- selected_provider_name(issue, settings, providers),
         {:ok, provider} <- fetch_provider(providers, provider_name) do
      {:ok, provider, reason}
    end
  end

  @spec validate(Schema.t()) :: :ok | {:error, String.t()}
  def validate(%Schema{} = settings) do
    with {:ok, providers} <- providers(settings),
         :ok <- validate_provider_name(settings.agent.default_provider, providers, "agent.default_provider"),
         :ok <- validate_routes(settings.agent.provider_routes, providers) do
      :ok
    else
      {:error, {:invalid_agent_provider_config, message}} -> {:error, message}
      {:error, {:unknown_agent_provider, provider, source}} -> {:error, "#{source} references unknown provider #{inspect(provider)}"}
    end
  end

  @spec providers(Schema.t() | nil) :: {:ok, %{String.t() => provider()}} | {:error, term()}
  def providers(settings \\ nil) do
    settings = settings || Config.settings!()
    builtins = builtin_providers(settings)

    Enum.reduce_while(settings.providers || %{}, {:ok, builtins}, fn {raw_name, raw_provider}, {:ok, acc} ->
      name = normalize_provider_name(raw_name)

      case normalize_provider(name, raw_provider, Map.get(builtins, name), settings) do
        {:ok, provider} -> {:cont, {:ok, Map.put(acc, name, provider)}}
        {:error, reason} -> {:halt, {:error, reason}}
      end
    end)
  end

  defp builtin_providers(settings) do
    timeout_ms = settings.codex.turn_timeout_ms

    %{
      "codex" => %{
        "name" => "codex",
        "display_name" => "Codex",
        "harness" => "codex_app_server",
        "command" => settings.codex.command,
        "timeout_ms" => timeout_ms,
        "model" => nil
      },
      "claude" => %{
        "name" => "claude",
        "display_name" => "Claude",
        "harness" => "claude_agent_sdk",
        "command" => "claude -p --permission-mode acceptEdits --model \"$ORBIT_AGENT_MODEL\" \"$ORBIT_AGENT_PROMPT\"",
        "timeout_ms" => timeout_ms,
        "model" => "sonnet"
      },
      "gemini" => %{
        "name" => "gemini",
        "display_name" => "Gemini",
        "harness" => "cli",
        "command" => "gemini --approval-mode=auto_edit --model \"$ORBIT_AGENT_MODEL\" -p \"$ORBIT_AGENT_PROMPT\"",
        "timeout_ms" => timeout_ms,
        "model" => "auto"
      }
    }
  end

  defp normalize_provider(_name, raw_provider, _default_provider, _settings) when not is_map(raw_provider) do
    {:error, {:invalid_agent_provider_config, "providers entries must be maps"}}
  end

  defp normalize_provider(name, raw_provider, default_provider, settings) do
    base =
      default_provider ||
        %{
          "name" => name,
          "display_name" => humanize_provider_name(name),
          "harness" => "cli",
          "command" => nil,
          "timeout_ms" => settings.codex.turn_timeout_ms,
          "model" => nil
        }

    provider =
      raw_provider
      |> normalize_keys()
      |> Enum.reduce(base, fn {key, value}, acc -> Map.put(acc, key, value) end)
      |> Map.put("name", name)
      |> Map.put_new("display_name", humanize_provider_name(name))
      |> Map.put_new("timeout_ms", settings.codex.turn_timeout_ms)

    validate_provider(provider)
  end

  defp validate_provider(%{"name" => name, "harness" => harness, "command" => command, "timeout_ms" => timeout_ms} = provider) do
    cond do
      not valid_provider_name?(name) ->
        {:error, {:invalid_agent_provider_config, "provider names must be non-empty strings"}}

      harness not in ["codex_app_server", "cli", "claude_agent_sdk"] ->
        {:error, {:invalid_agent_provider_config, "providers.#{name}.harness must be codex_app_server, cli, or claude_agent_sdk"}}

      not is_binary(command) or command == "" ->
        {:error, {:invalid_agent_provider_config, "providers.#{name}.command can't be blank"}}

      not is_integer(timeout_ms) or timeout_ms <= 0 ->
        {:error, {:invalid_agent_provider_config, "providers.#{name}.timeout_ms must be a positive integer"}}

      true ->
        {:ok, provider}
    end
  end

  defp selected_provider_name(issue, settings, providers) do
    routes = settings.agent.provider_routes ++ @default_routes

    case Enum.find_value(routes, &matching_route(&1, issue, providers)) do
      {provider_name, reason} ->
        {:ok, provider_name, reason}

      nil ->
        provider_name = normalize_provider_name(settings.agent.default_provider)
        {:ok, provider_name, "default provider #{provider_name}"}
    end
  end

  defp matching_route(route, issue, providers) when is_map(route) do
    provider_name = normalize_provider_name(Map.get(route, "provider"))

    cond do
      not Map.has_key?(providers, provider_name) ->
        nil

      matching_label?(route, issue) ->
        {provider_name, "matched #{provider_name} provider label route"}

      matching_keyword?(route, issue) ->
        {provider_name, "matched #{provider_name} provider keyword route"}

      true ->
        nil
    end
  end

  defp matching_route(_route, _issue, _providers), do: nil

  defp matching_label?(route, issue) do
    route_labels = route |> Map.get("labels", []) |> normalize_list() |> MapSet.new()

    issue
    |> issue_labels()
    |> Enum.any?(fn label -> MapSet.member?(route_labels, normalize_match_token(label)) end)
  end

  defp matching_keyword?(route, issue) do
    keywords = route |> Map.get("keywords", []) |> normalize_list()
    text = issue_text(issue)

    Enum.any?(keywords, &text_matches_keyword?(text, &1))
  end

  defp text_matches_keyword?(_text, ""), do: false

  defp text_matches_keyword?(text, keyword) do
    normalized_keyword = normalize_match_token(keyword)

    if String.length(normalized_keyword) <= 3 do
      Regex.match?(~r/(^|[^a-z0-9])#{Regex.escape(normalized_keyword)}([^a-z0-9]|$)/, text)
    else
      String.contains?(text, normalized_keyword)
    end
  end

  defp validate_routes(routes, providers) when is_list(routes) do
    Enum.reduce_while(routes, :ok, fn route, :ok ->
      case validate_route(route, providers) do
        :ok -> {:cont, :ok}
        {:error, reason} -> {:halt, {:error, reason}}
      end
    end)
  end

  defp validate_routes(_routes, _providers) do
    {:error, {:invalid_agent_provider_config, "agent.provider_routes must be a list of maps"}}
  end

  defp validate_route(route, providers) when is_map(route) do
    route
    |> Map.get("provider")
    |> normalize_provider_name()
    |> validate_provider_name(providers, "agent.provider_routes")
  end

  defp validate_route(_route, _providers) do
    {:error, {:invalid_agent_provider_config, "agent.provider_routes entries must be maps"}}
  end

  defp validate_provider_name(provider_name, providers, source) do
    normalized_name = normalize_provider_name(provider_name)

    cond do
      not valid_provider_name?(normalized_name) ->
        {:error, {:invalid_agent_provider_config, "#{source} must be a non-empty string"}}

      not Map.has_key?(providers, normalized_name) ->
        {:error, {:unknown_agent_provider, normalized_name, source}}

      true ->
        :ok
    end
  end

  defp fetch_provider(providers, provider_name) do
    normalized_name = normalize_provider_name(provider_name)

    case Map.fetch(providers, normalized_name) do
      {:ok, provider} -> {:ok, provider}
      :error -> {:error, {:unknown_agent_provider, normalized_name, "agent routing"}}
    end
  end

  defp issue_labels(%Issue{labels: labels}) when is_list(labels), do: labels
  defp issue_labels(%{labels: labels}) when is_list(labels), do: labels
  defp issue_labels(_issue), do: []

  defp issue_text(issue) do
    [
      Map.get(issue, :identifier),
      Map.get(issue, :title),
      Map.get(issue, :description),
      issue |> issue_labels() |> Enum.join(" ")
    ]
    |> Enum.reject(&is_nil/1)
    |> Enum.join(" ")
    |> normalize_match_text()
  end

  defp normalize_list(values) when is_list(values) do
    values
    |> Enum.map(&normalize_match_token/1)
    |> Enum.reject(&(&1 == ""))
  end

  defp normalize_list(value) when is_binary(value), do: normalize_list([value])
  defp normalize_list(_value), do: []

  defp normalize_match_text(value) do
    value
    |> to_string()
    |> String.downcase()
  end

  defp normalize_match_token(value) do
    value
    |> normalize_match_text()
    |> String.trim()
  end

  defp normalize_provider_name(value) when is_atom(value), do: value |> Atom.to_string() |> normalize_provider_name()

  defp normalize_provider_name(value) do
    value
    |> to_string()
    |> String.trim()
    |> String.downcase()
  end

  defp valid_provider_name?(name), do: is_binary(name) and name != ""

  defp humanize_provider_name(name) do
    name
    |> String.replace(~r/[_-]+/, " ")
    |> String.split(" ", trim: true)
    |> Enum.map_join(" ", &String.capitalize/1)
  end

  defp normalize_keys(value) when is_map(value) do
    Enum.reduce(value, %{}, fn {key, raw_value}, normalized ->
      Map.put(normalized, to_string(key), raw_value)
    end)
  end
end
