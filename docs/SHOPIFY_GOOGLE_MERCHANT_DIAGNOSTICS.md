# Shopify and Google Merchant Center product diagnostics

**Use this guide to compare a Shopify storefront snapshot with a Google Merchant Center processed-products snapshot for one declared sales-channel scope.** Commerce Incident Network identifies missing offers, disapprovals, price differences, and availability differences. It produces a local report and a human operator queue; it does not edit either platform.

The included example is fictional. The Shopify and Google capture connectors are implemented and tested with mocked API responses, but this repository has not exercised them against a merchant account. A merchant-authorized pilot still needs real permissions, an explicit SKU-to-offer mapping, and complete snapshots.

## What can the diagnostic answer?

| Merchant question | Signal used here | What the result does **not** establish |
| --- | --- | --- |
| Why is a product disapproved in Google Merchant Center? | The processed product's approval status for the declared country and reporting context | The underlying policy reason or required correction. Review the platform's issue details. |
| Why do Shopify and Google show different prices? | Exact USD prices for a mapped SKU and offer | Which price is intended, or whether a promotion explains the difference. |
| Why does Google show an in-stock item that Shopify cannot sell? | Storefront quantity and channel availability | That the feed is the cause, or that changing inventory is safe. |
| Why is a Shopify product absent from Google? | A published, explicitly mapped storefront SKU missing from a **complete** channel snapshot | That Google rejected it. Check feed submission, eligibility, and mapping. |
| Was an incident fixed? | The same condition is absent in a newer, complete snapshot of the same scope | Which action caused the change or whether it increased sales. The state is `resolved_observed`. |

The comparison is intentionally narrower than a general Merchant Center audit. It operates on one merchant, Shopify shop, Google account, country, reporting context, content language, feed label, and USD currency at a time. The [data contract](DATA_CONTRACT.md) defines required fields, freshness, completeness, and mapping rules.

## Reproduce the fictional example

Run from the repository root with Python 3.11 or newer. These commands read only checked-in fixtures and write reports under `/tmp`.

```bash
PYTHONPATH=src python3 -m commerce_incident_network run fixtures/demo/day1 /tmp/cin-day1 --as-of 2026-09-24T12:00:00Z
PYTHONPATH=src python3 -m commerce_incident_network run fixtures/demo/day2 /tmp/cin-day2 --as-of 2026-09-25T12:00:00Z --previous /tmp/cin-day1/report.json
PYTHONPATH=src python3 -m commerce_incident_network verify fixtures/demo/day1 examples/reports/day1.json
PYTHONPATH=src python3 -m commerce_incident_network verify fixtures/demo/day2 examples/reports/day2.json --previous examples/reports/day1.json
```

Open `/tmp/cin-day2/dashboard.html` for the local report. The checked-in [first report](../examples/reports/day1.json) and [second report](../examples/reports/day2.json) are the exact JSON generated from the fixtures. `verify` recomputes them from the supplied files. File hashes detect changes to those files; they do not authenticate Shopify or Google as their source.

| Fictional SKU | First snapshot | Second snapshot | Daily margin priority proxy in first snapshot |
| --- | --- | --- | ---: |
| `BOOK-A` | Channel disapproved; new | No longer observed | $24.00 |
| `MUG-B` | Storefront $18.00, channel $20.00; new | No longer observed | $7.00 |
| `LAMP-C` | Storefront quantity zero, channel in stock; new | Still ongoing | $1.60 |

The priority proxy equals modeled unit margin multiplied by historical 30-day units divided by 30. It orders operator attention. It is **not** estimated lost revenue, recovered revenue, conversion probability, or a causal effect of the incident. The second snapshot contains one active incident and two `resolved_observed` incidents; no fix is performed by the software.

## Use customer-authorized data

Start with customer-supplied, complete exports that conform to the [manifest and CSV contract](DATA_CONTRACT.md). Keep actual merchant files in `customer-data/`, which is Git-ignored. Do not put access tokens in issue reports or committed examples. The [Commerce Command runbook](COMMERCE_COMMAND.md) describes optional read-only API capture, local SQLite case handling, and the authorization work still required for a live pilot.

If an export is partial, stale, future-dated, or from another country or account, the engine rejects it rather than treating a missing offer as a confirmed incident or a disappeared incident as resolved. A human should review each recommendation in the appropriate platform before making a change, then capture a new complete snapshot to observe the result.

## Implementation and evidence boundary

- **Implemented:** deterministic snapshot comparison, incident IDs, JSON/CSV/HTML reports, offline verification, local operator desk, and read-only connector code.
- **Exercised here:** fictional fixture runs and mocked connector tests.
- **Not demonstrated here:** a merchant-authorized live capture, a verified platform correction, incremental sales, automated remediation, OAuth installation, or a hosted multi-tenant service.

For the connector's platform fields and authorization references, see [Commerce Command pilot architecture](COMMERCE_COMMAND.md). This guide and the example reports belong to the open-source repository; they do not depend on a2zsoc.com.
