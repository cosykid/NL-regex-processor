# Least-privilege access to THIS bucket only. Shared by the deployed role and
# the local-dev user so both get exactly the same (minimal) permissions.
data "aws_iam_policy_document" "bucket_access" {
  # Object-level operations, scoped to the bucket's contents.
  statement {
    sid = "Objects"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${aws_s3_bucket.files.arn}/*"]
  }

  # Bucket-level operations the app needs (list for prefix scans, region lookup).
  statement {
    sid = "Bucket"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [aws_s3_bucket.files.arn]
  }
}

resource "aws_iam_policy" "bucket_access" {
  name        = "${var.name_prefix}-${var.environment}-s3-access"
  description = "Read/write access to the ${local.bucket_name} bucket for the NL-Regex app."
  policy      = data.aws_iam_policy_document.bucket_access.json
}

# --- Deployed identity: an assumable role -----------------------------------
# The app assumes this on AWS (ECS task role / EC2 instance profile / EKS IRSA).
# No long-lived keys — the default AWS credential chain picks it up at runtime.
data "aws_iam_policy_document" "assume_role" {
  # Trust the configured compute service principals (ECS/EC2 by default).
  dynamic "statement" {
    for_each = length(var.trusted_role_services) > 0 ? [1] : []
    content {
      sid     = "ServiceAssume"
      actions = ["sts:AssumeRole"]
      principals {
        type        = "Service"
        identifiers = var.trusted_role_services
      }
    }
  }

  # Optional web-identity federation (EKS IRSA, GitHub Actions OIDC, ...).
  dynamic "statement" {
    for_each = var.oidc_provider_arn != "" ? [1] : []
    content {
      sid     = "OidcAssume"
      actions = ["sts:AssumeRoleWithWebIdentity"]
      principals {
        type        = "Federated"
        identifiers = [var.oidc_provider_arn]
      }
      dynamic "condition" {
        for_each = length(var.oidc_subjects) > 0 ? [1] : []
        content {
          test     = "StringEquals"
          variable = "${replace(var.oidc_provider_arn, "/^.*oidc-provider//", "")}:sub"
          values   = var.oidc_subjects
        }
      }
    }
  }
}

resource "aws_iam_role" "app" {
  name               = "${var.name_prefix}-${var.environment}-app"
  description        = "Runtime role for the NL-Regex app (S3 access to ${local.bucket_name})."
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

resource "aws_iam_role_policy_attachment" "app" {
  role       = aws_iam_role.app.name
  policy_arn = aws_iam_policy.bucket_access.arn
}

# An instance profile so the role can attach directly to an EC2 instance.
resource "aws_iam_instance_profile" "app" {
  name = "${var.name_prefix}-${var.environment}-app"
  role = aws_iam_role.app.name
}

# --- Local-dev identity: an IAM user with static keys (optional) -------------
# Toggle off (create_dev_user = false) for anything deployed — prod should use
# the role above, not keys.
resource "aws_iam_user" "dev" {
  count = var.create_dev_user ? 1 : 0
  name  = "${var.name_prefix}-${var.environment}-dev"
  tags  = { Purpose = "local-development" }
}

resource "aws_iam_user_policy_attachment" "dev" {
  count      = var.create_dev_user ? 1 : 0
  user       = aws_iam_user.dev[0].name
  policy_arn = aws_iam_policy.bucket_access.arn
}

resource "aws_iam_access_key" "dev" {
  count = var.create_dev_user ? 1 : 0
  user  = aws_iam_user.dev[0].name
}
