from django.urls import path

from booking import views

urlpatterns = [
    path("health/", views.health_check, name="health"),
    path("api/retell/functions/", views.RetellFunctionDispatchView.as_view(), name="retell_functions"),
    path("api/event-types/", views.EventTypesListView.as_view(), name="event_types"),
]
