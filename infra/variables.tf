variable "region" {
  description = "AWS region for everything except CloudFront, which is global."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Prefix for resource names."
  type        = string
  default     = "rxsync-investigator"
}

variable "pandas_layer_arn" {
  # AWS SDK for pandas 3.17.1 (pandas 3.0.5, numpy 2.5.1), Python 3.12, x86_64.
  # Pinned rather than looked up so a new layer release can't change a deploy.
  # Found with:
  #   aws ssm get-parameters-by-path --region us-east-1 --recursive \
  #     --path /aws/service/aws-sdk-pandas
  description = "AWS-managed AWS SDK for pandas layer."
  type        = string
  default     = "arn:aws:lambda:us-east-1:336392948345:layer:AWSSDKPandas-Python312:31"
}
