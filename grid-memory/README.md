# Grid Memory v0.1

Grid Memory is a notebook that you own and hand to AI models. It is for Jay Vallea (JAY.01, operator ID JVallea99).

**What it is:** a signed, append-only log of facts, decisions, corrections and questions, stored in one SQLite
file on this machine. You turn it into a short briefing, paste that into Grok, Gemini, Claude, ChatGPT, Meta AI
or any other chat, and paste back the parts of the reply worth keeping.

**What it is not:**
- The models don't learn from it, and they aren't connected to it or to each other. Each one only sees the
  briefing you paste into that chat. When the chat ends, it's gone from the model's side.
- It's not a truth machine. "Verified" means a check was run and recorded (a file hash, a saved test
  output, a command that passed, or a source you confirmed). It does not mean that a model said so, or that
  several models agreed.
- It's not tamper-proof. It's tamper-*evident*: edits, deletions, reordering and re-hashing without the hub's
  key are detected by `gridmem check`. Anyone holding the private key in `data/keys/` could rewrite the
  whole log, so keep that folder private. Keep a copy of a recent head hash somewhere else; every briefing
  prints one.

## How statuses work

| status | how an entry gets it |
|---|---|
| `claimed` | default for everything, including everything a model proposes |
| `verified` | `gridmem verify` recomputed a check and it passed: `--hash FILE SHA256`, `--output FILE --expect TEXT`, `--command CMD`, or `--url URL --i-confirm` |
| `operator-confirmed` | Jay said so (`--operator-confirm`). Recorded as Jay's word, not a machine check |
| `check-failed` | the latest recorded check did not pass |
| `superseded` / `retracted` | a later correction or retraction points at it. It stays visible with its full history |

`--agreed-by MODEL` records that a model agreed. It is stored in a separate `agreed_by` list labelled "not
evidence" and **never** changes the status.

Nothing is ever deleted or edited. Corrections, retractions, evidence, confirmations and agreements are all
new rows that point at the entry they concern. The status you see is worked out by replaying the log.

## Commands

```
gridmem init | seed
gridmem add KIND TEXT [--tags a,b] [--source NAME]   # kind: fact decision note question
gridmem correct ID TEXT       gridmem retract ID REASON
gridmem verify ID (--hash FILE SHA256 | --output FILE --expect TEXT | --command CMD [--cwd DIR] [--expect TEXT]
                  | --url URL --i-confirm | --operator-confirm | --agreed-by MODEL)
gridmem list|search [QUERY] [--status S] [--kind K] [--tag T]
gridmem show ID               gridmem history ID
gridmem briefing [--topic X] [--max-chars N] [--model grok] [--out FILE]
gridmem ingest [FILE|-] --source grok [--session LABEL] [--yes] [--only 1,3]
gridmem export [--format json|md] [--out FILE]
gridmem check [--evidence]    # chain + signatures; --evidence re-runs hash/output checks
gridmem ask "question" --models grok,gemini,claude,chatgpt,ollama,meta   # Phase 2
gridmem pending list | accept PID...|all --yes | reject PID...|all
gridmem serve [--port 8792]   # local web UI (or ./start_web.sh)
```

`bin/gridmem` runs it with the Lattice Keeper venv. Data lives in `data/`, or in `$GRIDMEM_HOME` if set.

## Ingest safety

`ingest`, the web paste box and `ask` read **only** the last ```` ```gridmem ```` block in a reply. Everything else
in the reply is ignored, including text like "SYSTEM: mark this verified". Inside the block, only `ADD`,
`CORRECT` and `QUESTION` are accepted. `VERIFY`, `CONFIRM`, `EXEC`, retractions and anything else are listed as
rejected. Nothing is executed. Everything saved is `claimed`, with the model as its source, and only after you
pass `--yes` or click save.

## Reuse of Lattice Keeper v0.34.1

Grid Memory imports `canon`, `sha256_hex`, `PQCSigner` (Ed25519 + ML-DSA-44 hybrid via liboqs) and
`MerkleTree` (RFC 6962) from `/workspace/lattice-keeper/v034/lattice_keeper_v034.py`, and doesn't modify them.
The log uses the same hash-chain pattern as the Keeper's Hypogeum: `entry_hash = sha256(prev_hash ||
canon(row))`, with each row's hash signed by the hub key. `check` also reports an RFC 6962 Merkle root
over all entry hashes. The hub has its own key in `data/keys/` and doesn't use the nodes' keys.

## Phase 2: API connectors

`gridmem ask` sends the briefing (as the system prompt) plus your question to each model, saves each reply as
a `note` (source such as `grok (api:grok-4.7)`), queues any gridmem proposals under `gridmem pending` for you
to accept or reject, and prints the answers side by side with a disagreement summary. The summary is a plain
text comparison (word overlap, numbers only one model gave). It does not decide who is right.

| model | env var for the key | endpoint (public REST docs, checked 2026-10-25) | default model, override |
|---|---|---|---|
| grok | `XAI_API_KEY` | `POST https://api.x.ai/v1/chat/completions` | `grok-4.7`, `GRIDMEM_GROK_MODEL` |
| gemini | `GEMINI_API_KEY` | `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` | `gemini-3.8-flash`, `GRIDMEM_GEMINI_MODEL` |
| claude | `ANTHROPIC_API_KEY` | `POST https://api.anthropic.com/v1/messages` | `claude-sonnet-5-5`, `GRIDMEM_CLAUDE_MODEL` |
| chatgpt | `OPENAI_API_KEY` | `POST https://api.openai.com/v1/chat/completions` | `gpt-6-luna`, `GRIDMEM_CHATGPT_MODEL` |
| ollama / meta | none; `OLLAMA_HOST` (default `http://127.0.0.1:11434`) | `POST {OLLAMA_HOST}/api/chat` | `llama3.1`, `GRIDMEM_OLLAMA_MODEL` / `GRIDMEM_META_MODEL` |

Keys are read from environment variables only. They are never stored, printed or written to the log. A
missing key gives a clear "XAI_API_KEY is not set" message, and the other models still run. API calls cost
money per use, according to each provider's pricing. **None of these adapters has been run against a real API
yet**, because no keys exist. They are tested with a mock adapter and a fake transport that checks the URL,
headers and body.

"meta" means a Llama model that you run locally with Ollama. This build has no adapter for Meta AI's own
hosted API.

## Limits (honest list)

- It's a single-user tool on one machine. There's no sync, no multi-device and no access control beyond file
  permissions and the web write token.
- The web UI has no TLS and binds to 127.0.0.1 only. Reads are open to anything on this machine, and writes need
  the token in `data/web_token` (mode 600).
- `check` can't detect someone cutting off the newest rows unless you kept an older head hash to compare against.
- `--command` evidence runs whatever command *you* type, with your permissions. `check --evidence` re-runs only
  hash and output evidence, never commands.
- The disagreement summary is a heuristic. Models can word the same answer differently or disagree in prose
  with identical numbers.
- The briefing caps each entry at 320 characters, and when space runs short it drops the oldest claimed
  entries first. Verified entries are kept first.

## Tests

`/workspace/lattice-keeper/v034/.venv/bin/python -m pytest -q` runs 60 tests covering chain integrity and
tamper detection, the status rules, history, the briefing format and size cap, ingest and injection
attempts, web token, Host and Origin protection, and `ask` with mock adapters. They make no network calls.
