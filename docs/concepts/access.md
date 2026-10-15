# Access model

Access has two separate axes, and Alchemist uses both:

- **Plant groups decide data.** A user belongs to one or more plant groups, or is global. Each registered dataset and
  each knowledge document is tagged with its plant, or `global` for shared data.
- **Workspaces decide projects.** Each functional area (Quality, Process engineering, ...) has a workspace. Members see
  and work on its projects.

Adding someone to a workspace never grants them another plant's data.

## Roles

| Role | Data they can read | Projects they can see | Can manage |
| --- | --- | --- | --- |
| Plant user | datasets of their assigned plants | projects in their workspaces | their own questions and runs |
| Global user | datasets of all plants | projects in their workspaces | their own questions and runs |
| Admin | everything | everything | users, groups, workspaces, catalog, model settings |

## What a run may read

When a question is asked, the run's plants are computed once and recorded in the manifest:

```text
run plants = user's plants (all plants if global or admin)
           ∩ plants the workspace covers (all plants if the workspace lists none)
           ∩ plants in the question's scope (if given)
```

The request is refused if the user is not a member of the workspace (admins excepted), if the scope names a plant the
user may not read, or if the intersection is empty. A question that spans plants runs only if the user may read every
plant involved, and the report lists which plants' data it used.

Replaying a run (and, in the target design, opening one) requires workspace membership and the right to read **every** plant the run used. Only the
person who asked, or an admin, can approve a plan.

## Groups from the identity provider

Users sign in with the company identity provider (OIDC). The API verifies the token and passes its claims to
`AccessModel.user_from_claims`, which turns group names into plants, workspaces and roles. The default group names are:

| Group | Grants |
| --- | --- |
| `alc-plant-<code>` | read the data of plant `<code>` |
| `alc-global` | read the data of all plants |
| `alc-ws-<area>` | membership of workspace `<area>` |
| `alc-catalog-admin` | manage the catalog |
| `alc-admin` | everything |

Groups for plants or workspaces that `access.yaml` does not define are ignored, so a stray group cannot invent a
plant. If your groups are named differently, or your provider sends group ids instead of names (Entra ID does), map
them in the `identity` section of `access.yaml`:

```yaml
identity:
  group_claim: groups                      # cognito:groups, roles, ...; or set ALC_GROUP_CLAIM
  user_claims: [preferred_username, email, sub]   # first one present is the user id
  plant_group: "alc-plant-{plant}"
  workspace_group: "alc-ws-{workspace}"
  global_group: alc-global
  admin_group: alc-admin
  catalog_admin_group: alc-catalog-admin
  group_aliases:                           # group id -> group name
    "7f3c0d6e-...": alc-plant-P01
```

To check a mapping, save the verified claims of a token as JSON and run `alchemist whoami --claims claims.json`; it
prints the user's plants, workspaces and roles, and the plants a run would read in each workspace. `ask`, `approve` and
`replay` also take `--claims` instead of `--user`.

## The access file

`access.yaml` (`ALC_ACCESS`, default `$ALC_HOME/access.yaml`, may be an `s3://` URI) lists the plants, the workspaces
and the plants each covers, and the optional `identity` mapping above. It may also list local users, which the CLI,
the demo and tests use instead of an identity provider. Leave `users` out in production, so identity-provider groups
are the only way in, or set `ALC_ACCESS_PROVIDER=oidc` (alias `alb-oidc`), which refuses local users even if listed.
Both providers are plugins ([Plugins](../guides/plugins.md)); another one can replace them. The demo writes this one:

```yaml
plants: [P01, P02]
workspaces:
  quality:
    plants: [P01, P02]
users:
  eng_p1:
    plants: [P01]
    workspaces: [quality]
  eng_p2:
    plants: [P02]
    workspaces: [quality]
  data_admin:
    admin: true
    global: true
    workspaces: [quality]
```

`eng_p1` can ask questions in `quality` about P01. The P02 historian dataset is not in the views of any run they start,
and a query naming `ds.historian_line5` is rejected.

## Where each rule is enforced

| Rule | How |
| --- | --- |
| Who is in which group | identity-provider groups mapped by `access.yaml`; local users as a fallback |
| Which plants a run reads | the tool layer and SQL guard, and, with `ALC_RUN_ROLE_ARN` set, the run's own STS session policy, enforced by S3 ([per-run credentials](../guides/s3.md#per-run-credentials)) |
| Who can open a run | checked on replay; the API checks it before issuing presigned URLs |
| Audit | `audit_events` table; with per-run credentials, CloudTrail shows each run's session, user and tags |

The rules are the same everywhere; per-run credentials move enforcement of data access from application code to AWS,
so the agent has no way to widen it. See [MVP and target design](../design.md).
