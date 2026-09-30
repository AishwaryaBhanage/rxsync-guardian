output "function_url" {
  description = "Public HTTPS endpoint of the API."
  value       = aws_lambda_function_url.api.function_url
}

output "site_url" {
  description = "The web app."
  value       = "https://${aws_cloudfront_distribution.web.domain_name}"
}

# Read by scripts/deploy_web.sh.
output "web_bucket" {
  description = "S3 bucket holding the web build."
  value       = aws_s3_bucket.web.id
}

output "distribution_id" {
  description = "CloudFront distribution to invalidate after a web deploy."
  value       = aws_cloudfront_distribution.web.id
}

output "secret_names" {
  description = "Set these with `aws secretsmanager put-secret-value` after apply."
  value = {
    anthropic_api_key = aws_secretsmanager_secret.anthropic_api_key.name
    demo_key          = aws_secretsmanager_secret.demo_key.name
  }
}
