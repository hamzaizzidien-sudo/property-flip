# Scheduled run runbook

Followed by the scheduled Claude sessions (daily Mon–Sat, weekly sweep Sunday). PropertyData is
called through the signed-in **PropertyData MCP connector**; everything else is local and free.

Hard rules
- Only call `get-sourced-properties`, only with the exact `mcp_arguments` printed by `plan`, and only
  while `plan` says `"action": "CALL"`. The per-run caps (daily 5, weekly 50 credits) are enforced there.
- Never retry a paid call. If anything is unexpected (error, refusal, mismatch, odd counts), stop,
  commit what you have, and report.
- Never edit, summarise or reformat a response before importing it.

Steps (`KIND` is `daily` or `weekly`; `RUN_ID` is `KIND-YYYYMMDD`)

1. Get the repo: attach `hamzaizzidien-sudo/property-flip` with push access, clone it, `cd` into it,
   `git pull origin main`, then `pip install -q -r requirements.txt`.
2. Reference data: if `data/reference.db` is missing run
   `python -m flipwatch.cli load-sold-prices 2021 2022 2023 2024 2025 2026` (~3 min, free).
   On weekly runs also refresh the current year: `python -m flipwatch.cli load-sold-prices 2026`.
3. `python -m flipwatch.cli plan KIND --run-id RUN_ID --page 1`
4. If `action` is `CALL`, call `get-sourced-properties` with exactly `mcp_arguments`.
   - If the tool result was saved to a file, use that file path.
   - Otherwise write the returned JSON text, byte for byte, to a scratch file.
5. `python -m flipwatch.cli import-response FILE --kind KIND --run-id RUN_ID --page N`
   - If it prints `"next": "page N+1"`, repeat from step 3 with `--page N+1`.
   - Stop on `"next": "DONE"` or when `plan` says `STOP`.
6. Weekly only: `python -m flipwatch.cli finish-run weekly --run-id RUN_ID` (marks in-scope
   properties missing from a complete sweep as REMOVED; does nothing if the sweep was capped).
7. `python -m flipwatch.cli screen --changed`, then `python -m flipwatch.cli shortlist`,
   then `python -m flipwatch.cli digest --hours 26`.
8. `git add data/flipwatch.db data/raw output && git commit -m "Flip Watch KIND run RUN_ID" && git push origin main`
9. Reply with the digest (`output/daily_report.md`), the credits used this run and in total, and
   anything unexpected.
