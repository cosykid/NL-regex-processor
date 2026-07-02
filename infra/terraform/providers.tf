provider "aws" {
  region = var.region

  # Every resource created here is tagged with these automatically.
  default_tags {
    tags = merge(
      {
        Project     = var.name_prefix
        Environment = var.environment
        ManagedBy   = "terraform"
      },
      var.tags,
    )
  }
}
