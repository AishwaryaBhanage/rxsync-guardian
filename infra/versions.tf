# Providers and the region. State is local (terraform.tfstate in this folder,
# gitignored) — no backend block on purpose.

terraform {
  required_version = ">= 1.6"

  required_providers {
    # 6.x: needed for aws_lambda_permission.invoked_via_function_url, which
    # function URLs require since October 2025.
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    # Zips infra/build/lambda/ (made by scripts/build_lambda.sh) for the upload.
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.region

  # Every resource gets this tag, so the whole demo is easy to find in the console.
  default_tags {
    tags = {
      Project = var.name
    }
  }
}

# The account id, used to make the S3 bucket name globally unique.
data "aws_caller_identity" "current" {}
