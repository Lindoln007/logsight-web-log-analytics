from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordResetForm,
    SetPasswordForm,
    UserCreationForm,
)


User = get_user_model()
# Classes from the LogSight design system (frontend/css/logsight.css).
INPUT_CLASSES = "input input-lg"


class StyledPasswordResetForm(PasswordResetForm):
    """Password-recovery request form using the LogSight input styles."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].widget.attrs.update(
            {"class": INPUT_CLASSES, "placeholder": "you@example.com", "autofocus": True}
        )


class StyledSetPasswordForm(SetPasswordForm):
    """New-password form using the LogSight input styles."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = INPUT_CLASSES


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(
            attrs={
                "autofocus": True,
                "autocomplete": "username",
                "placeholder": "Username",
                "class": INPUT_CLASSES,
            }
        )
    )
    password = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "placeholder": "Password",
                "class": INPUT_CLASSES,
            }
        ),
    )


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(
            attrs={
                "autocomplete": "email",
                "placeholder": "Email address",
                "class": INPUT_CLASSES,
            }
        ),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} {INPUT_CLASSES}".strip()

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if email and User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email address already exists.")
        return email


class ProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("username", "email", "first_name", "last_name")
        widgets = {
            "username": forms.TextInput(attrs={"autocomplete": "username"}),
            "email": forms.EmailInput(attrs={"autocomplete": "email"}),
            "first_name": forms.TextInput(attrs={"autocomplete": "given-name"}),
            "last_name": forms.TextInput(attrs={"autocomplete": "family-name"}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.get("instance")
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} {INPUT_CLASSES}".strip()

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        qs = User.objects.filter(username__iexact=username)
        if self.user is not None:
            qs = qs.exclude(pk=self.user.pk)
        if qs.exists():
            raise forms.ValidationError("That username is already taken.")
        return username

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        qs = User.objects.filter(email__iexact=email)
        if self.user is not None:
            qs = qs.exclude(pk=self.user.pk)
        if email and qs.exists():
            raise forms.ValidationError("That email address is already in use.")
        return email
