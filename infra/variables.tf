variable "region" {
  description = "AWS region. Any region that offers the instance type works."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile Terraform signs in with (~/.aws/credentials)."
  type        = string
  default     = "confer"
}

# m7i-flex.large (2 vCPU / 8 GB, x86) is on the AWS Free plan's instance list and is the size the brief names
# for plan B. About $2.50/day of credits with the public IP and disk. t3.small (2 GB) makes credits last ~4x longer.
variable "instance_type" {
  type    = string
  default = "m7i-flex.large"
}

variable "root_volume_gbs" {
  type    = number
  default = 30
}

variable "ssh_public_key_path" {
  description = "Public key allowed to SSH in as the default `ubuntu` user."
  type        = string
  default     = "~/.ssh/confer_vm.pub"
}

variable "ssh_allowed_cidr" {
  description = "Who may reach port 22. Key-only login either way; narrow to your IP/32 if it is stable."
  type        = string
  default     = "0.0.0.0/0"
}

variable "repo_url" {
  description = "Public repo cloned onto the VM (read-only, over HTTPS)."
  type        = string
  default     = "https://github.com/nikhil-kunapareddy/daily-briefing.git"
}

variable "duckdns_domain" {
  description = "DuckDNS subdomain, without .duckdns.org. The token is pushed after boot, never put here."
  type        = string
  default     = "dailyresearch"
}

variable "node_major" {
  description = "Node.js major line from NodeSource. OpenClaw needs 24.16+ on the 24 line."
  type        = number
  default     = 24
}

variable "openclaw_version" {
  description = "Pinned OpenClaw npm version."
  type        = string
  default     = "2026.10.3"
}

variable "hermes_commit" {
  description = "Pinned Hermes Agent commit (tag v0.22.0)."
  type        = string
  default     = "e06df9c2eae6901f4c88f464e0ae6e753c0428b8"
}
