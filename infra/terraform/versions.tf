terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # State is LOCAL by default so `terraform apply` works with zero
  # prerequisites. Once you have a dedicated state bucket + lock table,
  # uncomment and `terraform init -migrate-state` to move state to S3.
  #
  # backend "s3" {
  #   bucket       = "my-terraform-state"
  #   key          = "nl-regex-processor/terraform.tfstate"
  #   region       = "ap-southeast-2"
  #   encrypt      = true
  #   use_lockfile = true   # S3-native locking (Terraform >= 1.10)
  # }
}
