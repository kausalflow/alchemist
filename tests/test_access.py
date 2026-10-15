import pytest

from alchemist.access import AccessDenied, AccessModel

DATA = {"plants": ["P01", "P02"], "workspaces": {"quality": {"plants": ["P01", "P02"]}, "maint": {"plants": ["P02"]}},
        "users": {"a": {"plants": ["P01"], "workspaces": ["quality"]},
                  "g": {"global": True, "workspaces": ["quality", "maint"]},
                  "x": {"plants": ["P01"], "workspaces": ["maint"]}}}


def test_plant_user_reads_only_own_plants():
    m = AccessModel(DATA)
    assert m.run_plants(m.user("a"), "quality") == {"P01"}
    with pytest.raises(AccessDenied):
        m.run_plants(m.user("a"), "quality", ["P02"])


def test_workspace_membership_never_grants_data():
    m = AccessModel(DATA)
    with pytest.raises(AccessDenied):  # member of maint, but maint only covers P02
        m.run_plants(m.user("x"), "maint")
    with pytest.raises(AccessDenied):
        m.run_plants(m.user("a"), "maint")


def test_global_user_limited_by_workspace():
    m = AccessModel(DATA)
    assert m.run_plants(m.user("g"), "maint") == {"P02"}
    assert m.can_view_run(m.user("g"), "quality", ["P01", "P02"])
    assert not m.can_view_run(m.user("a"), "quality", ["P01", "P02"])


ID = {**DATA, "users": {}}


def test_claims_map_groups_to_plants_workspaces_and_roles():
    m = AccessModel(ID)
    u = m.user_from_claims({"preferred_username": "kim", "groups": ["alc-plant-P01", "alc-ws-quality", "other"]})
    assert (u.id, u.plants, u.workspaces, u.is_global, u.is_admin, u.source) == (
        "kim", {"P01"}, {"quality"}, False, False, "oidc")
    assert m.run_plants(u, "quality") == {"P01"}


def test_claims_ignore_unknown_plants_and_workspaces():
    m = AccessModel(ID)
    u = m.user_from_claims({"sub": "s1", "groups": "alc-plant-P09 alc-ws-finance alc-global"})
    assert u.plants == frozenset() and u.workspaces == frozenset() and u.is_global
    with pytest.raises(AccessDenied):
        m.run_plants(u, "quality")


def test_claims_with_custom_names_and_group_ids():
    m = AccessModel({**ID, "identity": {"group_claim": "roles", "plant_group": "Plant.{plant}.Reader",
                                        "workspace_group": "WS-{workspace}", "admin_group": "Alchemist.Admin",
                                        "group_aliases": {"7f3c-guid": "Plant.P02.Reader"}}})
    u = m.user_from_claims({"email": "a@x.com", "roles": ["Plant.P01.Reader", "7f3c-guid", "WS-maint"]})
    assert u.id == "a@x.com" and u.plants == {"P01", "P02"} and u.workspaces == {"maint"}
    assert m.run_plants(u, "maint") == {"P02"}
    assert m.user_from_claims({"email": "b", "roles": ["Alchemist.Admin"]}).is_admin


def test_claims_need_a_user_id():
    with pytest.raises(AccessDenied):
        AccessModel(ID).user_from_claims({"groups": ["alc-admin"]})


def test_oidc_provider_refuses_local_users(monkeypatch, tmp_path):
    import yaml

    from alchemist.config import Settings
    from alchemist.plugins import get_registry

    (tmp_path / "access.yaml").write_text(yaml.safe_dump(DATA))
    monkeypatch.setenv("ALC_GROUP_CLAIM", "cognito:groups")
    m = get_registry().access_providers.get("alb-oidc").load(Settings(access_file=str(tmp_path / "access.yaml")))
    with pytest.raises(AccessDenied):
        m.user("a")
    u = m.user_from_claims({"sub": "s", "cognito:groups": ["alc-plant-P02", "alc-ws-maint"]})
    assert m.run_plants(u, "maint") == {"P02"}
