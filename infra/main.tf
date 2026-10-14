locals {
  name = "daily-briefing"
  # Ubuntu's image names say amd64 or arm64. Pick the one the instance type runs.
  arch = contains(data.aws_ec2_instance_type.vm.supported_architectures, "arm64") ? "arm64" : "amd64"
}

# ---------------------------------------------------------------- lookups

data "aws_ec2_instance_type" "vm" {
  instance_type = var.instance_type
}

# Not every zone offers every instance type.
data "aws_ec2_instance_type_offerings" "vm" {
  location_type = "availability-zone"

  filter {
    name   = "instance-type"
    values = [var.instance_type]
  }
}

# Newest Ubuntu 24.04 server image from Canonical.
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-${local.arch}-server-*"]
  }
}

# ---------------------------------------------------------------- network

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  tags                 = { Name = "${local.name}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-igw" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = sort(data.aws_ec2_instance_type_offerings.vm.locations)[0]
  map_public_ip_on_launch = true
  tags                    = { Name = "${local.name}-public" }

  # The zone list can change later. That must not move the subnet, and the VM with it.
  lifecycle {
    ignore_changes = [availability_zone]
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-public" }

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# Cloud firewall. Ubuntu's AWS image has no host firewall turned on, so this is the only one.
resource "aws_security_group" "vm" {
  name        = local.name
  description = "SSH, HTTP and HTTPS in; everything out"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = local.name }
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  security_group_id = aws_security_group.vm.id
  description       = "SSH"
  cidr_ipv4         = var.ssh_allowed_cidr
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
}

resource "aws_vpc_security_group_ingress_rule" "web" {
  for_each          = { http = 80, https = 443 }
  security_group_id = aws_security_group.vm.id
  description       = upper(each.key)
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = each.value
  to_port           = each.value
}

# Path MTU discovery (ICMP type 3, code 4).
resource "aws_vpc_security_group_ingress_rule" "pmtu" {
  security_group_id = aws_security_group.vm.id
  description       = "Path MTU discovery"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "icmp"
  from_port         = 3
  to_port           = 4
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.vm.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# ---------------------------------------------------------------- VM

resource "aws_key_pair" "vm" {
  key_name   = local.name
  public_key = trimspace(file(pathexpand(var.ssh_public_key_path)))
}

resource "aws_instance" "vm" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.vm.id]
  key_name               = aws_key_pair.vm.key_name

  root_block_device {
    volume_size = var.root_volume_gbs
    volume_type = "gp3"
    encrypted   = true
  }

  # Metadata endpoint: session tokens only (IMDSv2).
  metadata_options {
    http_tokens = "required"
  }

  # No secrets here: user_data is readable from inside the VM via the metadata endpoint.
  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    first_boot_b64   = base64encode(file("${path.module}/first-boot.sh"))
    repo_url         = var.repo_url
    duckdns_domain   = var.duckdns_domain
    node_major       = var.node_major
    openclaw_version = var.openclaw_version
    hermes_commit    = var.hermes_commit
  })

  tags = { Name = local.name }

  # A newer image or an edited first-boot script must not silently replace the VM
  # (that would wipe the agents' state). Rebuild on purpose with:
  #   terraform apply -replace=aws_instance.vm
  lifecycle {
    ignore_changes = [ami, user_data]
  }
}
