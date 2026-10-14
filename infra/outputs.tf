output "public_ip" {
  value = oci_core_instance.vm.public_ip
}

output "ssh" {
  value = "ssh -i ~/.ssh/confer_vm ubuntu@${oci_core_instance.vm.public_ip}"
}

output "url" {
  value = "https://${var.duckdns_domain}.duckdns.org"
}

output "image" {
  description = "Image the VM was first built from."
  value       = data.oci_core_images.ubuntu.images[0].display_name
}
