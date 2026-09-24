# Snapshot contract, v1

This contract supports local, read-only comparison. All files must be UTF-8 CSV with exact headers, one header row, and no additional columns. A complete snapshot means that the operator has verified all relevant mapped products were included. Setting the flags to `true` without that verification can produce false `channel_missing` or false resolutions.

`manifest.json` has exactly these fields:

```json
{
  "merchant_id": "your-internal-merchant-id",
  "country": "US",
  "reporting_context": "SHOPPING_ADS",
  "content_language": "en",
  "feed_label": "US",
  "currency": "USD",
  "snapshot_at": "2026-09-24T10:00:00Z",
  "storefront_complete": true,
  "channel_complete": true
}
```

`mapping.csv`: `sku,offer_id`. Every storefront, channel, and economics row must have an explicit one-to-one mapping. A mapping may reference a missing product so that the engine can detect it.

`storefront.csv`: `sku,price_usd,inventory_quantity,published`. `published` is exactly `true` or `false`. Quantity is a nonnegative integer. The operator must decide how their platform's inventory and publication semantics map to these fields.

`channel.csv`: `offer_id,price_usd,availability,approval_status`. Availability is `in_stock` or `out_of_stock`; approval is `approved`, `pending`, or `disapproved`. Export only the manifest's country, reporting context, content language and feed label, not an all-market aggregate.

`economics.csv`: `sku,unit_margin_usd,units_sold_30d`. This file may contain only a header. Margin is a modeled nonnegative USD amount per unit after whichever direct costs the merchant has chosen; those cost inclusions must be documented by the merchant. Units are a nonnegative integer over a complete 30-day lookback. The priority proxy does not incorporate advertising, taxes, refunds, seasonality, or conversion probabilities.

Prices and margin accept at most two decimal places. The engine requires USD because currency conversion without timing and rate provenance would silently change incident meaning. It uses exact decimal arithmetic. Both source snapshots must be no older than `--max-age-hours` (24 by default) at the declared `--as-of` time. For a new day, run with a new timestamp and a truly complete snapshot; `--previous` must point to a report from the same merchant/country/context/currency and an earlier snapshot.

The output `report.json` is a deterministic calculation over the current files and, for lifecycle state, the supplied previous report. To verify a report that used `--previous`, supply the same previous report to `verify`. SHA-256 detects accidental input drift; it does not prove that the merchant or platform produced those files. `resolved_observed` means the incident condition is absent in a newer complete snapshot. It does not identify the fix, guarantee the product is selling, or quantify recovered revenue.
