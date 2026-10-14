locals {
  compartment_id = coalesce(var.compartment_ocid, var.tenancy_ocid)
  name           = "daily-briefing"
}

# ---------------------------------------------------------------- lookups

data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

# Newest standard (not Minimal) Ubuntu 24.04 build for Arm.
data "oci_core_images" "ubuntu" {
  compartment_id           = local.compartment_id
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "24.04"
  shape                    = "VM.Standard.A1.Flex"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"

  filter {
    name   = "display_name"
    values = ["^Canonical-Ubuntu-24\\.04-aarch64-.*"]
    regex  = true
  }
}

# ---------------------------------------------------------------- network

resource "oci_core_vcn" "main" {
  compartment_id = local.compartment_id
  display_name   = "${local.name}-vcn"
  cidr_blocks    = ["10.0.0.0/16"]
  dns_label      = "briefing"
}

resource "oci_core_internet_gateway" "main" {
  compartment_id = local.compartment_id
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.name}-igw"
  enabled        = true
}

resource "oci_core_route_table" "public" {
  compartment_id = local.compartment_id
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.name}-public-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.main.id
  }
}

# Cloud firewall. The VM's own iptables also blocks 80/443 until first-boot.sh opens them.
resource "oci_core_security_list" "public" {
  compartment_id = local.compartment_id
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.name}-public-sl"

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
  }

  ingress_security_rules {
    description = "SSH"
    source      = var.ssh_allowed_cidr
    protocol    = "6"
    tcp_options {
      min = 22
      max = 22
    }
  }

  dynamic "ingress_security_rules" {
    for_each = { http = 80, https = 443 }
    content {
      description = upper(ingress_security_rules.key)
      source      = "0.0.0.0/0"
      protocol    = "6"
      tcp_options {
        min = ingress_security_rules.value
        max = ingress_security_rules.value
      }
    }
  }

  # Path MTU discovery.
  ingress_security_rules {
    source   = "0.0.0.0/0"
    protocol = "1"
    icmp_options {
      type = 3
      code = 4
    }
  }
}

resource "oci_core_subnet" "public" {
  compartment_id    = local.compartment_id
  vcn_id            = oci_core_vcn.main.id
  display_name      = "${local.name}-public-subnet"
  cidr_block        = "10.0.1.0/24"
  dns_label         = "public"
  route_table_id    = oci_core_route_table.public.id
  security_list_ids = [oci_core_security_list.public.id]
}

# ---------------------------------------------------------------- VM

resource "oci_core_instance" "vm" {
  compartment_id      = local.compartment_id
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[0].name
  display_name        = local.name
  shape               = "VM.Standard.A1.Flex"

  shape_config {
    ocpus         = var.instance_ocpus
    memory_in_gbs = var.instance_memory_gbs
  }

  source_details {
    source_type             = "image"
    source_id               = data.oci_core_images.ubuntu.images[0].id
    boot_volume_size_in_gbs = var.boot_volume_gbs
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.public.id
    assign_public_ip = true
    hostname_label   = "briefing"
  }

  # No secrets here: user_data is readable from inside the VM via the metadata endpoint.
  metadata = {
    ssh_authorized_keys = trimspace(file(pathexpand(var.ssh_public_key_path)))
    user_data = base64encode(templatefile("${path.module}/cloud-init.yaml.tftpl", {
      first_boot_b64   = base64encode(file("${path.module}/first-boot.sh"))
      repo_url         = var.repo_url
      duckdns_domain   = var.duckdns_domain
      node_major       = var.node_major
      openclaw_version = var.openclaw_version
      hermes_commit    = var.hermes_commit
    }))
  }

  # A newer image or an edited first-boot script must not silently replace the VM
  # (that would wipe the agents' state). Rebuild on purpose with:
  #   terraform apply -replace=oci_core_instance.vm
  lifecycle {
    ignore_changes = [source_details[0].source_id, metadata]
  }
}
