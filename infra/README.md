# Alchemist infrastructure

AWS CDK app (Python) that deploys Alchemist. See [Deploy to AWS](../docs/guides/deploy-aws.md) and the
[deployment contract](../docs/reference/deployment.md).

```bash
uv sync --extra dev
uv run pytest -q
npx aws-cdk@2 synth -c env=dev --quiet
npx aws-cdk@2 deploy -c env=dev        # after replacing the placeholders in config/dev.yaml
```
