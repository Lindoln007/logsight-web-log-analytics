"""Smoke tests for the server-rendered pages of the redesigned interface."""
from __future__ import annotations

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .helpers import make_user


class SignedOutPagesTests(TestCase):
    def test_auth_pages_render_with_the_design_system(self):
        for name in ("accounts:login", "accounts:register", "accounts:redeem", "accounts:password_reset",
                     "accounts:password_reset_done", "accounts:password_reset_complete"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)
            self.assertContains(response, "/css/logsight.css")
            self.assertNotContains(response, "tailwind")

    def test_login_shows_an_error_for_bad_credentials(self):
        response = self.client.post(reverse("accounts:login"), {"username": "nobody", "password": "wrong"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "notice-bad")

    def test_expired_reset_link_explains_itself(self):
        response = self.client.get(reverse("accounts:password_reset_confirm", args=["abc", "bad-token"]))
        self.assertContains(response, "This link no longer works")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_password_reset_sends_a_working_link(self):
        user = make_user("resetme")
        user.email = "resetme@example.com"
        user.set_password("old-password-123")
        user.save()

        response = self.client.post(reverse("accounts:password_reset"), {"email": "resetme@example.com"})
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Reset your LogSight password")
        link = [line for line in mail.outbox[0].body.splitlines() if "/accounts/reset/" in line][0]

        # Following the emailed link leads to the new-password form.
        response = self.client.get(link.strip(), follow=True)
        self.assertContains(response, "Choose a new password")
        response = self.client.post(
            response.request["PATH_INFO"],
            {"new_password1": "a-much-better-pass-9", "new_password2": "a-much-better-pass-9"},
        )
        self.assertRedirects(response, reverse("accounts:password_reset_complete"))
        self.assertTrue(self.client.login(username="resetme", password="a-much-better-pass-9"))

    def test_unknown_address_gets_the_not_found_page(self):
        response = self.client.get("/definitely/not/here/")
        self.assertEqual(response.status_code, 404)


class SignedInPagesTests(TestCase):
    def setUp(self):
        self.user = make_user()          # first user becomes admin
        self.client.force_login(self.user)

    def test_shell_contains_every_view_and_no_dataset_picker_per_page(self):
        response = self.client.get(reverse("app-shell"))
        self.assertEqual(response.status_code, 200)
        for view in ("overview", "datasets", "explore", "forecast", "anomalies", "security", "compare", "reports"):
            self.assertContains(response, f'data-view="{view}"')
        self.assertContains(response, 'id="ds-button"')            # one working-dataset switcher
        self.assertNotContains(response, "tailwind")

    def test_profile_page_updates_details(self):
        url = reverse("accounts:profile")
        self.assertContains(self.client.get(url), "Your details")
        response = self.client.post(url, {"username": "analyst", "email": "a@example.com", "first_name": "Ada", "last_name": "L"}, follow=True)
        self.assertContains(response, "Your profile has been updated.")
        self.assertContains(response, "Ada L")

    def test_planned_module_page(self):
        response = self.client.get(reverse("coming-soon"), {"module": "alerts"})
        self.assertContains(response, "Alerts is not available yet")
