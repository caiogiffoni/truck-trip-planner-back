from django.urls import path

from trips import views

urlpatterns = [
    path("plan/", views.plan, name="trips-plan"),
]
