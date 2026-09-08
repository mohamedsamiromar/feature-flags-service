"""Tests for POST /api/v1/auth/register/.

The endpoint's job is not "create a user" — it is "create an account that can
immediately use the API". Most of what is asserted here is the tenancy the
signup provisions around the user, because a user without it is an account the
API gives no way to repair.
"""

import pytest
from django.conf import settings
from django.core.cache import cache
from rest_framework import status

from apps.accounts.models import User
from apps.environment.models import Environment
from apps.organizations.models import Membership, Organization, Project, Role

URL = "/api/v1/auth/register/"

VALID = {
    "username": "newcomer",
    "email": "newcomer@example.com",
    "password": "s3cur3-passphrase!",
}


@pytest.fixture(autouse=True)
def _reset_throttle():
    """Registration has its own 10/hour scope, enforced through the cache.

    Without clearing it, the eleventh registration in a session gets a 429 and
    the failure looks like a bug in whichever test happens to run eleventh.
    """
    cache.clear()
    yield
    cache.clear()


# ---------------------------------------------------------------------------
# The happy path and everything it provisions
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_register_creates_user(api_client):
    response = api_client.post(URL, VALID, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["user"]["username"] == "newcomer"
    assert response.data["user"]["email"] == "newcomer@example.com"
    assert User.objects.filter(username="newcomer").exists()


@pytest.mark.django_db
def test_password_is_hashed_not_stored_raw(api_client):
    api_client.post(URL, VALID, format="json")

    user = User.objects.get(username="newcomer")
    assert user.password != VALID["password"]
    assert user.check_password(VALID["password"])


@pytest.mark.django_db
def test_password_is_never_echoed_back(api_client):
    response = api_client.post(URL, VALID, format="json")

    assert "password" not in response.data["user"]
    assert VALID["password"] not in str(response.data)


@pytest.mark.django_db
def test_register_provisions_personal_organization_with_user_as_owner(api_client):
    response = api_client.post(URL, VALID, format="json")

    user = User.objects.get(username="newcomer")
    org = Organization.objects.get(slug=response.data["organization"]["slug"])
    membership = Membership.objects.get(organization=org, user=user)

    assert membership.role == Role.OWNER
    assert org.name == "Personal — newcomer"


@pytest.mark.django_db
def test_register_provisions_a_default_project(api_client):
    response = api_client.post(URL, VALID, format="json")

    project = Project.objects.get(key=response.data["project"]["key"])
    assert project.name == "Default"
    assert project.organization.slug == response.data["organization"]["slug"]


@pytest.mark.django_db
def test_register_provisions_all_three_environments(api_client):
    """Without an environment there is nothing to issue an SDK key against."""
    response = api_client.post(URL, VALID, format="json")

    project = Project.objects.get(key=response.data["project"]["key"])
    names = set(Environment.objects.filter(project=project).values_list("name", flat=True))

    assert names == {e.value for e in Environment.EnvironmentName}
    assert len(response.data["environments"]) == 3


@pytest.mark.django_db
def test_returned_tokens_authenticate_the_new_user(api_client):
    """The point of returning a JWT pair: signup leaves the caller able to work."""
    response = api_client.post(URL, VALID, format="json")
    access = response.data["access"]
    project_key = response.data["project"]["key"]

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    flags = api_client.get(f"/api/v1/projects/{project_key}/flags/")

    assert flags.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_new_account_can_create_a_flag_end_to_end(api_client):
    """The whole reason the endpoint provisions tenancy: two calls to a flag."""
    response = api_client.post(URL, VALID, format="json")
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
    project_key = response.data["project"]["key"]

    created = api_client.post(
        f"/api/v1/projects/{project_key}/flags/",
        {"name": "Dark Mode", "key": "dark-mode"},
        format="json",
    )

    assert created.status_code == status.HTTP_201_CREATED


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_duplicate_username_is_rejected(api_client):
    api_client.post(URL, VALID, format="json")

    response = api_client.post(URL, VALID, format="json")

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.data["code"] == -419


@pytest.mark.django_db
def test_weak_password_is_rejected(api_client):
    response = api_client.post(URL, {**VALID, "password": "123"}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "password" in response.data


@pytest.mark.django_db
def test_password_similar_to_username_is_rejected(api_client):
    """UserAttributeSimilarityValidator only runs if it is handed a user."""
    response = api_client.post(
        URL, {"username": "alexanderson", "password": "alexanderson"}, format="json"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "password" in response.data


@pytest.mark.django_db
def test_missing_username_is_rejected(api_client):
    response = api_client.post(URL, {"password": VALID["password"]}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "username" in response.data


@pytest.mark.django_db
def test_malformed_email_is_rejected(api_client):
    response = api_client.post(URL, {**VALID, "email": "not-an-email"}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "email" in response.data


@pytest.mark.django_db
def test_email_is_optional(api_client):
    response = api_client.post(
        URL, {"username": "noemail", "password": VALID["password"]}, format="json"
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["user"]["email"] == ""


@pytest.mark.django_db
def test_failed_registration_leaves_nothing_behind(api_client):
    """The provisioning is atomic — a half-built account is unusable."""
    api_client.post(URL, VALID, format="json")
    orgs_before = Organization.objects.count()
    projects_before = Project.objects.count()

    api_client.post(URL, VALID, format="json")

    assert Organization.objects.count() == orgs_before
    assert Project.objects.count() == projects_before


@pytest.mark.django_db
def test_registration_requires_no_authentication(api_client):
    """No credential exists yet — that is the entire point of the endpoint."""
    response = api_client.post(URL, VALID, format="json")

    assert response.status_code != status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# Tenancy isolation — a new account must not see anyone else's data
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_new_account_cannot_see_another_users_project(api_client, project):
    """`project` belongs to the `user` fixture, not the account we just made."""
    response = api_client.post(URL, VALID, format="json")
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")

    foreign = api_client.get(f"/api/v1/projects/{project.key}/flags/")

    assert foreign.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_two_registrations_get_separate_tenancies(api_client):
    first = api_client.post(URL, VALID, format="json")
    second = api_client.post(
        URL, {**VALID, "username": "second", "email": "second@example.com"}, format="json"
    )

    assert first.data["organization"]["slug"] != second.data["organization"]["slug"]
    assert first.data["project"]["key"] != second.data["project"]["key"]


@pytest.mark.django_db
def test_colliding_usernames_get_distinct_org_slugs(api_client):
    """Two distinct usernames can slugify to one base — `slugify` drops the dot.

    Both accounts are legitimate, so the second must not be refused; it takes a
    suffixed slug instead.
    """
    api_client.post(
        URL, {"username": "alice.b", "password": VALID["password"]}, format="json"
    )
    response = api_client.post(
        URL, {"username": "aliceb", "password": VALID["password"]}, format="json"
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert Organization.objects.filter(slug__startswith="aliceb").count() == 2
    assert Project.objects.filter(key__startswith="aliceb-default").count() == 2


@pytest.mark.django_db
def test_username_charset_is_validated(api_client):
    """`create_user` skips `full_clean`, so the model validator must be declared.

    A blank-ish username would otherwise slugify to nothing and send every such
    signup down the same fallback slug.
    """
    response = api_client.post(
        URL, {"username": "   ", "password": VALID["password"]}, format="json"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "username" in response.data


# ---------------------------------------------------------------------------
# Throttling
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_registration_is_throttled(api_client):
    """Anonymous and row-creating, so it gets a scope tighter than `anon`.

    Asserted against the real configured rate. Overriding `DEFAULT_THROTTLE_RATES`
    through the `settings` fixture would not take: `SimpleRateThrottle` binds
    `THROTTLE_RATES` to the rates dict at class-definition time, so it keeps the
    object it captured at import.
    """
    rate = int(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["registration"].split("/")[0])

    for i in range(rate):
        ok = api_client.post(
            URL, {"username": f"burst{i}", "password": VALID["password"]}, format="json"
        )
        assert ok.status_code == status.HTTP_201_CREATED, f"request {i} was rejected"

    blocked = api_client.post(
        URL, {"username": "burst-extra", "password": VALID["password"]}, format="json"
    )
    assert blocked.status_code == status.HTTP_429_TOO_MANY_REQUESTS
