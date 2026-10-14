# Decisions

Choices made while setting up the project, with the reason and how to change each one.
Newest section first.

## Infra (2026-10-14)

| # | Decision | Why | To change |
|---|---|---|---|
| 1 | **SSH (port 22) is open to any address.** Login is by key only; passwords are off on Oracle's Ubuntu image | A home IP changes, and a narrow rule would lock us out | Set `ssh_allowed_cidr = "<your-ip>/32"` in `infra/terraform.tfvars`, then `terraform apply` |
| 2 | **VM size: 1 OCPU / 6 GB** (A1.Flex, Arm). Changed 2026-10-14 from 2 / 12 | 2 / 12 failed 6 tries in a row with Oracle's "Out of host capacity"; smaller shapes find room more often. Peak use is ~2 GB: models run remotely and only one agent runs at a time. Resizing later needs capacity again, so treat it as fixed | `instance_ocpus` / `instance_memory_gbs` in `infra/variables.tf`, then `terraform apply` |
| 3 | **OpenClaw is installed system-wide by root** (`npm install -g`), not per user | The `agents` user can run it but can't modify or auto-update it, so the version stays pinned. Each user's state still lives in its own `~/.openclaw` | Change `openclaw_version` and re-run `first-boot.sh` on the VM |
| 4 | **Terraform never replaces the VM by itself.** A newer image or an edited `first-boot.sh` is ignored | Replacing the VM wipes the agents' state and logs. A rebuild has to be on purpose | `terraform apply -replace=oci_core_instance.vm` |
| 5 | **The app listens on `127.0.0.1:8000` and keeps its data in `/var/lib/daily-briefing/`** | Caddy forwards HTTPS to that port. The data folder is owned by `app` and closed to `agents`, so no agent can touch live data | Edit the Caddyfile and folder in `infra/first-boot.sh`, and the matching rule in `SPEC.md` |
