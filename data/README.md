# Data

The SIRENE bulk dump is **not** committed to git (~2 GB). Download it locally
once, then DuckDB reads the CSVs directly.

## What you need

Two files from the INSEE Sirene bulk dump:

1. **`StockUniteLegale_utf8.zip`** — the legal entities (`unités légales`,
   keyed by `siren`). Roughly 30M rows.
2. **`StockEtablissement_utf8.zip`** — the establishments (`établissements`,
   keyed by `siret`). Roughly 40M rows.

Source: https://www.data.gouv.fr/datasets/base-sirene-des-entreprises-et-de-leurs-etablissements-siren-siret/

(Or the canonical INSEE page: https://www.sirene.fr/sirene/public/static/open-data)

## Get the dump

Run the helper script:

```bash
./scripts/fetch_sirene.sh
```

This downloads the two zips into `data/sirene/` and unzips them. The script
is idempotent — re-running skips files that are already present.

If you'd rather do it by hand:

```bash
mkdir -p data/sirene && cd data/sirene
curl -L -o StockUniteLegale_utf8.zip      https://files.data.gouv.fr/insee-sirene/StockUniteLegale_utf8.zip
curl -L -o StockEtablissement_utf8.zip    https://files.data.gouv.fr/insee-sirene/StockEtablissement_utf8.zip
unzip -o StockUniteLegale_utf8.zip
unzip -o StockEtablissement_utf8.zip
```

After unzipping you should have:

```
data/sirene/
  StockUniteLegale_utf8.csv     # ~7 GB unzipped
  StockEtablissement_utf8.csv   # ~9 GB unzipped
```

## How prospecter reads it

`src/prospecter/tools/duckdb_tool.py` registers two DuckDB views over those
CSVs at startup, with the column projection we actually need (≈ 15 columns
out of 100+). DuckDB scans only the projected columns, so memory stays
small even on the unfiltered table.

For repeat runs you can materialise to Parquet for ~5× faster scans:

```bash
uv run python -m prospecter.tools.duckdb_tool materialize
```

This produces `data/sirene/sirene.parquet`. The tool prefers Parquet if it
exists, falls back to the raw CSVs otherwise.

## What we drop

- `prenomUsuelUniteLegale`, `nomUniteLegale` (these are individual people on
  sole-proprietor entries — out of scope; we only target legal-entity
  prospects).
- Diffusion-restricted rows (`statutDiffusionUniteLegale != "O"`) are
  excluded by default. If you want them, set `PROSPECTER_INCLUDE_RESTRICTED=1`.

## Refresh cadence

INSEE refreshes the bulk dump monthly. For this project, that's far more
often than the eval needs to be re-run. Treat the dump as static for the
duration of an experiment.
