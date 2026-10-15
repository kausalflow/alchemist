"""Security properties of the synthesized template. Each test names a rule from the technical design."""

import pytest
from aws_cdk.assertions import Match

from alchemist_infra.config import EnvConfig, load_config

from .conftest import dumps, find, synth


def _sg_id(resources, name):
    (k,) = [k for k, v in resources.items() if v["Type"] == "AWS::EC2::SecurityGroup" and name in k]
    return k


def _policies_of(resources, role_fragment):
    role = next(k for k, v in resources.items() if v["Type"] == "AWS::IAM::Role" and role_fragment in k)
    docs = [v["Properties"]["PolicyDocument"] for v in resources.values()
            if v["Type"] == "AWS::IAM::Policy" and {"Ref": role} in v["Properties"].get("Roles", [])]
    return role, dumps(docs)


def test_no_nat_gateway(template):
    template.resource_count_is("AWS::EC2::NatGateway", 0)


def test_sandbox_cannot_reach_bedrock_endpoint(resources):
    """The sandbox has no route to Bedrock: the Bedrock endpoint admits only app security groups."""
    sandbox, app_ep = _sg_id(resources, "SandboxSg"), _sg_id(resources, "AppEndpointSg")
    bedrock = next(v for k, v in resources.items() if v["Type"] == "AWS::EC2::VPCEndpoint" and "BedrockRuntime" in k)
    assert bedrock["Properties"]["SecurityGroupIds"] == [{"Fn::GetAtt": [app_ep, "GroupId"]}]
    sources = [v["Properties"].get("SourceSecurityGroupId") for v in resources.values()
               if v["Type"] == "AWS::EC2::SecurityGroupIngress"
               and v["Properties"]["GroupId"] == {"Fn::GetAtt": [app_ep, "GroupId"]}]
    assert sources and {"Fn::GetAtt": [sandbox, "GroupId"]} not in sources
    # Nor may the sandbox reach the database.
    db = _sg_id(resources, "DbSg")
    db_sources = [v["Properties"].get("SourceSecurityGroupId") for v in resources.values()
                  if v["Type"] == "AWS::EC2::SecurityGroupIngress"
                  and v["Properties"]["GroupId"] == {"Fn::GetAtt": [db, "GroupId"]}]
    assert {"Fn::GetAtt": [sandbox, "GroupId"]} not in db_sources


def test_only_orchestrator_reaches_the_kernel(resources):
    sandbox, orch = _sg_id(resources, "SandboxSg"), _sg_id(resources, "OrchestratorSg")
    ingress = [v["Properties"] for v in resources.values() if v["Type"] == "AWS::EC2::SecurityGroupIngress"
               and v["Properties"]["GroupId"] == {"Fn::GetAtt": [sandbox, "GroupId"]}]
    assert [i["SourceSecurityGroupId"] for i in ingress] == [{"Fn::GetAtt": [orch, "GroupId"]}]
    assert ingress[0]["FromPort"] == 8888


def test_sandbox_role_can_only_call_the_broker(resources):
    _, policy = _policies_of(resources, "SandboxRole")
    assert "lambda:InvokeFunction" in policy
    for forbidden in ("s3:", "bedrock:", "sts:", "secretsmanager:"):
        assert forbidden not in policy


def test_orchestrator_has_bedrock_but_no_lake(resources, dev_cfg):
    _, policy = _policies_of(resources, "OrchestratorRole")
    assert "bedrock:InvokeModel" in policy
    for b in dev_cfg.lake.buckets:
        assert b.name not in policy
    # Snapshots of raw rows in run folders are denied too.
    assert "/runs/*/inputs/*" in policy and "/runs/*/work/*" in policy


def test_run_role_is_assumed_only_by_the_broker(resources, dev_cfg):
    role = next(v for k, v in resources.items() if v["Type"] == "AWS::IAM::Role" and "RunRole" in k)
    trust = dumps(role["Properties"]["AssumeRolePolicyDocument"])
    assert "BrokerFunctionRole" in trust and "sts:TagSession" in trust
    assert role["Properties"]["MaxSessionDuration"] == 3600
    _, policy = _policies_of(resources, "RunRole")
    assert all(b.name in policy for b in dev_cfg.lake.buckets)
    assert "SEALED" in policy and '"Effect": "Deny"' in policy
    assert "bedrock:" not in policy


def test_only_run_writers_may_write_in_run_folders(resources):
    (policy,) = [v for k, v in resources.items() if v["Type"] == "AWS::S3::BucketPolicy" and "Workspace" in k]
    stmts = policy["Properties"]["PolicyDocument"]["Statement"]
    deny = next(s for s in stmts if s.get("Sid") == "OnlyRunWritersInRunFolders")
    assert deny["Effect"] == "Deny" and "s3:PutObject" in deny["Action"]
    writers = dumps(deny["Condition"]["ArnNotLike"]["aws:PrincipalArn"])
    for role in ("RunRole", "OrchestratorRole", "SealerRole"):
        assert role in writers
    assert "ApiRole" not in writers and "SandboxRole" not in writers


