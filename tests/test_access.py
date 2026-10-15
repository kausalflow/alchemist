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
