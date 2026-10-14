# public_ip changes if the VM is stopped and started (a reboot keeps it).
# DuckDNS follows on its own; refresh this output with `terraform apply -refresh-only`.
output "public_ip" {
  value = aws_instance.vm.public_ip
}

output "ssh" {
  value = "ssh -i ~/.ssh/confer_vm ubuntu@${aws_instance.vm.public_ip}"
}

output "url" {
  value = "https://${var.duckdns_domain}.duckdns.org"
}

output "image" {
  description = "Image the VM was first built from."
  value       = aws_instance.vm.ami
}

output "zone" {
  value = aws_instance.vm.availability_zone
}
