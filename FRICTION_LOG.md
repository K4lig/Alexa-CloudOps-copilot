# Friction log

## 1. The MCP Python SDK version must stay below 2.2
- **Date:** 2026-10-13
- **Tool:** MCP Python SDK
- **Task:** Install the SDK for a Streamable HTTP server that will later use Strands Agents (Bedrock).
- **Expected:** The latest MCP SDK works with the AWS agent SDK.
- **Actual:** strands-agents requires mcp<2.2, so the project is pinned to 2.1.x.
- **Severity:** minor
- **Workaround:** Pinned `mcp>=2.1,<2.2`; it still speaks protocol 2025-11-25.
- **Suggestion:** Document the supported MCP SDK range in the Alexa+ hackathon resources.
