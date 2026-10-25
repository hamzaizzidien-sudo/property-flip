# Flip Watch (prototype)

Buy → refurbish → sell opportunity tracker for ~15 miles around L35. Funnel:
discovery (paid, tiny) → local screening (free) → free enrichment → paid enrichment (shortlist only) → appraisal.

```
pip install -r requirements.txt
cp .env.example .env                                  # add PROPERTYDATA_API_KEY
python -m flipwatch.cli discover --results 10         # dry run: prints params + credit estimate
python -m flipwatch.cli discover --results 10 --confirm   # spends credits (capped per run)
python -m flipwatch.cli import data/raw/<file>.json   # ingest a saved response (REST or MCP)
python -m flipwatch.cli report                        # console table + output/candidates.csv
python -m flipwatch.cli analyse <id> --gdv 200000 --gdv-low 190000 --gdv-high 210000 --refurb 25000 --confidence MEDIUM
python -m flipwatch.cli credits                       # credit ledger
pytest -q
```

Layout: `flipwatch/sources/` (one adapter per feed, normalising into `sources/base.Listing`),
`ingest.py` (dedupe, observations, events), `signals.py` (keyword motivation/risk flags),
`finance.py` (SDLT, cost stack, max purchase price, offer gap), `scoring.py`, `db.py` (schema).
Raw payloads are kept in `data/raw/` and the `raw_responses` table.
