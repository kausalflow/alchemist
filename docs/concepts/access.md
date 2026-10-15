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

## The access file (MVP)

In the MVP, groups come from `access.yaml` (`ALC_ACCESS`, default `$ALC_HOME/access.yaml`). The demo writes this one:

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

## Enforcement today and in the target design

| Rule | MVP | Target design |
| --- | --- | --- |
| Who is in which group | `access.yaml` | identity-provider groups (`alc-plant-<code>`, `alc-global`, `alc-ws-<area>`, `alc-catalog-admin`, `alc-admin`) |
| Which plants a run reads | checked in the tool layer and the SQL guard | per-run STS session policy, enforced by S3 |
| Who can open a run | checked on replay | checked by the API before issuing presigned URLs |
| Audit | `audit_events` table | same table, plus CloudTrail and Bedrock invocation logs |

The rules are the same in both; the target design moves enforcement from application code to AWS so the agent has no
way to widen it. See [MVP and target design](../design.md).
