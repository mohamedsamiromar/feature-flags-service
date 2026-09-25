"""
Joining an organization takes the invitee's consent.

Membership used to be created directly: an admin POSTed any user id and that
user was in — without being asked, and with sequential ids to probe which
accounts exist. Now an admin invites by username, and a membership exists
only once the invitee accepts.
"""

import pytest
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.audit.services import AuditService
from apps.organizations.models import Invitation, Membership, Role
from conftest import MembershipFactory, OrganizationFactory, UserFactory

ORG = "/api/v1/organizations"
MINE = "/api/v1/invitations"


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _member_of(org, role):
    user = UserFactory()
    MembershipFactory(organization=org, user=user, role=role)
    return user


@pytest.fixture
def org(db):
    return OrganizationFactory()


@pytest.fixture
def admin(org):
    return _member_of(org, Role.ADMIN)


@pytest.fixture
def owner(org):
    return _member_of(org, Role.OWNER)


@pytest.fixture
def invitee(db):
    return UserFactory()


def _invite(client, org, username, role=Role.MEMBER):
    return client.post(
        f"{ORG}/{org.slug}/invitations/",
        {"username": username, "role": role},
        format="json",
    )


def _is_member(org, user):
    return Membership.objects.filter(organization=org, user=user).exists()


@pytest.mark.django_db
class TestNoMembershipWithoutConsent:
    def test_direct_add_is_gone(self, org, admin, invitee):
        resp = _client(admin).post(
            f"{ORG}/{org.slug}/members/",
            {"user": invitee.id, "role": Role.MEMBER},
            format="json",
        )
        assert resp.status_code == 405
        assert not _is_member(org, invitee)

    def test_invite_alone_grants_nothing(self, org, admin, invitee):
        resp = _invite(_client(admin), org, invitee.username)
        assert resp.status_code == 201
        assert resp.data["status"] == Invitation.Status.PENDING
        assert not _is_member(org, invitee)
        # Still invisible to the invitee until they accept.
        assert _client(invitee).get(f"{ORG}/{org.slug}/").status_code == 404


@pytest.mark.django_db
class TestAcceptAndDecline:
    def test_invitee_sees_and_accepts(self, org, admin, invitee):
        _invite(_client(admin), org, invitee.username, Role.VIEWER)

        listed = _client(invitee).get(f"{MINE}/")
        assert listed.status_code == 200
        [invitation] = listed.data["results"]
        assert invitation["organization"] == org.slug

        resp = _client(invitee).post(f"{MINE}/{invitation['id']}/accept/")
        assert resp.status_code == 200
        assert Membership.objects.get(organization=org, user=invitee).role == Role.VIEWER
        assert Invitation.objects.get().status == Invitation.Status.ACCEPTED

    def test_decline_grants_nothing(self, org, admin, invitee):
        invitation = _invite(_client(admin), org, invitee.username).data
        resp = _client(invitee).post(f"{MINE}/{invitation['id']}/decline/")
        assert resp.status_code == 200
        assert not _is_member(org, invitee)
        assert Invitation.objects.get().status == Invitation.Status.DECLINED

    def test_only_the_invitee_can_accept(self, org, admin, invitee):
        invitation = _invite(_client(admin), org, invitee.username).data
        stranger = UserFactory()
        resp = _client(stranger).post(f"{MINE}/{invitation['id']}/accept/")
        assert resp.status_code == 404
        assert not _is_member(org, stranger)
        assert not _is_member(org, invitee)

    def test_the_inviter_cannot_accept_on_their_behalf(self, org, admin, invitee):
        invitation = _invite(_client(admin), org, invitee.username).data
        resp = _client(admin).post(f"{MINE}/{invitation['id']}/accept/")
        assert resp.status_code == 404
        assert not _is_member(org, invitee)

    def test_cannot_accept_twice(self, org, admin, invitee):
        invitation = _invite(_client(admin), org, invitee.username).data
        _client(invitee).post(f"{MINE}/{invitation['id']}/accept/")
        again = _client(invitee).post(f"{MINE}/{invitation['id']}/accept/")
        assert again.status_code == 404

    def test_other_users_invitations_are_not_listed(self, org, admin, invitee):
        _invite(_client(admin), org, invitee.username)
        assert _client(UserFactory()).get(f"{MINE}/").data["results"] == []


@pytest.mark.django_db
class TestInviting:
    def test_member_cannot_invite(self, org, invitee):
        member = _member_of(org, Role.MEMBER)
        resp = _invite(_client(member), org, invitee.username)
        assert resp.status_code == 403
        assert not Invitation.objects.exists()

    def test_non_member_cannot_invite(self, org, invitee):
        resp = _invite(_client(UserFactory()), org, invitee.username)
        assert resp.status_code == 404

    def test_unknown_username_is_404(self, org, admin):
        resp = _invite(_client(admin), org, "no-such-user")
        assert resp.status_code == 404

    def test_existing_member_cannot_be_invited(self, org, admin):
        member = _member_of(org, Role.MEMBER)
        resp = _invite(_client(admin), org, member.username)
        assert resp.status_code == 409

    def test_one_pending_invitation_per_user(self, org, admin, invitee):
        _invite(_client(admin), org, invitee.username)
        resp = _invite(_client(admin), org, invitee.username)
        assert resp.status_code == 409
        assert Invitation.objects.count() == 1

    def test_can_reinvite_after_decline(self, org, admin, invitee):
        first = _invite(_client(admin), org, invitee.username).data
        _client(invitee).post(f"{MINE}/{first['id']}/decline/")
        assert _invite(_client(admin), org, invitee.username).status_code == 201


