"""VPC with four tiers and VPC endpoints; no NAT gateway, so nothing but the ALB reaches the internet.

| Tier    | Runs                         | Reaches                                                 |
| ------- | ---------------------------- | ------------------------------------------------------- |
| public  | ALB                          | API tasks; the identity provider                        |
| app     | API, orchestrator, indexers  | Aurora, S3 gateway, all interface endpoints incl. Bedrock |
| sandbox | sandbox tasks                | S3 gateway, logs, ECR, secrets, Lambda (broker); not Bedrock |
| data    | Aurora                       | nothing outbound                                        |

The sandbox cannot reach Bedrock because the Bedrock (and STS, Step Functions, ECS, KMS) endpoints
only admit the app security groups, and there is no other route out of the VPC.
"""

from __future__ import annotations

from aws_cdk import Annotations, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from constructs import Construct

from .config import EnvConfig

APP, SANDBOX, DATA, PUBLIC = "app", "sandbox", "data", "public"


class Network(Construct):
    def __init__(self, scope: Construct, id: str, *, cfg: EnvConfig) -> None:
        super().__init__(scope, id)
        n = cfg.network
        if n.existing:
            e = n.existing
            self.vpc = ec2.Vpc.from_vpc_attributes(
                self, "Vpc", vpc_id=e.vpc_id, vpc_cidr_block=e.vpc_cidr, availability_zones=e.availability_zones,
                public_subnet_ids=e.public_subnet_ids,
                public_subnet_route_table_ids=e.public_route_table_ids or None,
                private_subnet_ids=e.app_subnet_ids, private_subnet_route_table_ids=e.app_route_table_ids,
                isolated_subnet_ids=e.sandbox_subnet_ids + e.data_subnet_ids,
            )
            self.app_subnets = ec2.SubnetSelection(subnets=[
                ec2.Subnet.from_subnet_attributes(self, f"App{i}", subnet_id=s, route_table_id=r,
                                                  availability_zone=az)
                for i, (s, r, az) in enumerate(zip(e.app_subnet_ids, e.app_route_table_ids, e.availability_zones))])
            self.sandbox_subnets = ec2.SubnetSelection(subnets=[
                ec2.Subnet.from_subnet_attributes(self, f"Sandbox{i}", subnet_id=s, route_table_id=r,
                                                  availability_zone=az)
                for i, (s, r, az) in enumerate(zip(e.sandbox_subnet_ids, e.sandbox_route_table_ids,
                                                   e.availability_zones))])
            self.data_subnets = ec2.SubnetSelection(subnets=[
                ec2.Subnet.from_subnet_attributes(self, f"Data{i}", subnet_id=s, availability_zone=az)
                for i, (s, az) in enumerate(zip(e.data_subnet_ids, e.availability_zones))])
            self.public_subnets = ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC)
        else:
            self.vpc = ec2.Vpc(
                self, "Vpc", ip_addresses=ec2.IpAddresses.cidr(n.cidr), nat_gateways=0,
                availability_zones=Stack.of(self).availability_zones,
                subnet_configuration=[
                    ec2.SubnetConfiguration(name=PUBLIC, subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=26),
                    ec2.SubnetConfiguration(name=APP, subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24),
                    ec2.SubnetConfiguration(name=SANDBOX, subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24),
                    ec2.SubnetConfiguration(name=DATA, subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=26),
                ],
                restrict_default_security_group=True,
            )
            self.vpc.add_flow_log("FlowLog")
            self.app_subnets = ec2.SubnetSelection(subnet_group_name=APP)
            self.sandbox_subnets = ec2.SubnetSelection(subnet_group_name=SANDBOX)
            self.data_subnets = ec2.SubnetSelection(subnet_group_name=DATA)
            self.public_subnets = ec2.SubnetSelection(subnet_group_name=PUBLIC)

        def sg(name: str, desc: str, outbound: bool = False) -> ec2.SecurityGroup:
            return ec2.SecurityGroup(self, name, vpc=self.vpc, description=desc, allow_all_outbound=outbound)

        # Workload security groups. Outbound HTTPS is open in the SG because the subnets have no route out of the
        # VPC: HTTPS can only land on the S3 gateway or an endpoint, and each endpoint decides who it admits.
        # The ALB needs the internet to reach the identity provider's token and user-info endpoints.
        self.alb_sg = sg("AlbSg", "ALB: HTTPS from CloudFront", outbound=True)
        Annotations.of(self.alb_sg).acknowledge_warning(
            "@aws-cdk/aws-ec2:ipv4IgnoreEgressRule", "ALB keeps all outbound for the identity provider")
        self.api_sg = sg("ApiSg", "API tasks")
        self.orchestrator_sg = sg("OrchestratorSg", "Orchestrator tasks (Bedrock, no lake)")
        self.sandbox_sg = sg("SandboxSg", "Sandbox tasks (lake via broker credentials, no Bedrock)")
        self.indexer_sg = sg("IndexerSg", "Indexer and sealer tasks")
        self.db_sg = sg("DbSg", "Aurora PostgreSQL")
        self.app_sgs = [self.api_sg, self.orchestrator_sg, self.indexer_sg]
        for g in self.app_sgs + [self.sandbox_sg]:
            g.add_egress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "S3 gateway and VPC endpoints only")
        for g in self.app_sgs:
            g.add_egress_rule(self.db_sg, ec2.Port.tcp(5432), "Aurora")
            self.db_sg.add_ingress_rule(g, ec2.Port.tcp(5432), "Alchemist services")
        self.orchestrator_sg.add_egress_rule(self.sandbox_sg, ec2.Port.tcp(cfg.commands.sandbox_port), "kernel")
        self.sandbox_sg.add_ingress_rule(self.orchestrator_sg, ec2.Port.tcp(cfg.commands.sandbox_port),
                                         "kernel gateway, orchestrator only")
        self.api_sg.add_ingress_rule(self.alb_sg, ec2.Port.tcp(cfg.commands.api_port), "ALB")

        # Endpoints every task may use (image pulls, logs, the broker) and endpoints only app tasks may use.
        self.shared_endpoint_sg = sg("SharedEndpointSg", "Endpoints for app and sandbox tasks")
        self.app_endpoint_sg = sg("AppEndpointSg", "Endpoints for app tasks only (Bedrock, STS, Step Functions)")
        for g in self.app_sgs + [self.sandbox_sg]:
            self.shared_endpoint_sg.add_ingress_rule(g, ec2.Port.tcp(443))
        for g in self.app_sgs:
            self.app_endpoint_sg.add_ingress_rule(g, ec2.Port.tcp(443))

        endpoint_subnets = self.app_subnets
        shared = {
            "EcrApi": ec2.InterfaceVpcEndpointAwsService.ECR,
            "EcrDocker": ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
            "Logs": ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
            "Lambda": ec2.InterfaceVpcEndpointAwsService.LAMBDA_,
            # Fargate fetches task secrets through the task's own network interface (sandbox: the pseudonym key).
            "SecretsManager": ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
        }
        app_only = {
            "BedrockRuntime": ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME,
            "Sts": ec2.InterfaceVpcEndpointAwsService.STS,
            "StepFunctions": ec2.InterfaceVpcEndpointAwsService.STEP_FUNCTIONS,
            "Ecs": ec2.InterfaceVpcEndpointAwsService.ECS,
            "Kms": ec2.InterfaceVpcEndpointAwsService.KMS,
            "Monitoring": ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_MONITORING,
        }
        self.endpoints: dict[str, ec2.InterfaceVpcEndpoint] = {}
        for name, svc in shared.items():
            self.endpoints[name] = self.vpc.add_interface_endpoint(
                name, service=svc, subnets=endpoint_subnets, security_groups=[self.shared_endpoint_sg],
                private_dns_enabled=True, open=False)
        for name, svc in app_only.items():
            self.endpoints[name] = self.vpc.add_interface_endpoint(
                name, service=svc, subnets=endpoint_subnets, security_groups=[self.app_endpoint_sg],
                private_dns_enabled=True, open=False)

        self.s3_endpoint = self.vpc.add_gateway_endpoint(
            "S3", service=ec2.GatewayVpcEndpointAwsService.S3, subnets=[self.app_subnets, self.sandbox_subnets])

    def limit_s3_endpoint(self, bucket_names: list[str], region: str) -> None:
        """S3 gateway endpoint policy: only Alchemist's buckets, the lake, and the ECR layer bucket."""
        resources = []
        for b in bucket_names:
            resources += [f"arn:aws:s3:::{b}", f"arn:aws:s3:::{b}/*"]
        resources.append(f"arn:aws:s3:::prod-{region}-starport-layer-bucket/*")
        self.s3_endpoint.add_to_policy(iam.PolicyStatement(
            principals=[iam.AnyPrincipal()], actions=["s3:*"], resources=resources))

    def limit_bedrock_endpoint(self, model_arns: list[str], guardrail_arns: list[str]) -> None:
        """Bedrock endpoint policy: only the allow-listed inference profiles and models, and the guardrail."""
        ep = self.endpoints["BedrockRuntime"]
        ep.add_to_policy(iam.PolicyStatement(
            principals=[iam.AnyPrincipal()],
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=model_arns))
        if guardrail_arns:
            ep.add_to_policy(iam.PolicyStatement(
                principals=[iam.AnyPrincipal()], actions=["bedrock:ApplyGuardrail"], resources=guardrail_arns))
