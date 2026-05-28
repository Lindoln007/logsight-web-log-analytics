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

    from logs.services.invites import validate_invite, redeem_invite

    token_str = request.GET.get("invite") or request.POST.get("invite_token", "")
    invite    = validate_invite(token_str) if token_str else None

    # No valid token → show invitation-only gate, no form.
    if not invite:
        return render(request, "accounts/register.html", {
            "invite_required": True,
            "invite_invalid":  bool(token_str),  # True = bad token, False = none at all
        })

    # Valid token → show the registration form.
    # If the token has an email pre-assigned, lock the field; otherwise let the invitee fill it.
    has_fixed_email = bool(invite.email)

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if has_fixed_email:
            form.data = form.data.copy()
            form.data["email"] = invite.email

        if form.is_valid():
            user = form.save(commit=False)
            if has_fixed_email:
                user.email = invite.email
            user.save()
            redeem_invite(token_str, user)
            login(request, user)
            messages.success(request, f"Welcome to LogSight! You've been added as {invite.role.capitalize()}.")
            return redirect("app-shell")
    else:
        form = RegistrationForm(initial={"email": invite.email or ""})

    if has_fixed_email:
        form.fields["email"].widget.attrs["readonly"] = True
        form.fields["email"].widget.attrs["class"] += " opacity-60 cursor-not-allowed"

    return render(request, "accounts/register.html", {
        "form":        form,
        "invite":      invite,
        "token_str":   token_str,
        "invite_valid": True,
    })


@require_http_methods(["GET", "POST"])
def redeem_code_view(request):
    """
    GET/POST /accounts/redeem/
    Validates an invite token entered on the login page and redirects
    to the registration form with the token in the query string.
    """
    if request.user.is_authenticated:
        return redirect("app-shell")

    from logs.services.invites import validate_invite

    error = None
    if request.method == "POST":
        code = request.POST.get("invite_code", "").strip()
        if not code:
            error = "Please enter your invite code."
        else:
            invite = validate_invite(code)
            if invite:
                return redirect(f"/accounts/register/?invite={code}")
            else:
                error = "That code is invalid or has already been used. Check with your admin."

    return render(request, "accounts/login.html", {"show_invite_form": True, "invite_error": error})


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
