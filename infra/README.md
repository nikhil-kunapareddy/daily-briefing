# infra

One AWS VM (`m7i-flex.large`, 2 vCPU / 8 GB, Ubuntu 24.04), its network, and its first-boot setup.
Cost: paid from AWS Free plan credits (about $2.50/day with the public IP and 30 GB disk). Nothing is charged to a card.

Why AWS and not Oracle: see `DECISIONS.md` #6. The Oracle version is in git history (commit `1ee3b1b`).

| File | What it does |
|---|---|
| `versions.tf`, `variables.tf`, `main.tf`, `outputs.tf` | VPC, internet gateway, route table, subnet, security group (22, 80, 443), key pair, VM |
| `cloud-init.yaml.tftpl` | Hands `first-boot.sh` and its settings to the VM. No secrets |
| `first-boot.sh` | Runs once as root: users, Node 24, gh, Caddy, DuckDNS updater, OpenClaw and Hermes (pinned), main-only checkout |
| `push-secrets.sh` | Run after boot from your laptop: copies key files in, points DuckDNS at the VM, signs the agents user in to GitHub |

## Rebuild from scratch

Needs:
- An AWS account on the Free plan, with an IAM user that has `AmazonEC2FullAccess`.
  Its access key is saved as a CLI profile: `aws configure --profile confer`.
- Terraform 1.6+, and an SSH key pair at `~/.ssh/confer_vm`.
- The three key files (`agents.env`, `app.env`, `root.env`) in a `secrets/` folder next to the repo.

```
cd infra
terraform init
terraform plan                                     # expect: 13 to add
terraform apply
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
- Changing the image or `first-boot.sh` does not replace the VM. Rebuild on purpose: `terraform apply -replace=aws_instance.vm`.
- Reboot rather than stop: a stop and start gives the VM a new public IP. DuckDNS follows within 5 minutes;
  run `terraform apply -refresh-only` to update the `ssh` output.
- Terraform state is local (`terraform.tfstate`, git-ignored). Back it up.
