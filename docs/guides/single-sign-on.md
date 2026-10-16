# Single sign-on

Alchemist has no login screen or passwords of its own. In AWS the load balancer (ALB) signs users in with the company
identity provider over OIDC and passes their claims to the API in a signed header (`ALC_AUTH=alb-oidc`). Access comes
from the user's groups ([Access](../concepts/access.md)). This page sets it up with OneLogin; any OIDC provider works
the same way, as long as its user-info endpoint returns the groups.

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
