"""
Multivariate flags — unit and API tests for Variation management.

Covers: auto-creation of boolean variations, CRUD endpoints,
ownership isolation, and flag_type field.
"""

import pytest
from conftest import FeatureFlagFactory, VariationFactory, personal_project_for
from apps.flags.models import FeatureFlag, Variation
from apps.flags.services import FlagService

_service = FlagService()


# ---------------------------------------------------------------------------
# Model / service tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBooleanVariationAutoCreate:
    def test_boolean_flag_creates_true_false_variations(self, user):
        flag = _service.create_flag(project_key=personal_project_for(user).key, user=user, name="F", key="f1")
        names = set(flag.variations.values_list("name", flat=True))
        assert names == {"true", "false"}

    def test_boolean_flag_sets_off_variation(self, user):
        flag = _service.create_flag(project_key=personal_project_for(user).key, user=user, name="F", key="f2")
        assert flag.off_variation.value is False

    def test_boolean_flag_sets_fallthrough_variation(self, user):
        flag = _service.create_flag(project_key=personal_project_for(user).key, user=user, name="F", key="f3")
        assert flag.fallthrough_variation.value is True

    def test_multivariate_flag_does_not_auto_create_variations(self, user):
        flag = _service.create_flag(
            project_key=personal_project_for(user).key, user=user, name="F", key="f4",
            flag_type=FeatureFlag.FlagType.MULTIVARIATE,
        )
        assert flag.variations.count() == 0


@pytest.mark.django_db
class TestVariationService:
    def test_create_variation_links_to_flag(self, user, flag):
        v = _service.create_variation(
            project_key=flag.project.key, key=flag.key, user=user, name="red", value_type="string", value="red"
        )
        assert v.flag_id == flag.id
        assert v.value == "red"

    def test_update_variation_persists(self, user, flag):
        v = VariationFactory(flag=flag, name="v1", value_type="string", value="old")
        updated = _service.update_variation(
            project_key=flag.project.key, key=flag.key, user=user, variation_id=v.id, value="new"
        )
        v.refresh_from_db()
        assert updated.value == "new"
        assert v.value == "new"

    def test_delete_variation_removes_from_db(self, user, flag):
        v = VariationFactory(flag=flag)
        pk = v.pk
        _service.delete_variation(project_key=flag.project.key, key=flag.key, user=user, variation_id=v.id)
        assert not Variation.objects.filter(pk=pk).exists()

    def test_create_variation_raises_for_non_owner(self, flag):
        from conftest import UserFactory
        from apps.core.errors import APIError
        other = UserFactory()
        # Owner-scoped lookup: another user's flag is invisible (404), not a 403.
        with pytest.raises(APIError):
            _service.create_variation(
                project_key=flag.project.key, key=flag.key, user=other, name="x", value_type="boolean", value=True
            )


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestVariationListCreate:
    def test_list_returns_variations(self, base, auth_client, flag):
        VariationFactory(flag=flag, name="a", value_type="string", value="a")
        VariationFactory(flag=flag, name="b", value_type="string", value="b")
        resp = auth_client.get(f"{base}/{flag.key}/variations/")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_create_returns_201(self, base, auth_client, flag):
        resp = auth_client.post(
            f"{base}/{flag.key}/variations/",
            {"name": "red", "value_type": "string", "value": "red"},
            format="json",
        )
        assert resp.status_code == 201

    def test_create_persists_to_db(self, base, auth_client, flag):
        auth_client.post(
            f"{base}/{flag.key}/variations/",
            {"name": "red", "value_type": "string", "value": "red"},
            format="json",
        )
        assert Variation.objects.filter(flag=flag, name="red").exists()

    def test_list_another_users_flag_returns_404(self, base, auth_client):
        other_flag = FeatureFlagFactory()
        resp = auth_client.get(f"{base}/{other_flag.key}/variations/")
        assert resp.status_code == 404


@pytest.mark.django_db
class TestVariationDetail:
    def test_patch_updates_value(self, base, auth_client, flag):
        v = VariationFactory(flag=flag, name="v", value_type="string", value="old")
        resp = auth_client.patch(
            f"{base}/{flag.key}/variations/{v.id}/",
            {"value": "new"},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["value"] == "new"

    def test_delete_returns_204(self, base, auth_client, flag):
        v = VariationFactory(flag=flag, name="v", value_type="boolean", value=True)
        resp = auth_client.delete(f"{base}/{flag.key}/variations/{v.id}/")
        assert resp.status_code == 204

    def test_delete_removes_from_db(self, base, auth_client, flag):
        v = VariationFactory(flag=flag, name="v", value_type="boolean", value=True)
        auth_client.delete(f"{base}/{flag.key}/variations/{v.id}/")
        assert not Variation.objects.filter(pk=v.pk).exists()

    def test_patch_another_users_variation_returns_404(self, base, auth_client):
        other_flag = FeatureFlagFactory()
        v = VariationFactory(flag=other_flag)
        resp = auth_client.patch(
            f"{base}/{other_flag.key}/variations/{v.id}/",
            {"value": "x"},
            format="json",
        )
        assert resp.status_code == 404


@pytest.mark.django_db
class TestFlagTypeField:
    def test_flag_response_includes_flag_type(self, base, auth_client, flag):
        resp = auth_client.get(f"{base}/{flag.key}/")
        assert "flag_type" in resp.json()

    def test_flag_response_includes_variations(self, base, auth_client, flag):
        resp = auth_client.get(f"{base}/{flag.key}/")
        assert "variations" in resp.json()

    def test_create_multivariate_flag(self, base, auth_client):
        resp = auth_client.post(
            f"{base}/",
            {"name": "Theme", "key": "theme", "flag_type": "multivariate"},
            format="json",
        )
        assert resp.status_code == 201
        assert resp.json()["flag_type"] == "multivariate"

    def test_boolean_flag_has_two_auto_variations(self, base, auth_client):
        resp = auth_client.post(
            f"{base}/",
            {"name": "Toggle", "key": "toggle-x"},
            format="json",
        )
        assert resp.status_code == 201
        flag = FeatureFlag.objects.get(key="toggle-x")
        assert flag.variations.count() == 2
