# Decisions

Choices made while setting up the project, with the reason and how to change each one.
Newest section first.

## Infra: move to AWS (2026-10-14)

| # | Decision | Why | To change |
|---|---|---|---|
| 6 | **The VM runs on the AWS Free plan, not Oracle Always Free** | Oracle's only Always Free Arm shape returned "Out of host capacity" on all 42 tries over ~6 hours (6 at 2 OCPU / 12 GB, 36 at 1 / 6). `us-sanjose-1` has one availability domain, so there was nowhere else to try. The brief names the AWS Free plan as plan B. We switched after hours rather than the brief's "a day" to protect the 5-day schedule. The account's credits last until 2027-01-02 | The Oracle Terraform is in git history (commit `1ee3b1b`) |
| 7 | **VM size: `m7i-flex.large`** (2 vCPU / 8 GB, x86) | The size the brief names for plan B, and on the Free plan's instance list. About $2.50/day of credits, so they cover the build week and the review week | `instance_type` in `infra/terraform.tfvars`. A stop and start resizes it (a new public IP; DuckDNS follows). `t3.small` makes credits last ~4x longer |
| 8 | **Own VPC, not the account's default VPC** | Works the same in any account, including one whose default VPC was deleted | `infra/main.tf` |
| 9 | **No Elastic IP.** The VM keeps the public IP it boots with | Attaching one after boot changes the IP while first boot is still downloading packages. A reboot keeps the IP; only a stop and start changes it, and the DuckDNS cron follows within 5 minutes | Add an `aws_eip` in `infra/main.tf` |

## Infra (2026-10-14)

| # | Decision | Why | To change |
|---|---|---|---|
| 1 | **SSH (port 22) is open to any address.** Login is by key only; Ubuntu cloud images turn passwords off | A home IP changes, and a narrow rule would lock us out | Set `ssh_allowed_cidr = "<your-ip>/32"` in `infra/terraform.tfvars`, then `terraform apply` |
| 2 | ~~VM size: 1 OCPU / 6 GB (Oracle A1.Flex, Arm)~~ **Replaced by #7** | Oracle had no capacity at either size | |
| 3 | **OpenClaw is installed system-wide by root** (`npm install -g`), not per user | The `agents` user can run it but can't modify or auto-update it, so the version stays pinned. Each user's state still lives in its own `~/.openclaw` | Change `openclaw_version` and re-run `first-boot.sh` on the VM |
| 4 | **Terraform never replaces the VM by itself.** A newer image or an edited `first-boot.sh` is ignored | Replacing the VM wipes the agents' state and logs. A rebuild has to be on purpose | `terraform apply -replace=aws_instance.vm` |
| 5 | **The app listens on `127.0.0.1:8000` and keeps its data in `/var/lib/daily-briefing/`** | Caddy forwards HTTPS to that port. The data folder is owned by `app` and closed to `agents`, so no agent can touch live data | Edit the Caddyfile and folder in `infra/first-boot.sh`, and the matching rule in `SPEC.md` |
