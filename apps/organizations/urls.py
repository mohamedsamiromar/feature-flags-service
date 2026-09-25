from rest_framework.routers import DefaultRouter

from apps.organizations.views import InvitationViewSet, OrganizationViewSet, ProjectViewSet

app_name = "organizations"

router = DefaultRouter()
router.register("organizations", OrganizationViewSet, basename="organizations")
router.register("projects", ProjectViewSet, basename="projects")
router.register("invitations", InvitationViewSet, basename="invitations")

urlpatterns = router.urls
