output "bucket_name" {
  description = "Name of the S3 bucket the app stores uploads + results in (S3_BUCKET)."
  value       = aws_s3_bucket.files.bucket
}

output "bucket_arn" {
  description = "ARN of the S3 bucket."
  value       = aws_s3_bucket.files.arn
}

output "region" {
  description = "Region the bucket lives in (S3_REGION / AWS_REGION)."
  value       = var.region
}

output "iam_policy_arn" {
  description = "ARN of the least-privilege bucket-access policy (attached to the role and dev user)."
  value       = aws_iam_policy.bucket_access.arn
}

output "app_role_arn" {
  description = "ARN of the runtime role the DEPLOYED app assumes (attach to the ECS task / EC2 instance / EKS service account)."
  value       = aws_iam_role.app.arn
}

output "app_instance_profile_name" {
  description = "Instance profile name to attach the role to an EC2 instance."
  value       = aws_iam_instance_profile.app.name
}

output "instance_public_ip" {
  description = "Elastic IP of the EC2 host (use as the DEPLOY_HOST secret). Null until ssh_public_key is set and the instance is applied."
  value       = try(aws_eip.app[0].public_ip, null)
}

output "ssh_command" {
  description = "Ready-made SSH command for the EC2 host."
  value       = try("ssh ubuntu@${aws_eip.app[0].public_ip}", null)
}

output "dev_access_key_id" {
  description = "Access key id for LOCAL development (only when create_dev_user = true). Read with: terraform output -raw dev_access_key_id"
  value       = try(aws_iam_access_key.dev[0].id, null)
  sensitive   = true
}

output "dev_secret_access_key" {
  description = "Secret access key for LOCAL development (only when create_dev_user = true). Read with: terraform output -raw dev_secret_access_key"
  value       = try(aws_iam_access_key.dev[0].secret, null)
  sensitive   = true
}

# Convenience: the exact block to paste into backend/.env for local dev. The
# key VALUES are printed separately (sensitive) via the two outputs above.
output "dotenv_snippet" {
  description = "Lines to add to backend .env to point the app at this bucket."
  value       = <<-EOT
    STORAGE_BACKEND=s3
    S3_BUCKET=${aws_s3_bucket.files.bucket}
    S3_REGION=${var.region}
    AWS_REGION=${var.region}
    %{if var.create_dev_user~}
    AWS_ACCESS_KEY_ID=$(terraform output -raw dev_access_key_id)
    AWS_SECRET_ACCESS_KEY=$(terraform output -raw dev_secret_access_key)
    %{else~}
    # Deployed: no static keys — the app assumes ${aws_iam_role.app.arn}
    %{endif~}
  EOT
}
