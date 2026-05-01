# Benchmark Test Configurations

YAML files in this directory define benchmark test scenarios. Copy any
file as a starting point, edit it, and pass the path to the replay+QA
script via `--config`.

## File format

```yaml
# Optional comment describing the scenario
sources:
  <ModuleClassName>: <role>
  ...
use_agent_loop: <bool>     # default: true
```

## Available roles

| Role | Replay writes data? | QA injects context? | When to use |
|------|--------------------|--------------------|-------------|
| `active` (default) | yes | yes | Full participation |
| `no_qa` | yes | no | Hide module's QA context (isolation) |
| `off` | no | no | Ablation — module never touches data |

Sources NOT listed default to `active`.

## Available sources

### Module class names (real modules)

| Name | What it manages |
|------|-----------------|
| `AwarenessModule` | User preferences profile |
| `BasicInfoModule` | Agent identity / session info |
| `ChatModule` | Conversation history (long + short term) |
| `CommonToolsModule` | Generic shared tools |
| `EventMemoryModule` | Event-level storage |
| `GeminiRagModule` | Document RAG (Gemini) |
| `JobModule` | Background jobs / scheduling |
| `LarkModule` | Lark / Feishu integration |
| `MatrixModule` | Matrix protocol integration |
| `MemoryModule` | Long-term semantic memory (EverMemOS) |
| `MessageBusModule` | Cross-agent message bus |
| `SkillModule` | Skill loading / execution |
| `SocialNetworkModule` | Social graph |

### Special non-module sources

| Name | What it manages |
|------|-----------------|
| `Narrative` | Narrative summary in QA system prompt |

## Top-level toggles

| Field | Default | Effect |
|-------|---------|--------|
| `use_agent_loop` | `true` | Replay drives each round through full `AgentRuntime.run()` so the agent can call MCP tools. Set `false` for hook-only fast replay (no LLM). |

## Provided presets

- [`sn_isolation.yaml`](sn_isolation.yaml) — Test SN alone
- [`memory_isolation.yaml`](memory_isolation.yaml) — Test Memory (EverMemOS) alone
- [`awareness_isolation.yaml`](awareness_isolation.yaml) — Test Awareness alone
- [`sn_plus_memory.yaml`](sn_plus_memory.yaml) — Cross-module: SN + Memory
- [`full_integration.yaml`](full_integration.yaml) — All modules participating
