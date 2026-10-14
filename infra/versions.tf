terraform {
  required_version = ">= 1.6"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "~> 9.9"
    }
  }
}

# Signs in with ~/.oci/config (profile DEFAULT): user, tenancy, fingerprint, key file.
provider "oci" {
  region              = var.region
  config_file_profile = "DEFAULT"
}
