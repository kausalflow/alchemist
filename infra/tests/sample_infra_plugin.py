"""An infrastructure plugin used by the tests: one export bucket and one task that writes to it."""

from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3

from alchemist_infra.config import TaskSize


def register(ctx):
    scope = ctx.scope("KilnExport")
    bucket = s3.Bucket(scope, "Exports", bucket_name="kiln-exports-test", encryption_key=ctx.storage.key,
                       block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True)
    ctx.allow_s3_bucket(bucket.bucket_name)
    role = iam.Role(scope, "Role", assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"))
    bucket.grant_put(role)
    ctx.services.add_task("KilnExport", "kiln-export", TaskSize(cpu=512, memory_mib=1024), role,
                          ["kiln", "export"], ctx.services.common_env, {})
