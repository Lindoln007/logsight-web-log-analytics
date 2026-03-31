from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_http_methods

from .forms import LoginForm, ProfileUpdateForm, RegistrationForm


class UserLoginView(LoginView):
    authentication_form = LoginForm
    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        return self.get_redirect_url() or reverse_lazy("app-shell")


@require_http_methods(["GET", "POST"])
def register_view(request):
    if request.user.is_authenticated:
        return redirect("app-shell")

    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Your account has been created successfully.")
        return redirect("app-shell")

    return render(request, "accounts/register.html", {"form": form})


@require_http_methods(["POST"])
def logout_view(request):
    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect("accounts:login")


@login_required
@require_http_methods(["GET", "POST"])
def profile_view(request):
    form = ProfileUpdateForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Your profile has been updated.")
        return redirect("accounts:profile")

    return render(request, "accounts/profile.html", {"form": form})
