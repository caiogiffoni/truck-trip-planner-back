from django.urls import path

from trips import views

urlpatterns = [
    path("route/", views.route, name="trips-route"),
    path("plan/", views.plan, name="trips-plan"),
]
