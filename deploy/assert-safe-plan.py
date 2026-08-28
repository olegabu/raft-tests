#!/usr/bin/env python3
"""Refuse a terraform plan that would destroy or replace anything.

Reads `terraform show -json <planfile>` on stdin and exits non-zero if
any resource is being deleted. This is what makes an unattended apply
safe enough to run: the interactive "yes" prompt exists mostly to catch
a plan that quietly turns into a teardown, and that is exactly the case
this refuses.

It has already earned its place once. Adding a client produced a plan
reading "4 to add, 4 to destroy" -- not because of the client, but
because data.aws_ami.ubuntu tracks Canonical's latest image and they
had published a new one, so every instance in the fleet was going to be
replaced, taking its pushed binaries and data dir with it. That is now
pinned with ignore_changes, but the same class of surprise (a data
source moving under a resource) can come back through any provider
update.

Usage:
  terraform -chdir=deploy show -json tfplan | python3 deploy/assert-safe-plan.py
  terraform -chdir=deploy show -json tfplan | python3 deploy/assert-safe-plan.py --allow-destroy
"""
import json
import sys

allow_destroy = "--allow-destroy" in sys.argv

try:
    plan = json.load(sys.stdin)
except json.JSONDecodeError as exc:
    print(f"assert-safe-plan: could not parse the plan JSON: {exc}", file=sys.stderr)
    sys.exit(2)

created, updated, destroyed = [], [], []
for change in plan.get("resource_changes", []):
    actions = change.get("change", {}).get("actions", [])
    addr = change.get("address", "?")
    if "delete" in actions:
        destroyed.append((addr, actions))
    elif "create" in actions:
        created.append(addr)
    elif "update" in actions:
        updated.append(addr)

for addr in created:
    print(f"  create  {addr}")
for addr in updated:
    print(f"  update  {addr}")
for addr, actions in destroyed:
    kind = "replace" if "create" in actions else "destroy"
    print(f"  {kind} {addr}")

if destroyed and not allow_destroy:
    print(
        f"\nassert-safe-plan: REFUSING -- {len(destroyed)} resource(s) would be "
        f"destroyed or replaced.\nRun the apply interactively and read the plan "
        f"yourself, or pass --allow-destroy if this is a deliberate teardown.",
        file=sys.stderr,
    )
    sys.exit(1)

print(f"\nassert-safe-plan: OK -- {len(created)} to add, {len(updated)} to change, "
      f"{len(destroyed)} to destroy.")
