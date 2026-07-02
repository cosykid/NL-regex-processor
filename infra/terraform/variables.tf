variable "region" {
  description = "AWS region for the bucket. Defaults to Sydney to sit next to the Neon (ap-southeast-2) database."
  type        = string
  default     = "ap-southeast-2"
}

variable "name_prefix" {
  description = "Prefix for resource names (bucket, IAM role/user/policy)."
  type        = string
  default     = "nl-regex-processor"
}

variable "environment" {
  description = "Deployment environment (dev | staging | prod). Part of resource names and tags."
  type        = string
  default     = "dev"
}

variable "bucket_name" {
  description = "Explicit bucket name. Leave empty to auto-generate a globally-unique name from name_prefix + environment + a random suffix."
  type        = string
  default     = ""
}

variable "force_destroy" {
  description = "Allow `terraform destroy` to delete a non-empty bucket. Handy for dev, DANGEROUS for prod (leave false)."
  type        = bool
  default     = false
}

# --- data protection --------------------------------------------------------
variable "enable_versioning" {
  description = "Keep prior versions of overwritten/deleted objects (recommended)."
  type        = bool
  default     = true
}

variable "sse_algorithm" {
  description = "Server-side encryption: AES256 (SSE-S3, no key to manage) or aws:kms (set kms_key_arn)."
  type        = string
  default     = "AES256"
  validation {
    condition     = contains(["AES256", "aws:kms"], var.sse_algorithm)
    error_message = "sse_algorithm must be \"AES256\" or \"aws:kms\"."
  }
}

variable "kms_key_arn" {
  description = "KMS key ARN when sse_algorithm = aws:kms. Empty uses the AWS-managed aws/s3 key."
  type        = string
  default     = ""
}

# --- lifecycle --------------------------------------------------------------
variable "abort_incomplete_multipart_days" {
  description = "Delete parts of failed multipart uploads after N days (avoids paying for orphaned upload fragments)."
  type        = number
  default     = 7
}

variable "noncurrent_version_expiration_days" {
  description = "Delete non-current (superseded) object versions after N days. Only applies when enable_versioning = true."
  type        = number
  default     = 30
}

variable "uploads_expiration_days" {
  description = "Expire objects under uploads/ after N days. 0 = keep forever."
  type        = number
  default     = 0
}

variable "results_expiration_days" {
  description = "Expire objects under results/ after N days. 0 = keep forever."
  type        = number
  default     = 0
}

# --- browser access (only needed for direct browser<->S3 transfers) ---------
variable "cors_allowed_origins" {
  description = "Origins allowed to call the bucket directly from a browser (presigned PUT/GET). Empty = no CORS rule (the app proxies transfers through Django, so this is usually unnecessary)."
  type        = list(string)
  default     = []
}

# --- app identity -----------------------------------------------------------
variable "create_dev_user" {
  description = "Create an IAM user + access keys for LOCAL development. Set false for deployed environments, which should use the IAM role instead of static keys."
  type        = bool
  default     = true
}

variable "trusted_role_services" {
  description = "AWS service principals allowed to assume the app role (the compute your deployed app runs on). ECS tasks + EC2 by default; EKS uses the OIDC settings below instead."
  type        = list(string)
  default     = ["ecs-tasks.amazonaws.com", "ec2.amazonaws.com"]
}

variable "oidc_provider_arn" {
  description = "IAM OIDC provider ARN for web-identity federation (e.g. an EKS cluster's OIDC provider, or GitHub Actions). Empty disables OIDC trust."
  type        = string
  default     = ""
}

variable "oidc_subjects" {
  description = "Allowed `sub` claims for the OIDC trust (e.g. [\"system:serviceaccount:default:nl-regex\"] for IRSA, or [\"repo:org/repo:ref:refs/heads/main\"] for GitHub Actions)."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Extra tags merged into the provider default_tags."
  type        = map(string)
  default     = {}
}
