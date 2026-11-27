# Grid Memory quickstart (copy-paste flow)

All commands run from `/workspace/grid-memory`.

## 0. One-time setup (already done on this box)
```
cd /workspace/grid-memory
bin/gridmem init          # creates data/grid.db and the hub signing key
bin/gridmem seed          # starter entries (add --run-keeper-tests to verify the Keeper test count)
bin/gridmem check         # should print "ok": true
```

## 1. Make a briefing and paste it into a chat
```
bin/gridmem briefing --model grok                      # print it
bin/gridmem briefing --model gemini --topic genesis --max-chars 3000 --out /tmp/brief.txt
```
Copy the whole block, from `=== GRID MEMORY BRIEFING` to `=== END GRID MEMORY BRIEFING ===`, into a new chat,
then ask your question under it.

## 2. Bring the reply back
Save the model's whole reply to a file (or pipe it in), then preview it. **Nothing is saved yet:**
```
bin/gridmem ingest /tmp/grok_reply.txt --source grok --session "chat 2026-10-25"
# or:  xclip -o | bin/gridmem ingest --source grok
```
If the proposals look right, save them (or only some of them):
```
bin/gridmem ingest /tmp/grok_reply.txt --source grok --session "chat 2026-10-25" --yes
bin/gridmem ingest /tmp/grok_reply.txt --source grok --yes --only 1,3
```
Everything saved this way is `claimed`, with the model as its source.

## 3. Keep it honest
```
bin/gridmem add fact "Guardian line 278 uses betaEff = kappa/(1+c*kappa)" --tags guardian
bin/gridmem correct GM-0012 "Line 278 not changed yet; fix is proposed only"
bin/gridmem retract GM-0013 "wrong file"
bin/gridmem verify GM-0001 --hash /path/to/genesis_input.txt fd0adf9005622e103f02115a50314d91f83dc705f1d685b16d45f52ad6b940b2
bin/gridmem verify GM-0005 --output /workspace/derivation-audit/formulas_17_21_v2_checks_output.txt --expect "34/34 checks passed (final)"
bin/gridmem verify GM-0003 --command "/workspace/lattice-keeper/v034/.venv/bin/python -m pytest -q tests" --cwd /workspace/lattice-keeper/v034 --expect "88 passed"
bin/gridmem verify GM-0008 --operator-confirm --note "Jay, 2026-10-25"
bin/gridmem verify GM-0001 --agreed-by gemini      # recorded; status does NOT change
bin/gridmem history GM-0002
bin/gridmem check --evidence
```
(The ids GM-0012 and GM-0013 above are examples. Use `bin/gridmem list` to see the real ones.)

## 4. Optional: the local web page
```
./start_web.sh            # http://127.0.0.1:8792/ , Ctrl-C to stop
cat data/web_token        # paste into the "write token" box (needed only to save)
```
The page shows the entries and search, has a Copy briefing button, and has a paste box with Preview, then Save.

## 5. Later: Phase 2 (needs API keys you create yourself)
```
export XAI_API_KEY=...  GEMINI_API_KEY=...  ANTHROPIC_API_KEY=...  OPENAI_API_KEY=...
bin/gridmem ask --models grok,gemini,claude "Is the genesis anchor verified?"
bin/gridmem pending list
bin/gridmem pending accept P-1a2b3c4d --yes      # or: pending reject all
```
Models whose key isn't set are skipped with a message. Each call is billed by that provider.
