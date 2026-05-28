from django.urls import path

from . import views


app_name = "accounts"

urlpatterns = [
    path("login/", views.UserLoginView.as_view(), name="login"),
    path("register/", views.register_view, name="register"),
    path("redeem/", views.redeem_code_view, name="redeem"),
    path("logout/", views.logout_view, name="logout"),
    path("profile/", views.profile_view, name="profile"),
]
