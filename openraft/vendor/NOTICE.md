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

Everything else is unmodified from upstream.
