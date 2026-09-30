# The API: one Lambda function behind a public function URL.

# Zip of infra/build/lambda/. Run scripts/build_lambda.sh first; plan fails
# if the folder is missing.
data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/build/lambda"
  output_path = "${path.module}/build/lambda.zip"
}

# Log group created up front, so it gets a 7-day retention and the function
# never needs logs:CreateLogGroup.
resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${var.name}-api"
  retention_in_days = 7
}

# The role the function runs as.
resource "aws_iam_role" "api" {
  name = "${var.name}-api"

  # Only the Lambda service can assume it.
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Least privilege: write to its own log group, read its two secrets. Nothing else.
# (No kms:Decrypt: the secrets use the AWS-managed aws/secretsmanager key.)
resource "aws_iam_role_policy" "api" {
  name = "logs-and-secrets"
  role = aws_iam_role.api.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "WriteOwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.api.arn}:*"
      },
      {
        Sid    = "ReadOwnSecrets"
        Effect = "Allow"
        Action = "secretsmanager:GetSecretValue"
        Resource = [
          aws_secretsmanager_secret.anthropic_api_key.arn,
          aws_secretsmanager_secret.demo_key.arn,
        ]
      },
    ]
  })
}

# The function itself: api/handler.py's handler(), with pandas from the AWS layer.
resource "aws_lambda_function" "api" {
  function_name    = "${var.name}-api"
  role             = aws_iam_role.api.arn
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  handler          = "api.handler.handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  layers           = [var.pandas_layer_arn]

  # An investigation is several model turns; 120 s leaves headroom over the ~10 s typical.
  timeout     = 120
  memory_size = 1024

  environment {
    variables = {
      # Where the packaged data and eval files sit inside the deployment package.
      RXSYNC_DATA_DIR  = "/var/task/data"
      RXSYNC_EVALS_DIR = "/var/task/evals"
      # ARNs only, never values: the handler fetches the values at cold start.
      ANTHROPIC_API_KEY_SECRET_ARN = aws_secretsmanager_secret.anthropic_api_key.arn
      DEMO_KEY_SECRET_ARN          = aws_secretsmanager_secret.demo_key.arn
    }
  }

  # The log group and permissions must exist before the first invocation.
  depends_on = [aws_cloudwatch_log_group.api, aws_iam_role_policy.api]
}

# Public HTTPS endpoint for the function. Auth NONE: the demo key is checked
# inside the handler. CORS lives here, not in the handler, and allows only the
# CloudFront site.
resource "aws_lambda_function_url" "api" {
  function_name      = aws_lambda_function.api.function_name
  authorization_type = "NONE"

  cors {
    allow_origins = ["https://${aws_cloudfront_distribution.web.domain_name}"]
    allow_methods = ["GET", "POST"]
    allow_headers = ["content-type", "x-demo-key"]
    max_age       = 600
  }
}

# Resource policy, part 1 of 2: anyone may call the function URL...
resource "aws_lambda_permission" "url_invoke_url" {
  statement_id           = "FunctionURLAllowPublicAccess"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.api.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

# ...part 2 of 2: and the URL may invoke the function — but only via the URL,
# not through the Invoke API. Both statements are required since October 2025.
resource "aws_lambda_permission" "url_invoke_function" {
  statement_id             = "FunctionURLInvokeAllowPublicAccess"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.api.function_name
  principal                = "*"
  invoked_via_function_url = true
}
