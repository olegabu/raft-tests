# Vendored code notice

The crates in this directory (`raft-kv-memstore`, `app-http`, `log-mem`,
`network-v2-http`, `sm-mem`, `types-kv`, plus the shared `utils/` helper) are
copied from
[databendlabs/openraft](https://github.com/databendlabs/openraft)
(`examples/`), licensed under MIT OR Apache-2.0 (see `LICENSE-MIT` /
`LICENSE-APACHE`).

Changes made from upstream:
- `openraft = { path = "../../openraft" }` swapped for the published
  crates.io dependency `openraft = "0.10.0-alpha.33"` (pinned to match the
  exact commit this was vendored from) in `raft-kv-memstore`, `app-http`,
  `log-mem`, `network-v2-http`, `sm-mem`.
- `sm-mem`: removed the `openraft-legacy` dependency and its
  `SnapshotReceiverFactory` impl (legacy v1-network snapshot support) — dead
  code here since this setup only uses `network-v2-http`.
- `app-http` and `network-v2-http`: their `reqwest` dependency switched from
  default features (`native-tls`, dynamically linking system OpenSSL) to
  `rustls-tls` (pure Rust, statically linked). The binary built on this dev
  machine linked against `libssl.so.1.1`, which the deploy target's Ubuntu
  22.04 doesn't ship (it defaults to OpenSSL 3.0) — rustls avoids that class
  of dev/deploy OpenSSL-version mismatch entirely.

Everything else is unmodified from upstream.
