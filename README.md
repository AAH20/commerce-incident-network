# Commerce Incident Network

**Find channel listing incidents, prioritize them with a transparent commerce-economics proxy, and observe whether they disappear in the next complete snapshot.** The first release is a read-only local CLI for a single USD merchant, country, and reporting context. It produces a review queue and a self-contained HTML dashboard from merchant-authorized snapshots. No store or ad-platform credentials are required for the demo; this release does **not** connect to live Shopify or Google accounts, subscribe to webhooks, or change listings.

```bash
PYTHONPATH=src python3 -m commerce_incident_network run fixtures/demo/day1 outputs/day1 --as-of 2026-09-24T12:00:00Z
PYTHONPATH=src python3 -m commerce_incident_network run fixtures/demo/day2 outputs/day2 --as-of 2026-09-25T12:00:00Z --previous outputs/day1/report.json
PYTHONPATH=src python3 -m commerce_incident_network verify fixtures/demo/day2 outputs/day2/report.json --previous outputs/day1/report.json
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Open `outputs/day2/dashboard.html`. The demo begins with a disapproved book, a price mismatch, and an availability mismatch. The second complete snapshot shows the book and price incident absent while the availability mismatch continues. These are **fictional records**, and `resolved_observed` is not a claim about why the issue disappeared or how much revenue it recovered.

## Why this exists

A merchant can see a product in the storefront while a sales channel sees something else. A channel diagnostic can tell the operator *what* is wrong, but an operator still has to join source and destination identities, decide what to handle first, assign the correction, and check that the next channel state reflects it. This repository implements the comparison and observation steps. It does not claim to replace platform diagnostics or act on their behalf.

```mermaid
flowchart LR
    subgraph Inputs[Customer-authorized complete snapshots]
        S[Storefront SKU, price, inventory, publication]
        C[Channel offer, price, availability, approval]
        M[Explicit SKU-to-offer mapping]
        E[Optional margin and 30-day units]
    end
    S --> V[Schema, scope and freshness validation]
    C --> V
    M --> V
    E --> V
    V --> D[Deterministic cross-system comparison]
    D --> I[Stable incident IDs]
    E --> P[Daily margin priority proxy]
    I --> P
    P --> Q[JSON, CSV and offline HTML queue]
    Q --> N[New complete snapshot]
    N --> R[New, ongoing or resolved-observed state]
```

```mermaid
stateDiagram-v2
    [*] --> New: Issue first observed
    New --> Ongoing: Same issue in later complete snapshot
    Ongoing --> Ongoing: Still present
    New --> ResolvedObserved: Absent in later complete snapshot
    Ongoing --> ResolvedObserved: Absent in later complete snapshot
    ResolvedObserved --> New: Appears again after resolution
```

## What the first release detects

| Incident | Signal | Operator next step |
| --- | --- | --- |
| Channel missing | Published mapped SKU absent from complete channel snapshot | Check feed and eligibility |
| Channel disapproved or pending | Channel approval field | Review the channel's issue details |
| Price mismatch | Storefront and channel USD prices differ | Check discounts and intended channel price |
| Availability mismatch | Published storefront stock state differs from channel | Check inventory policy and feed timing |
| Unpublished storefront item live in channel | Storefront unpublished, channel approved and in stock | Review publication and listing intent |
| Storefront missing | Mapped SKU absent from complete storefront snapshot | Check mapping and source export |

Every incident has a stable ID based on merchant, country, reporting context, content language, feed label, SKU, and kind. `--previous` compares only the same scope and an older snapshot. The engine refuses incomplete, future-dated, or stale snapshots so that a partial export cannot masquerade as a resolution. The output contains SHA-256 hashes of source files. `verify` recomputes the report from those same files; it does not authenticate the original platform.

The optional priority proxy is `unit_margin_usd × units_sold_30d ÷ 30`. It uses historical sales velocity to order work within a severity class. It is **not** lost sales, causal revenue recovery, a forecast, or a reliable estimate for low-volume products. Missing economics produces a blank proxy, never a fabricated zero. Price and availability differences may be intentional promotions, backorders, or propagation lag; a human should decide the action.

## Data contract and safe operation

See [the exact CSV and manifest contract](docs/DATA_CONTRACT.md). Put actual merchant data under `customer-data/`, which Git ignores, and keep outputs under `outputs/`. Never commit credentials, customer exports, or unredacted reports. CSV output neutralizes formula-leading cells; HTML escapes source values and has no external scripts.

The input contract is deliberately narrow: one merchant, one market, one reporting context, one content language and feed label, USD, and one explicit SKU-to-offer mapping. It does not silently match products by title, perform currency conversion, infer variant-level availability from aggregate inventory, or label a missing channel record as a platform disapproval. Approved merchant exports can be transformed into this contract after the merchant validates the mapping and snapshot completeness.

## Existing A2Z components

[Merchant Profit OS](https://github.com/AAH20/merchant-profit-os) supplies a separate catalog/economics reference. FeedOps uses the same **concept** of unit margin and sales velocity, but has no code-level or live data integration with that repository yet. [A2Z Commerce Cash Control](https://github.com/AAH20/a2z-commerce-cash-control) addresses order-to-ledger reconciliation; this project addresses storefront-to-channel listing incidents. Neither cash reconciliation nor channel incident resolution proves incremental sales. [AttentionOS Bench](https://github.com/AAH20/attentionos-bench) is a separate path for testing marketing effectiveness.

## Deployment sequence

1. **Consented export pilot:** one merchant, one channel account, and a named human reviewer. Validate a complete product mapping and compare the queue with the channel's own diagnostics. Record false positives and operator minutes.
2. **Read-only maintained adapters:** implement Shopify Admin GraphQL pagination and Google Merchant API processed-product pagination against merchant-granted credentials. Capture source timestamps, account IDs, scopes, pagination completion, API errors, and versioned field mappings. Google can lag after a product update, so treat the first snapshot after a fix as provisional. [Shopify API](https://shopify.dev/docs/api/admin-graphql/latest/queries/products) · [Google processed products](https://developers.google.com/merchant/api/reference/rest/products_v1/accounts.products/list)
3. **Scheduled review:** authenticated agency workspace, scoped tenant storage, notifications, owner assignment, and a human approval log. Begin with recommendations; any write-back needs explicit merchant authorization and a separate rollback design.
4. **Broader channels:** add channel-specific adapters only after measured pilot precision. OpenAI shopping-feed onboarding is currently limited to approved partners; this repository does not claim access or certification. [OpenAI commerce guide](https://developers.openai.com/commerce/guides/get-started)

The first pilot should measure incident precision against manually reviewed cases, time to detection and observed resolution, reviewer minutes per 100 products, and the fraction of mapped products covered by complete snapshots. For a commercial agency product, test whether multi-merchant operation reduces those minutes enough to pay for connector maintenance, support, and secure hosting. Growth through agencies is a distribution hypothesis, not a guaranteed network effect.

Licensed under MIT. See [LICENSE](LICENSE).
