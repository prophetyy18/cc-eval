# Module dependency graph

- MOD-payment: purchase pipeline, calls MOD-ledger
- MOD-ledger: accounting, calls MOD-session
- MOD-session: user sessions, calls MOD-gateway
- MOD-gateway: external API
- MOD-audit: audit trail, independent
