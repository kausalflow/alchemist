"""CloudFront: the SPA from S3 at /, and /api/* and /oauth2/* forwarded to the ALB on the same origin.

Serving both from one name keeps the ALB's OIDC session cookie first-party. The ALB answers only requests that carry
the origin-verify header CloudFront adds. Client-side routes are rewritten to /index.html by a CloudFront Function on
the SPA behaviour only, so API 403/404 responses are never masked.
"""

from __future__ import annotations

from aws_cdk import Duration
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudfront as cf
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_route53 as r53
from aws_cdk import aws_route53_targets as r53t
from aws_cdk import aws_s3_deployment as s3deploy
from constructs import Construct

from .config import EnvConfig
from .services import ORIGIN_HEADER, REPO_ROOT, Services
from .storage import Storage

SPA_REWRITE = """
function handler(event) {
  var r = event.request;
  if (r.uri.indexOf('.') === -1) { r.uri = '/index.html'; }
  return r;
}
"""


class Edge(Construct):
    def __init__(self, scope: Construct, id: str, *, cfg: EnvConfig, storage: Storage, services: Services) -> None:
        super().__init__(scope, id)
        d = cfg.domain
        api_origin = origins.HttpOrigin(
            d.api_origin_name, protocol_policy=cf.OriginProtocolPolicy.HTTPS_ONLY,
            origin_ssl_protocols=[cf.OriginSslPolicy.TLS_V1_2],
            custom_headers={ORIGIN_HEADER: services.origin_header_value},
            read_timeout=Duration.seconds(60), keepalive_timeout=Duration.seconds(60))
        api_behaviour = cf.BehaviorOptions(
            origin=api_origin, viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            allowed_methods=cf.AllowedMethods.ALLOW_ALL, cache_policy=cf.CachePolicy.CACHING_DISABLED,
            origin_request_policy=cf.OriginRequestPolicy.ALL_VIEWER, compress=False)
        self.distribution = cf.Distribution(
            self, "Distribution", comment=f"{cfg.prefix} web app and API",
            domain_names=[d.name],
            certificate=acm.Certificate.from_certificate_arn(self, "Cert", d.cloudfront_certificate_arn),
            minimum_protocol_version=cf.SecurityPolicyProtocol.TLS_V1_2_2021,
            default_root_object="index.html",
            default_behavior=cf.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(storage.web),
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cf.CachePolicy.CACHING_OPTIMIZED,
                response_headers_policy=cf.ResponseHeadersPolicy.SECURITY_HEADERS,
                function_associations=[cf.FunctionAssociation(
                    event_type=cf.FunctionEventType.VIEWER_REQUEST,
                    function=cf.Function(self, "SpaRewrite", code=cf.FunctionCode.from_inline(SPA_REWRITE),
                                         runtime=cf.FunctionRuntime.JS_2_0))]),
            additional_behaviors={"/api/*": api_behaviour, "/oauth2/*": api_behaviour},
            enable_logging=True, log_bucket=storage.logs, log_file_prefix="cloudfront/",
            http_version=cf.HttpVersion.HTTP2_AND_3)

        dist = REPO_ROOT / cfg.web_dist_path
        if (dist / "index.html").exists():
            s3deploy.BucketDeployment(self, "Spa", sources=[s3deploy.Source.asset(str(dist))],
                                      destination_bucket=storage.web, distribution=self.distribution,
                                      distribution_paths=["/index.html"], prune=True)
        self.spa_deployed = (dist / "index.html").exists()

        if d.hosted_zone_id and d.hosted_zone_name:
            zone = r53.HostedZone.from_hosted_zone_attributes(self, "Zone", hosted_zone_id=d.hosted_zone_id,
                                                              zone_name=d.hosted_zone_name)
            r53.ARecord(self, "AppRecord", zone=zone, record_name=d.name,
                        target=r53.RecordTarget.from_alias(r53t.CloudFrontTarget(self.distribution)))
            r53.ARecord(self, "OriginRecord", zone=zone, record_name=d.api_origin_name,
                        target=r53.RecordTarget.from_alias(r53t.LoadBalancerTarget(services.alb)))

