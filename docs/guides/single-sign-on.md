# Single sign-on

Alchemist has no login screen or passwords of its own. In AWS the load balancer (ALB) signs users in with the company
identity provider over OIDC and passes their claims to the API in a signed header (`ALC_AUTH=alb-oidc`). Access comes
from the user's groups ([Access](../concepts/access.md)). This page sets it up with OneLogin; any OIDC provider works
the same way, as long as its user-info endpoint returns the groups. Outside AWS (on-prem, or another cloud), see
[Any OIDC provider, outside AWS](#any-oidc-provider-outside-aws).

## 1. Create the app in OneLogin

An administrator in OneLogin:

1. **Applications → Add App →** "OpenId Connect (OIDC)". Name it, for example, `Alchemist dev`.
2. **Configuration:** Redirect URI `https://<domain.name>/oauth2/idpresponse` (the user-facing name from
   `infra/config/<env>.yaml`).
3. **SSO:** Application Type *Web*. Copy the Client ID and Client Secret.
4. **Parameters:** set *Groups* to the source of the user's groups (OneLogin roles, or `MemberOf` for directory
   groups), with the multi-value output transform, so each group arrives as its own list item.
5. **Access:** add the roles that may use Alchemist; other users cannot sign in.

## 2. Name the groups

Create roles (or directory groups) named after the default convention, case-sensitive:

| Group | Grants |
| --- | --- |
| `alc-plant-<code>` | read plant `<code>`, e.g. `alc-plant-P01` |
| `alc-global` | read all plants |
| `alc-ws-<area>` | member of workspace `<area>` |
| `alc-catalog-admin` | manage the catalog |
| `alc-admin` | everything |

Existing groups can be reused instead: map each one to a role with `identity.group_aliases` in `access.yaml`, for
example `"Plant Leipzig Quality": alc-plant-P01`. Plants and workspaces must also be listed in `access.yaml`; groups
for anything it does not define are ignored.

## 3. Configure the stack

Store the client secret as a plain string in Secrets Manager, then fill `oidc` in `infra/config/<env>.yaml`
(`<subdomain>` is your OneLogin tenant):

```yaml
oidc:
  issuer: https://<subdomain>.onelogin.com/oidc/2
  authorization_endpoint: https://<subdomain>.onelogin.com/oidc/2/auth
  token_endpoint: https://<subdomain>.onelogin.com/oidc/2/token
  user_info_endpoint: https://<subdomain>.onelogin.com/oidc/2/me
  client_id: <client id>
  client_secret_name: alchemist-dev/oidc-client-secret
  scope: openid email profile groups
  group_claim: groups
```

Deploy as in [Deploy to AWS](deploy-aws.md). The stack sets `ALC_AUTH=alb-oidc` and `ALC_ALB_ARN`, so the API accepts
only tokens signed by its own load balancer.

## 4. Check a user

Save a user's claims as JSON (from OneLogin's user-info response, or the payload of the `x-amzn-oidc-data` header)
and run:

```bash
alchemist whoami --claims claims.json
```

It prints the user's plants, workspaces and roles. A user with no `alc-ws-*` group (and not `alc-admin`)
signs in but cannot see any project.

## Things to know

- The ALB reads claims from the user-info endpoint, not the ID token, so the groups must be returned there. OneLogin
  does this when the `groups` scope is requested.
- The ALB rejects sign-ins whose claims exceed 11 KB. Send only the groups Alchemist needs rather than every
  directory group.
- Group changes take effect at the next sign-in; the ALB session lasts `oidc.session_timeout_hours` (default 8).

## Any OIDC provider, outside AWS

Without an ALB, a sign-in proxy in front of the API does the sign-in: [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/)
as a sidecar or behind ingress-nginx, Traefik's forward auth, or your platform's gateway. It forwards the user's **ID
token** to Alchemist, which checks it itself (`ALC_AUTH=oidc`): the signature against the provider's published keys,
the issuer, the audience and the expiry. A request without a valid token is refused, so the proxy is not trusted
blindly, and the dev user picker is off.

```bash
ALC_AUTH=oidc
ALC_OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0   # Entra ID; Okta, Keycloak, Google work the same
ALC_OIDC_AUDIENCE=<client-id>                                         # the app registration's client id
ALC_ACCESS_PROVIDER=oidc                                              # users only from tokens, never from access.yaml
```

With oauth2-proxy, pass the ID token on: `--pass-authorization-header` (sidecar) or `--set-authorization-header` with
ingress-nginx's `auth-response-headers: Authorization` (see the Helm chart's `ingress.annotations`). If the proxy uses
another header, name it in `ALC_OIDC_TOKEN_HEADER`. The groups claim must be in the ID token: in Entra ID add the
`groups` optional claim (it sends group ids, so map them with `group_aliases` in `access.yaml`); in Keycloak add a
group-membership mapper to the client.

Groups map to plants, workspaces and roles exactly as above. Check a user's token with `alchemist whoami --claims`.

