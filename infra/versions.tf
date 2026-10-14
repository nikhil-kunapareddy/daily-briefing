terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

# Signs in with a named profile in ~/.aws/credentials (an IAM user with AmazonEC2FullAccess).
provider "aws" {
  region  = var.region
  profile = var.aws_profile

  default_tags {
    tags = { Project = "daily-briefing" }
  }
}
