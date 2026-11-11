"""PA-22: the app connects with a least-privilege login over verified TLS; only the migrate task holds the owner."""

import json

from .conftest import find


def _secret_id(resources, fragment):
    (k,) = [k for k, v in resources.items() if v["Type"] in ("AWS::SecretsManager::Secret",
                                                             "AWS::SecretsManager::SecretTargetAttachment")
            and fragment in k]
    return k


def _containers(resources):
    for k, td in find(resources, "AWS::ECS::TaskDefinition").items():
        yield k, td["Properties"]["ContainerDefinitions"][0]


def test_only_the_migrate_task_gets_the_owner_login(resources):
    owner = _secret_id(resources, "ClusterSecretAttachment")
    app = _secret_id(resources, "AppLogin")
    seen = set()
    for k, c in _containers(resources):
        secrets = {s["Name"]: json.dumps(s["ValueFrom"]) for s in c.get("Secrets", [])}
        if "ALC_DB_PASSWORD" not in secrets:
            continue
        seen.add(k)
        assert app in secrets["ALC_DB_PASSWORD"] and app in secrets["ALC_DB_USER"], k
        if "MigrateTask" in k:
            assert owner in secrets["ALC_DB_OWNER_PASSWORD"]
            assert c["Command"] == ["db", "migrate", "--owner-user", "alchemist_admin"]
        else:
            assert owner not in json.dumps(c["Secrets"]), k
    assert any("MigrateTask" in k for k in seen) and any("ApiTask" in k for k in seen)


def test_every_database_client_verifies_the_server_certificate(resources):
    clients = 0
    for k, c in _containers(resources):
        env = {e["Name"]: e["Value"] for e in c.get("Environment", [])}
        if "ALC_DB_HOST" in env:
            clients += 1
            assert env["ALC_DB_SSLMODE"] == "verify-full", k
            assert env["ALC_DB_SSLROOTCERT"] == "/etc/alchemist/certs/aws-rds-global-bundle.pem", k
    assert clients >= 4
    (pg,) = find(resources, "AWS::RDS::DBClusterParameterGroup").values()
    assert pg["Properties"]["Parameters"]["rds.force_ssl"] == "1"


def test_migrations_run_after_the_database_and_before_the_services(resources):
    (trigger_id, trigger) = next(iter(find(resources, "Custom::Trigger").items()))
    deps = trigger.get("DependsOn", [])
    assert any("DatabaseCluster" in d for d in deps)
    api = next(v for k, v in resources.items() if v["Type"] == "AWS::ECS::Service" and "ApiService" in k)
    assert trigger_id in api.get("DependsOn", [])
    # The trigger function may start only the migrate task, and reads no secret itself.
    fn_policy = next(json.dumps(v["Properties"]["PolicyDocument"]) for k, v in resources.items()
                     if v["Type"] == "AWS::IAM::Policy" and "MigrateTrigger" in k)
    assert "ecs:RunTask" in fn_policy and "MigrateTask" in fn_policy and "secretsmanager" not in fn_policy
