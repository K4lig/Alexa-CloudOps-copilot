# Friction log

Frictions found while building Alexa CloudOps Copilot for Amazon Developer Hackaton 2026.
Severity: **blocker** (could not continue) | **major** (lost more than 1 hour) | **minor** (annoying, quick workaround).

## 1. The MCP Python SDK version must stay below 2.2
- **Date:** 2026-10-13
- **Tool:** MCP Python SDK
- **Task:** Install the SDK for a Streamable HTTP server that will later use Strands Agents (Bedrock).
- **Expected:** The latest MCP SDK works with the AWS agent SDK.
- **Actual:** strands-agents requires mcp<2.2, so the project is pinned to 2.1.x.
- **Severity:** minor
- **Workaround:** Pinned `mcp>=2.1,<2.2`; it still speaks protocol 2025-11-25.
- **Suggestion:** Document the supported MCP SDK range in the Alexa+ hackathon resources.

---

## 2. Connecting a self-hosted MCP server to real Alexa+ needs more than the track suggests
- **Date:** 2026-10-14
- **Tool:** Alexa+ MCP Toolkit
- **Task:** Connect my Streamable HTTP MCP server to Alexa+.
- **Steps:** Read the MCP Toolkit quickstart and the hackathon resources.
- **Expected:** Point Alexa+ at a public Streamable HTTP URL and test it.
- **Actual:** It also requires OAuth 2.1 with PKCE (account linking and protected resource metadata), responses under 500 ms, the `alexa-ai` CLI with Login with Amazon, and the examples only use the US marketplace (`en-US`). With a few days left, and from Ecuador, this was not feasible.
- **Severity:** major
- **Workaround:** Built the simulated Alexa+ path the rules allow: a Strands agent that calls the same MCP server over Streamable HTTP.
- **Suggestion:** Provide a hackathon sandbox (a test skill or simulator that accepts a bearer token instead of full OAuth) and state supported countries and accounts up front.

---

## 3. A new account has a Bedrock daily token quota of 0 for every model
- **Date:** 2026-10-19
- **Tool:** Amazon Bedrock (Claude Haiku 4.5, Amazon Nova Lite, Amazon Nova 2 Lite)
- **Task:** Call a Bedrock model from my agent.
- **Steps:** `aws bedrock-runtime converse` with several models; checked Service Quotas; upgraded the account from the Free plan to the Paid plan; tried to request an increase.
- **Expected:** A working default quota, or a clear message explaining how to get one.
- **Actual:** Every model fails with `ThrottlingException: Too many tokens per day`. Service Quotas shows "Model invocation max tokens per day" = 0.0 for every model, marked as not adjustable (`request-service-quota-increase` returns "quota is not adjustable"). Upgrading to the Paid plan did not change it. Only a support case can raise it.
- **Severity:** blocker
- **Workaround:** Opened a support case. The agent supports several model providers through Strands, so the demo runs on Groq while the Bedrock request is pending. Bedrock remains the default in code.
- **Suggestion:** When the quota is 0, return an explicit error ("your quota for this model is 0; request an increase") with a link, instead of "Too many tokens per day", which suggests a temporary limit. Show default quotas when a model is enabled, and allow a self-service increase for small, low-usage values.

---

## 4. A throttled Strands agent looks frozen, with no output or error
- **Date:** 2026-10-19
- **Tool:** Strands Agents SDK (BedrockModel)
- **Task:** Ask the agent its first question.
- **Steps:** Created the agent with `callback_handler=None` and asked one question.
- **Expected:** An answer, or an error within a few seconds.
- **Actual:** The program waited silently for minutes. A KeyboardInterrupt traceback only showed a worker thread waiting. The real cause (the Bedrock quota throttling in entry 3) appeared only when calling Bedrock directly with the AWS CLI.
- **Severity:** major (about 1 hour lost)
- **Workaround:** Wrote a check script that tests each layer (MCP server, model alone, full agent) with a timeout and prints the exception.
- **Suggestion:** Log a warning on each retry ("Bedrock throttled, retrying in N s"), and stop with an error when the quota is 0 or the error is not retryable, instead of backing off silently.

---

## 5. Warning on every turn with reasoning models through the OpenAI provider
- **Date:** 2026-10-19
- **Tool:** Strands Agents SDK + Groq
- **Task:** Use `openai/gpt-oss-120b` through `OpenAIModel`.
- **Expected:** Clean output.
- **Actual:** Every turn prints a warning about unsupported `reasoningContent`. The answers are correct.
- **Severity:** minor
- **Workaround:** `logging.getLogger("strands.models.openai").setLevel(logging.ERROR)`.
- **Suggestion:** Handle or quietly drop reasoning content in the OpenAI-compatible provider.

## 6. Groq retired the model the agent depended on
- **Date:** 2026-10-21
- **Tool:** Groq API
- **Task:** Run voice-mcp-sim with its default model.
- **Expected:** The model to keep working.
- **Actual:** `404 model_not_found` for `llama-3.3-70b-versatile`, which Groq shut down on 2026-08-16.
- **Severity:** minor
- **Workaround:** Default changed to `openai/gpt-oss-120b`; the model is always configurable with `GROQ_MODEL`.
- **Suggestion:** Never hardcode a model ID without an override, and check provider deprecation pages before demos.

## 7. Browser speech recognition fails with "network"
- **Date:** 2026-10-21
- **Tool:** Web Speech API (Chrome)
- **Task:** Voice input in voice-mcp-sim.
- **Expected:** Speech-to-text works in the browser.
- **Actual:** "Voice error: network". Chrome sends the audio to Google's servers, which some browsers, VPNs and networks block. Headless Chromium never answers at all.
- **Severity:** major
- **Workaround:** voice-mcp-sim records the audio and transcribes it on the server with Whisper (Groq) when browser speech fails or does not answer within 2.5 seconds.
- **Suggestion:** For voice demos, don't rely only on browser speech recognition.

## 8. Streaming gpt-oss on Groq repeated replies and skipped tools
- **Date:** 2026-10-21
- **Tool:** Strands Agents SDK (`OpenAIModel`) + Groq `openai/gpt-oss-120b`
- **Task:** Multi-turn voice conversation with tool calls.
- **Expected:** One answer per turn, backed by a tool call.
- **Actual:** Some answers came back twice in the same text ("X.X."), and follow-up questions were answered from conversation history without calling a tool.
- **Severity:** major
- **Workaround:** `OpenAIModel(..., stream=False)`, a prompt rule to always call a tool for live data, and a guard that collapses an exactly duplicated reply.
- **Suggestion:** Document provider-specific streaming quirks for OpenAI-compatible endpoints in Strands.


