# Security and data handling

The local pilot may process merchant product and order data. Keep snapshots, reports, and the SQLite desk in private directories; the repository ignores `customer-data/` and `outputs/`. The desk requires its database file to be accessible only to its owner. Back up or delete those files according to the merchant's instructions.

The capture command reads credentials from `SHOPIFY_ADMIN_TOKEN` and `GOOGLE_MERCHANT_ACCESS_TOKEN` in its process environment. It does not write either token to a snapshot or report. Use a merchant-authorized, narrowly scoped token and rotate or revoke it when the pilot ends. Do not paste tokens into issues, terminal transcripts, or sample fixtures.

This project does not expose a network listener or perform platform writes. The static HTML is escaped, and CSV incident exports neutralize spreadsheet formulas. These measures do not make the local SQLite event trail immutable or authenticate a human label. Do not expose the desk through a reverse proxy or share one database across merchants.

If you find a vulnerability, avoid posting customer data or exploit details in a public issue. Use GitHub's private vulnerability reporting for this repository if available, or contact the maintainer privately through their GitHub profile.