@pytest.mark.django_db
class TestOwnerRankThroughInvitations:
    def test_admin_cannot_invite_an_owner(self, org, admin, invitee):
        resp = _invite(_client(admin), org, invitee.username, Role.OWNER)
        assert resp.status_code == 403
        assert not Invitation.objects.exists()

    def test_owner_can_invite_an_owner(self, org, owner, invitee):
        invitation = _invite(_client(owner), org, invitee.username, Role.OWNER).data
        _client(invitee).post(f"{MINE}/{invitation['id']}/accept/")
        assert Membership.objects.get(organization=org, user=invitee).role == Role.OWNER

    def test_invite_is_void_once_the_inviter_loses_the_right(self, org, owner, invitee):
        """The grant is the inviter's authority, so it is re-checked on accept.
        An owner demoted after inviting someone as owner must not still be
        able to hand that rank out."""
        invitation = _invite(_client(owner), org, invitee.username, Role.OWNER).data
        _member_of(org, Role.OWNER)  # keeps the last-owner guard out of the way
        Membership.objects.filter(organization=org, user=owner).update(role=Role.ADMIN)

        resp = _client(invitee).post(f"{MINE}/{invitation['id']}/accept/")

        assert resp.status_code == 409
        assert not _is_member(org, invitee)

    def test_invite_is_void_once_the_inviter_leaves(self, org, admin, invitee):
        invitation = _invite(_client(admin), org, invitee.username).data
        Membership.objects.filter(organization=org, user=admin).delete()

        resp = _client(invitee).post(f"{MINE}/{invitation['id']}/accept/")

        assert resp.status_code == 409
        assert not _is_member(org, invitee)


@pytest.mark.django_db
class TestRevoking:
    def test_admin_lists_and_revokes(self, org, admin, invitee):
        invitation = _invite(_client(admin), org, invitee.username).data
        listed = _client(admin).get(f"{ORG}/{org.slug}/invitations/")
        assert [i["id"] for i in listed.data] == [invitation["id"]]

        resp = _client(admin).delete(f"{ORG}/{org.slug}/invitations/{invitation['id']}/")
        assert resp.status_code == 204

        accept = _client(invitee).post(f"{MINE}/{invitation['id']}/accept/")
        assert accept.status_code == 404
        assert not _is_member(org, invitee)

    def test_member_cannot_list_or_revoke(self, org, admin, invitee):
        invitation = _invite(_client(admin), org, invitee.username).data
        member = _client(_member_of(org, Role.MEMBER))
        assert member.get(f"{ORG}/{org.slug}/invitations/").status_code == 403
        assert member.delete(
            f"{ORG}/{org.slug}/invitations/{invitation['id']}/"
        ).status_code == 403

    def test_admin_cannot_revoke_an_owner_invitation(self, org, owner, admin, invitee):
        invitation = _invite(_client(owner), org, invitee.username, Role.OWNER).data
        resp = _client(admin).delete(f"{ORG}/{org.slug}/invitations/{invitation['id']}/")
        assert resp.status_code == 403

    def test_cannot_revoke_another_orgs_invitation(self, org, admin, invitee):
        other_org = OrganizationFactory()
        other_owner = _member_of(other_org, Role.OWNER)
        foreign = _invite(_client(other_owner), other_org, invitee.username).data
        resp = _client(admin).delete(f"{ORG}/{org.slug}/invitations/{foreign['id']}/")
        assert resp.status_code == 404


@pytest.mark.django_db
class TestInvitationAudit:
    def test_membership_is_attributed_to_the_inviter(self, org, admin, invitee):
        """The trail answers "who granted this", not "who clicked accept"."""
        invitation = _invite(_client(admin), org, invitee.username).data
        _client(invitee).post(f"{MINE}/{invitation['id']}/accept/")

        grant = AuditLog.objects.get(entity_type="membership", action=AuditService.CREATE)
        assert grant.user == admin
        assert grant.new_value["user"] == invitee.id

    def test_each_step_is_audited(self, org, admin, invitee):
        invitation = _invite(_client(admin), org, invitee.username).data
        _client(invitee).post(f"{MINE}/{invitation['id']}/accept/")

        actions = {
            (log.action, log.user_id)
            for log in AuditLog.objects.filter(entity_type="invitation")
        }
        assert actions == {
            (AuditService.CREATE, admin.id),
            (AuditService.ACCEPT, invitee.id),
        }

    def test_refused_invite_is_not_audited(self, org, admin):
        member = _member_of(org, Role.MEMBER)
        _invite(_client(admin), org, member.username)  # 409
        assert not AuditLog.objects.filter(entity_type="invitation").exists()
