import { createInterface } from "node:readline";

function emit(frame) {
  process.stdout.write(`${JSON.stringify(frame)}\n`);
}

function errorPayload(error, code) {
  return {
    code,
    name: error?.name ?? "Error",
    message: error?.message ?? String(error),
    stack: error?.stack,
  };
}

async function readRequest() {
  const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });

  const requestLine = await new Promise((resolve) => {
    rl.once("line", resolve);
  });

  return {
    request: JSON.parse(requestLine),
    lines: rl,
  };
}

function compactObject(value) {
  if (Array.isArray(value)) {
    return value.map(compactObject);
  }

  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([, entry]) => entry !== null && entry !== undefined)
        .map(([key, entry]) => [key, compactObject(entry)]),
    );
  }

  return value;
}

function queryOptions(request, abortController) {
  const env = {
    ...process.env,
    CLAUDE_AGENT_SDK_CLIENT_APP: "orbit",
    ...(request.env ?? {}),
  };

  return compactObject({
    cwd: request.cwd,
    model: request.model,
    resume: request.resume,
    permissionMode: request.permissionMode,
    allowedTools: request.allowedTools,
    disallowedTools: request.disallowedTools,
    settingSources: request.settingSources,
    includePartialMessages: request.includePartialMessages,
    maxTurns: request.maxTurns,
    pathToClaudeCodeExecutable: request.pathToClaudeCodeExecutable,
    env,
    abortController,
  });
}

let request;
let lines;

try {
  ({ request, lines } = await readRequest());
} catch (error) {
  emit({ orbit_event: "sdk_error", error: errorPayload(error, "invalid_request") });
  process.exit(1);
}

let query;

try {
  ({ query } = await import("@anthropic-ai/claude-agent-sdk"));
} catch (error) {
  emit({ orbit_event: "sdk_error", error: errorPayload(error, "sdk_import_failed") });
  process.exit(1);
}

const abortController = new AbortController();

lines.on("line", (line) => {
  try {
    const command = JSON.parse(line);

    if (command?.type === "abort") {
      abortController.abort();
    }
  } catch (_error) {
    // Ignore malformed control input; SDK messages are only emitted on stdout.
  }
});

try {
  for await (const message of query({
    prompt: request.prompt,
    options: queryOptions(request, abortController),
  })) {
    emit({ orbit_event: "sdk_message", message });
  }
} catch (error) {
  if (abortController.signal.aborted) {
    emit({ orbit_event: "sdk_stopped", error: errorPayload(error, "sdk_aborted") });
    process.exit(0);
  }

  emit({ orbit_event: "sdk_error", error: errorPayload(error, "sdk_query_failed") });
  process.exit(1);
}