def test_buckets_are_encrypted_private_and_tls_only(resources):
    buckets = find(resources, "AWS::S3::Bucket")
    for k, b in buckets.items():
        p = b["Properties"]
        assert p["PublicAccessBlockConfiguration"]["BlockPublicPolicy"] is True
        if any(n in k for n in ("Workspace", "Knowledge", "BedrockLogs")):
            algo = p["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]["ServerSideEncryptionByDefault"]
            assert algo["SSEAlgorithm"] == "aws:kms"
    for p in find(resources, "AWS::S3::BucketPolicy").values():
        assert "aws:SecureTransport" in dumps(p)


def test_run_workflow_waits_on_plan_approval_and_always_seals(resources):
    (sm,) = find(resources, "AWS::StepFunctions::StateMachine").values()
    d = dumps(sm["Properties"]["DefinitionString"])
    assert "ecs:runTask.waitForTaskToken" in d and "$$.Task.Token" in d
    for state in ("StartSandbox", "Analyse", "Report", "StopSandbox", "Seal"):
        assert f'\\"{state}\\"' in d
    # Analyse failures still produce a report; nothing logs execution data (it holds the broker token).
    assert sm["Properties"]["LoggingConfiguration"]["IncludeExecutionData"] is False


def test_alb_requires_origin_header_and_oidc(template):
    template.has_resource_properties("AWS::ElasticLoadBalancingV2::Listener", {
        "Port": 443, "Protocol": "HTTPS",
        "DefaultActions": [Match.object_like({"Type": "fixed-response",
                                              "FixedResponseConfig": Match.object_like({"StatusCode": "403"})})]})
    template.has_resource_properties("AWS::ElasticLoadBalancingV2::ListenerRule", {
        "Conditions": [Match.object_like({"Field": "http-header", "HttpHeaderConfig": Match.object_like(
            {"HttpHeaderName": "X-Alchemist-Origin-Verify"})})],
        "Actions": Match.array_with([Match.object_like({"Type": "authenticate-oidc"}),
                                     Match.object_like({"Type": "forward"})])})


def test_cloudfront_routes_api_and_oauth_to_the_alb(template):
    template.has_resource_properties("AWS::CloudFront::Distribution", {"DistributionConfig": Match.object_like({
        "CacheBehaviors": Match.array_with([Match.object_like({"PathPattern": "/api/*"}),
                                            Match.object_like({"PathPattern": "/oauth2/*"})])})})


def test_aurora_serverless_v2_encrypted(template):
    template.has_resource_properties("AWS::RDS::DBCluster", {
        "Engine": "aurora-postgresql", "StorageEncrypted": True,
        "ServerlessV2ScalingConfiguration": {"MinCapacity": 0.5, "MaxCapacity": 4}})


def test_bedrock_endpoint_policy_lists_only_allowed_models(resources, dev_cfg):
    ep = next(v for k, v in resources.items() if v["Type"] == "AWS::EC2::VPCEndpoint" and "BedrockRuntime" in k)
    policy = dumps(ep["Properties"]["PolicyDocument"])
    for arn in dev_cfg.bedrock.allowed_model_arns:
        assert arn in policy
    assert '"Resource": "*"' not in policy


def test_api_gets_the_contract_environment(resources):
    api = next(v for k, v in resources.items() if v["Type"] == "AWS::ECS::TaskDefinition" and "ApiTask" in k)
    (c,) = api["Properties"]["ContainerDefinitions"]
    names = {e["Name"] for e in c["Environment"]} | {s["Name"] for s in c["Secrets"]}
    assert {"ALC_WORKSPACE", "ALC_KNOWLEDGE", "ALC_DB_HOST", "ALC_DB_USER", "ALC_DB_PASSWORD", "ALC_LEAD_MODEL",
            "ALC_STATE_MACHINE_ARN", "ALC_GROUP_CLAIM", "ALC_ALLOWED_PROVIDERS"} <= names
    assert c["Command"] == ["api", "--host", "0.0.0.0", "--port", "8080"]


def test_config_rejects_object_lock_without_versioning(dev_cfg):
    data = dev_cfg.model_dump()
    data["storage"].update(object_lock_governance_days=30, workspace_versioning=False)
    with pytest.raises(ValueError, match="versioning"):
        EnvConfig.model_validate(data)


def test_object_lock_and_existing_vpc_synthesize(dev_cfg):
    data = dev_cfg.model_dump()
    data["storage"].update(object_lock_governance_days=30, workspace_versioning=True)
    data["network"] = {"existing": {
        "vpc_id": "vpc-0123456789abcdef0", "vpc_cidr": "10.20.0.0/16", "availability_zones": ["eu-central-1a", "eu-central-1b"],
        "public_subnet_ids": ["subnet-00000000000000001", "subnet-00000000000000002"], "app_subnet_ids": ["subnet-00000000000000003", "subnet-00000000000000004"],
        "app_route_table_ids": ["rtb-00000000000000005", "rtb-00000000000000006"], "sandbox_subnet_ids": ["subnet-00000000000000007", "subnet-00000000000000008"],
        "sandbox_route_table_ids": ["rtb-00000000000000009", "rtb-0000000000000000a"], "data_subnet_ids": ["subnet-0000000000000000b", "subnet-0000000000000000c"]}}
    data["image"] = {"ecr_repository_arn": "arn:aws:ecr:eu-central-1:111111111111:repository/alchemist",
                     "digest": "sha256:" + "0" * 64}
    t = synth(EnvConfig.model_validate(data))
    t.resource_count_is("AWS::EC2::VPC", 0)
    t.has_resource_properties("AWS::S3::Bucket", {"ObjectLockEnabled": True})
    images = [c["Image"] for td in find(t.to_json()["Resources"], "AWS::ECS::TaskDefinition").values()
              for c in td["Properties"]["ContainerDefinitions"]]
    assert images and all(i["Fn::Join"][1][-1].endswith("/alchemist@sha256:" + "0" * 64) for i in images)


def test_prod_config_synthesizes_with_retained_data():
    t = synth(load_config("prod"))
    t.has_resource("AWS::RDS::DBCluster", {"DeletionPolicy": "Snapshot",
                                           "Properties": Match.object_like({"DeletionProtection": True})})
    t.has_resource("AWS::S3::Bucket", {"DeletionPolicy": "Retain"})
