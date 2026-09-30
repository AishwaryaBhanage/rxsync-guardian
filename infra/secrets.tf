# Two secrets, created EMPTY. There is deliberately no
# aws_secretsmanager_secret_version here: the values are set afterwards with
# `aws secretsmanager put-secret-value`, so they never appear in this code or in
# terraform.tfstate. Until a value is set, the API answers 500 (see load_secrets
# in api/handler.py).

# The Anthropic API key the agent calls Claude with.
resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name        = "${var.name}/ANTHROPIC_API_KEY"
  description = "Anthropic API key for the RxSync Investigator Lambda."

  # Delete at once on destroy, so a later apply can reuse the name instead of
  # waiting out the default 30-day recovery window.
  recovery_window_in_days = 0
}

# The shared demo key the handler checks in the x-demo-key header.
resource "aws_secretsmanager_secret" "demo_key" {
  name                    = "${var.name}/DEMO_KEY"
  description             = "Shared demo key checked by the RxSync Investigator API."
  recovery_window_in_days = 0
}
