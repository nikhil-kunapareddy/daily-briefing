variable "tenancy_ocid" {
  description = "Tenancy OCID, from ~/.oci/config. Always Free resources live in the tenancy's home region."
  type        = string
}

variable "compartment_ocid" {
  description = "Compartment for all resources. Defaults to the tenancy root."
  type        = string
  default     = null
}

variable "region" {
  description = "Home region. Always Free A1 capacity exists only here."
  type        = string
  default     = "us-sanjose-1"
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

# Always Free allows up to 4 OCPU / 24 GB of A1 in total. 1 / 6 because the smaller shape
# finds capacity more often, and nothing heavy runs on the VM (models are remote, one agent at a time).
variable "instance_ocpus" {
  type    = number
  default = 1
}

variable "instance_memory_gbs" {
  type    = number
  default = 6
}

variable "boot_volume_gbs" {
  description = "Always Free includes 200 GB of block storage in total."
  type        = number
  default     = 50
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
