from rest_framework.routers import DefaultRouter

from apps.organizations.views import OrganizationViewSet, ProjectViewSet

app_name = "organizations"

router = DefaultRouter()
router.register("organizations", OrganizationViewSet, basename="organizations")
router.register("projects", ProjectViewSet, basename="projects")

urlpatterns = router.urls
