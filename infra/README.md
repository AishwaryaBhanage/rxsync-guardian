# infra/

Minimal Terraform that deploys RxSync Investigator to AWS `us-east-1`: the API as
one Lambda function behind a public function URL, and the web app as a private S3
bucket served through CloudFront. State is local and gitignored.

| File | Creates |
| --- | --- |
| `secrets.tf` | Two empty Secrets Manager secrets: `ANTHROPIC_API_KEY`, `DEMO_KEY` |
| `lambda.tf` | Log group (7-day retention), IAM role + least-privilege policy, the function, its URL and the two URL permissions |
| `web.tf` | S3 bucket (all public access blocked), CloudFront with origin access control, bucket policy |
| `outputs.tf` | `function_url`, `site_url`, plus what `scripts/deploy_web.sh` needs |

## Deploy

```bash
scripts/build_lambda.sh                  # package -> infra/build/ (rerun whenever code or reports change)
terraform -chdir=infra init
terraform -chdir=infra apply

# Set the secret values out of band, so they never enter Terraform code or state.
echo "DEMO_KEY=$(openssl rand -hex 24)" >> .env
for k in ANTHROPIC_API_KEY DEMO_KEY; do
  aws secretsmanager put-secret-value --secret-id "rxsync-investigator/$k" \
    --secret-string "$(grep "^$k=" .env | cut -d= -f2-)" > /dev/null
done

scripts/deploy_web.sh                    # build web/ against the function URL, sync, invalidate
```

Until the secrets have values the API answers 500 to everything. The handler
re-reads them on each request until both load, so no redeploy is needed.

## Package size

Lambda allows 262 MB unzipped for the function and its layers together. The AWS SDK
for pandas layer takes 195.8 MB, which leaves 66.4 MB. The function package is
16.7 MB unzipped (5.3 MB zipped): the Anthropic SDK and its dependencies, the code,
the eval reports and the seed-42 CSVs. That leaves 49.7 MB of headroom.

## Cost controls

The function has **no reserved concurrency**. The account's Lambda limit is 10
concurrent executions, and AWS refuses any reservation that would leave fewer than 10
unreserved, so the setting fails at apply. Costs are capped by these four controls
instead. None of them is a hard limit on its own:

| Control | What it limits | Limitation |
| --- | --- | --- |
| **Demo key** | Every route rejects requests without the `x-demo-key` header, so a bare function URL costs almost nothing to hit | The key is compiled into the public JavaScript bundle. It keeps out scanners, not someone who has opened the site |
| **Anthropic console spend limit** | Model spend, which is nearly all the cost (~$0.0125 per investigation on Haiku). This is the only hard cap on it | Set it yourself in the Anthropic console; nothing here configures it |
| **AWS budget alarm** | Alerts you when AWS spend passes the threshold | An alert only; it stops nothing. It does not cover Anthropic charges |
| **120 s Lambda timeout** | Caps the duration and model turns of any single request (the agent also stops after 8 tool calls) | A per-request limit, not a total |

Fixed AWS cost is about $0.80 a month for the two secrets. Lambda, the function URL,
S3 and CloudFront are pay-per-use and near zero at demo traffic. The account's own
limit of 10 concurrent executions also bounds how many investigations can run at once.

## Tear down

```bash
terraform -chdir=infra destroy
```

This deletes both secrets immediately (no recovery window) and empties the bucket.
