# The web app: a private S3 bucket, served only through CloudFront.

# Holds the web/dist build. Name includes the account id to be globally unique.
resource "aws_s3_bucket" "web" {
  bucket = "${var.name}-web-${data.aws_caller_identity.current.account_id}"

  # The contents are a rebuildable Vite build, so destroy may empty the bucket.
  force_destroy = true
}

# Block every form of public access; CloudFront reads through OAC instead.
resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Lets CloudFront sign its requests to the private bucket (the successor to OAI).
resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "${var.name}-web"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# AWS's managed "cache static files" policy, looked up by name.
data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

# The CDN in front of the bucket, on its default *.cloudfront.net HTTPS domain.
resource "aws_cloudfront_distribution" "web" {
  enabled             = true
  comment             = "${var.name} web app"
  default_root_object = "index.html"
  price_class         = "PriceClass_100" # North America and Europe edges only: cheapest

  origin {
    origin_id                = "web-bucket"
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }

  default_cache_behavior {
    target_origin_id       = "web-bucket"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_optimized.id
    compress               = true
  }

  # SPA fallback: a path with no object behind it serves index.html. Through OAC
  # a missing key comes back as 403 (no ListBucket), so both codes are mapped.
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }
  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

# Bucket policy: only this distribution may read objects.
resource "aws_s3_bucket_policy" "web" {
  bucket = aws_s3_bucket.web.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowCloudFrontRead"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.web.arn}/*"
      Condition = {
        StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.web.arn }
      }
    }]
  })

  # Applying a bucket policy while the public-access block is still being
  # created can race; order them.
  depends_on = [aws_s3_bucket_public_access_block.web]
}
