# EC2 host for the Docker Compose stack (backend + worker + redis). The
# frontend lives on Vercel and proxies /api here via rewrites.
#
# Created only when `ssh_public_key` is set — leave it empty and this file is a
# no-op, so bucket-only applies keep working. The instance:
#
#   • is ARM (t4g.large by default) — CI builds linux/arm64 images to match.
#   • attaches the existing app instance profile, so boto3 AND Spark's S3A
#     connector pick up credentials from instance metadata (no static keys).
#   • allows containers to reach that metadata: Docker adds a network hop, so
#     the IMDS hop limit must be 2 (the default of 1 breaks credential lookup
#     inside containers with the default bridge network).
#   • installs Docker + the compose plugin via cloud-init and prepares
#     /opt/nl-regex, where the deploy workflow drops docker-compose.yml and
#     where you create the runtime .env (secrets never leave the box).

locals {
  create_instance = var.ssh_public_key != "" ? 1 : 0
}

# Latest Ubuntu 24.04 LTS for arm64, straight from Canonical.
data "aws_ami" "ubuntu_arm64" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-arm64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# Deploy into the default VPC — one box, no private-subnet topology needed.
data "aws_vpc" "default" {
  default = true
}

resource "aws_key_pair" "app" {
  count      = local.create_instance
  key_name   = "${var.name_prefix}-${var.environment}"
  public_key = var.ssh_public_key
}

resource "aws_security_group" "app" {
  count       = local.create_instance
  name        = "${var.name_prefix}-${var.environment}"
  description = "HTTP to the backend API; SSH for deploys."
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTP (gunicorn; Vercel /api rewrite proxies here)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "SSH (deploys + admin). Narrow ssh_ingress_cidr if you can."
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.ssh_ingress_cidr
  }

  egress {
    description = "All outbound (Neon, GHCR, S3, Anthropic API, apt)."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "app" {
  count                  = local.create_instance
  ami                    = data.aws_ami.ubuntu_arm64.id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.app[0].key_name
  vpc_security_group_ids = [aws_security_group.app[0].id]
  iam_instance_profile   = aws_iam_instance_profile.app.name

  # IMDSv2 only; hop limit 2 so processes INSIDE containers can fetch the
  # role credentials (Docker's bridge adds one hop).
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_size = var.root_volume_gb
    volume_type = "gp3"
  }

  # 2 GiB swap: 8 GiB RAM is comfortable for gunicorn + Celery + a local-mode
  # Spark JVM + Redis, but a heavy Spark stage can spike past it; swap turns
  # an OOM-kill into a slowdown.
  user_data = <<-EOT
    #!/bin/bash
    set -euxo pipefail
    curl -fsSL https://get.docker.com | sh
    usermod -aG docker ubuntu
    fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    mkdir -p /opt/nl-regex
    chown ubuntu:ubuntu /opt/nl-regex
  EOT

  tags = {
    Name = "${var.name_prefix}-${var.environment}"
  }
}

# Stable public IP so the DEPLOY_HOST secret survives stop/start cycles.
resource "aws_eip" "app" {
  count    = local.create_instance
  instance = aws_instance.app[0].id
  domain   = "vpc"
}
