import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { query } from "@anthropic-ai/claude-agent-sdk";

const providedWorkspace = process.env.ORBIT_CLAUDE_AGENT_SDK_PROBE_WORKSPACE;
const workspace = providedWorkspace || (await mkdtemp(join(tmpdir(), "orbit-claude-agent-sdk-")));

const model = process.env.ORBIT_CLAUDE_AGENT_MODEL || "sonnet";

async function resetWorkspace() {
  if (providedWorkspace && !resolve(workspace).includes("orbit-claude-agent-sdk")) {
    throw new Error(
      "Refusing to reset ORBIT_CLAUDE_AGENT_SDK_PROBE_WORKSPACE because the path " +
        "does not contain orbit-claude-agent-sdk",
    );
  }

  await rm(workspace, { recursive: true, force: true });
  await mkdir(workspace, { recursive: true });
  await writeFile(resolve(workspace, "seed.txt"), "seed=present\n");
}

function countPartialEvent(summary, message) {
  const eventType = message.event?.type ?? "unknown";
  summary.partialEventCount += 1;
  summary.partialEventTypes[eventType] = (summary.partialEventTypes[eventType] ?? 0) + 1;
}

function recordAssistantEvents(summary, message) {
  for (const block of message.message?.content ?? []) {
    if (block?.type === "tool_use") {
      summary.toolUses.push({
        name: block.name,
        input: block.input,
      });
    }
  }
}

function recordToolResults(summary, message) {
  for (const block of message.message?.content ?? []) {
    if (block?.type === "tool_result") {
      summary.toolResults.push({
        content: block.content,
        isError: block.is_error,
      });
    }
  }
}

async function runTurn(prompt, extraOptions = {}) {
  const summary = {
    sessionId: null,
    resultSubtype: null,
    resultText: null,
    numTurns: null,
    stopReason: null,
    systemInit: null,
    partialEventCount: 0,
    partialEventTypes: {},
    toolUses: [],
    toolResults: [],
    permissionDenials: [],
    messageTypes: [],
    thrown: null,
  };

  try {
    for await (const message of query({
      prompt,
      options: {
        cwd: workspace,
        model,
        allowedTools: ["Read", "Write", "Edit", "Bash", "Glob"],
        tools: ["Read", "Write", "Edit", "Bash", "Glob"],
        permissionMode: "acceptEdits",
        includePartialMessages: true,
        settingSources: [],
        maxTurns: 8,
        ...extraOptions,
      },
    })) {
      summary.messageTypes.push(`${message.type}${message.subtype ? `:${message.subtype}` : ""}`);

      if (message.type === "system" && message.subtype === "init") {
        summary.systemInit = {
          sessionId: message.session_id,
          cwd: message.cwd,
          model: message.model,
          apiKeySource: message.apiKeySource,
          permissionMode: message.permissionMode,
          tools: message.tools,
        };
      }

      if (message.type === "stream_event") {
        countPartialEvent(summary, message);
      }

      if (message.type === "assistant") {
        recordAssistantEvents(summary, message);
      }

      if (message.type === "user") {
        recordToolResults(summary, message);
      }

      if (message.type === "system" && message.subtype === "permission_denied") {
        summary.permissionDenials.push({
          tool: message.tool_name,
          input: message.tool_input,
        });
      }

      if (message.type === "result") {
        summary.sessionId = message.session_id;
        summary.resultSubtype = message.subtype;
        summary.resultText = message.result;
        summary.numTurns = message.num_turns;
        summary.stopReason = message.stop_reason;
        summary.permissionDenials = message.permission_denials ?? summary.permissionDenials;
      }
    }
  } catch (error) {
    summary.thrown = `${error.name}: ${error.message}`;
  }

  return summary;
}

async function runAbortProbe() {
  const abortController = new AbortController();
  const summary = {
    stopped: false,
    errorName: null,
    errorMessage: null,
    messageTypes: [],
  };

  const timer = setTimeout(() => abortController.abort(), 1500);

  try {
    for await (const message of query({
      prompt: "Run `sleep 20 && echo SHOULD_NOT_PRINT`, then say done.",
      options: {
        cwd: workspace,
        model,
        allowedTools: ["Bash"],
        tools: ["Bash"],
        permissionMode: "acceptEdits",
        settingSources: [],
        maxTurns: 3,
        abortController,
      },
    })) {
      summary.messageTypes.push(`${message.type}${message.subtype ? `:${message.subtype}` : ""}`);
    }
  } catch (error) {
    summary.stopped = true;
    summary.errorName = error.name;
    summary.errorMessage = error.message;
  } finally {
    clearTimeout(timer);
  }

  return summary;
}

await resetWorkspace();

const firstTurn = await runTurn(
  "In the current working directory, create ./orbit_probe.txt with exactly one line: first=ok. " +
    "Do not use an absolute path. Then run `ls -1 orbit_probe.txt`. " +
    "Keep the final response under 20 words and include PROBE_DONE.",
);

const resumedTurn = await runTurn(
  "In the current working directory, append exactly one line `second=ok` to ./orbit_probe.txt. " +
    "Do not use an absolute path. Then run `cat orbit_probe.txt`. " +
    "Keep the final response under 20 words and include PROBE_RESUME_DONE.",
  { resume: firstTurn.sessionId },
);

let fileText = null;

try {
  fileText = await readFile(resolve(workspace, "orbit_probe.txt"), "utf8");
} catch (error) {
  fileText = `READ_ERROR:${error.message}`;
}

const abortProbe = await runAbortProbe();

console.log(
  JSON.stringify(
    {
      workspace,
      model,
      firstTurn,
      resumedTurn,
      sameSession: firstTurn.sessionId === resumedTurn.sessionId,
      fileText,
      abortProbe,
    },
    null,
    2,
  ),
);
