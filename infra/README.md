# infra

One Oracle Always Free Arm VM (A1.Flex, 2 OCPU / 12 GB, Ubuntu 24.04), its network, and its first-boot setup.
Cost: $0 if you stay inside Always Free.

| File | What it does |
|---|---|
| `versions.tf`, `variables.tf`, `main.tf`, `outputs.tf` | VCN, internet gateway, route table, firewall (22, 80, 443), subnet, VM |
| `cloud-init.yaml.tftpl` | Hands `first-boot.sh` and its settings to the VM. No secrets |
| `first-boot.sh` | Runs once as root: users, firewall, Node 24, gh, Caddy, DuckDNS updater, OpenClaw and Hermes (pinned), main-only checkout |
| `push-secrets.sh` | Run after boot from your laptop: copies key files in, points DuckDNS at the VM, signs the agents user in to GitHub |
| `apply-retry.sh` | Retries `terraform apply` while Oracle reports "Out of host capacity" |

## Rebuild from scratch

Needs: an Oracle account with an API key in `~/.oci/config`, Terraform 1.6+, an SSH key pair, and the three key files
(`agents.env`, `app.env`, `root.env`) in a `secrets/` folder next to the repo.

```
cd infra
cp terraform.tfvars.example terraform.tfvars      # fill in tenancy_ocid
terraform init
terraform plan                                     # expect: 6 to add
terraform apply                                    # or ./apply-retry.sh if Oracle has no capacity
./push-secrets.sh                                  # waits for first boot (~10 min), then copies keys in
```

Check the VM: `$(terraform output -raw ssh)`, then `tail /var/log/first-boot.log`.

## What lands where on the VM

| Path | Owner | Notes |
|---|---|---|
| `/srv/daily-briefing` | `app` | `main` only. Agents can read it (their prompts live here) but not write |
| `/var/lib/daily-briefing` | `app` | Product data (SQLite). Closed to agents |
| `/home/agents/work` | `agents` | One clone per agent goes here (set up from `agents/`) |
| `/home/agents/.env`, `/home/app/.env`, `/root/root.env` | each user, mode 600 | Keys, copied in by `push-secrets.sh` |

Caddy serves `https://<duckdns_domain>.duckdns.org` and forwards to `127.0.0.1:8000`.

## Safety notes

- Keys never go through Terraform or cloud-init: both end up readable (state file, VM metadata endpoint).
- Changing the image or `first-boot.sh` does not replace the VM. Rebuild on purpose: `terraform apply -replace=oci_core_instance.vm`.
- Terraform state is local (`terraform.tfstate`, git-ignored). Back it up.
