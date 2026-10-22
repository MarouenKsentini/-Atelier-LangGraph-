# AGENTS.md — atelier_LangGraph

LangGraph ReAct agent (Gemini via `langchain-google-genai`) that calls an **external** MCP
"Facturation" server over streamable HTTP. Python 3.13 + `uv`. No tests, no lint/CI config,
and `README.md` is empty — this file and the module docstrings are the only docs.

## Commands

Run from the repo root: `graph.py` is imported by bare name (`from graph import ...`).

```powershell
uv sync                          # refresh .venv from uv.lock (uv 0.12.5)
uv run agent.py --outils         # list tools discovered on the MCP server, no LLM call
uv run agent.py "question"       # one or more questions, isolated thread each
uv run agent.py                  # built-in golden set (2 questions, costs LLM calls)
uv run agent.py --graph          # writes graphe.png, then ALSO runs the golden set
uv run chat.py                   # interactive REPL, one thread for the whole session
uv run python -c "import graph"  # cheap import/syntax check
```

- Prerequisite: MCP server at `http://127.0.0.1:8000/mcp` (default `MCP_URL`). It is **not in
  this repo**; `agent.py`/`chat.py` `sys.exit(1)` when it is unreachable.
- `graphe.png` is rendered through the public mermaid.ink API; offline the export falls back to
  `graphe.mmd`.
- There is no test/lint command to run — don't invent one.

## Layout

- `graph.py` — env loading, `SYSTEM_PROMPT`, `construire_graphe()`, `derniere_reponse()`. All
  graph wiring and model config lives here.
- `agent.py` — non-interactive CLI: tool inventory, graph export, golden set, Thought/Action/Observation trace.
- `chat.py` — interactive loop.
- `src/atelier_langgraph/__init__.py` — stub `main()` printing "Hello from atelier-langgraph!".
  The `atelier-langgraph` console script points at this stub, **not** at the real CLI; run the
  root scripts directly.

## Gotchas

- Graph is `START -> agent -[tool_calls?]-> tools -> agent`, compiled with `InMemorySaver()`.
  Memory is per-process and keyed by `configurable.thread_id`; nothing persists across runs.
  `chat.py` reuses one thread (follow-ups see history); `agent.py` uses a fresh thread per question.
- `recursion_limit` must be passed per `ainvoke` (via `RECURSION_LIMIT = 15`), not on the compiled
  graph — keep it in the config dict in any new entrypoint.
- Persona/behavior changes go in `SYSTEM_PROMPT` in `graph.py`. The agent answers in French with
  TND HT amounts, and the system prompt forbids guessing IDs or inventing data.
- `appeler_modele` prepends `SystemMessage` only when `messages[0]` isn't one, so a resumed thread
  keeps the prompt it started with.
- `construire_graphe()` rebuilds the LLM and graph on every call; there is no module-level graph singleton.
- Tool names/args come from the external server and are mixed French/camelCase
  (`filterArticlesByPriceRange`, `top_clients_ca`, `clients_retard_paiement`). Changing tools means
  changing that server, not this repo.
- `.env` is **not** gitignored and holds `GOOGLE_API_KEY`, `GOOGLE_MODEL`, `MCP_URL`. `graph.py`
  calls `load_dotenv()` at import, so anything importing `graph` gets the values. Never print or
  commit secrets.
- Gemini free tier quota is **20 requests/day per model** (`429 RESOURCE_EXHAUSTED`, retry delay
  often ~17h). It is not a code bug: switch model or wait. `.env` sets `GOOGLE_MODEL` plus
  `GOOGLE_MODEL_SECOURS` (comma-separated); `construire_graphe()` binds tools on each model and
  chains them with `with_fallbacks`, so a depleted model rolls over to the next. A fallback call
  can take ~70s because the SDK retries before giving up on the primary.
- A `404 NOT_FOUND ... no longer available to new users` on the model means it was retired for this
  API key. List what the key can use, then update `GOOGLE_MODEL`/`GOOGLE_MODEL_SECOURS` in `.env`
  **and** the defaults in `graph.py` (separate places):

  ```powershell
  $k = (Get-Content .env | Where-Object {$_ -match '^GOOGLE_API_KEY='}) -replace '^GOOGLE_API_KEY=',''
  (Invoke-RestMethod "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200&key=$k").models.name
  ```

  The listing includes models the key still cannot call, and `gemini-flash-latest` shares its quota
  with the model it aliases. Probe for real: a 1-token `generateContent` returns `200` / `429` / `404`.
- `gemini-3.8-flash` also throws transient `503` under load; the fallback chain absorbs it.
- Don't pass `temperature` to `ChatGoogleGenerativeAI`. Gemini 3+ models use fixed sampling, so the
  SDK prints `UserWarning ... temperature will be ignored` on every request; `graph.py` omits it on
  purpose (determinism comes from the prompt, not the sampler).
- The MCP server sends `additionalProperties: false` in every tool schema; Gemini rejects that key,
  so `langchain-google-genai` floods `Key 'additionalProperties' is not supported in schema,
  ignoring` once per tool. `outils_compatibles_gemini()` (called at the top of
  `construire_graphe()`) strips it from copies of the tools before `bind_tools`/`ToolNode` — keep
  that call in any new entrypoint instead of silencing loggers.
- `pyproject.toml` uses the `uv_build` backend (src layout). Moving the root scripts into
  `src/atelier_langgraph/` breaks the `from graph import ...` imports unless they are updated too.
