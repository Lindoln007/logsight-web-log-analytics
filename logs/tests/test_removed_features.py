from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class RemovedFeatureTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="feature-test",
            email="feature-test@example.com",
            password="N2ght!Owl-river-47",
        )
        self.client.force_login(self.user)

    def test_app_shell_does_not_show_removed_modules_or_role_gates(self):
        response = self.client.get(reverse("app-shell"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Live Ingestion")
        self.assertNotContains(response, "User Management")
        self.assertNotContains(response, "nav-admin-group")
        self.assertNotContains(response, "upload-restricted")
        self.assertNotContains(response, "LOGSIGHT_USER_ROLE")
        self.assertNotContains(response, "data-view=\"alerts\"")
        self.assertNotContains(response, "data-coming-soon=\"alerts\"")
        self.assertNotContains(response, "Alerts")

    def test_removed_modules_do_not_resolve_to_placeholders(self):
        for module in ("admin", "live-ingestion", "user-management", "alerts"):
            with self.subTest(module=module):
                response = self.client.get(
                    reverse("coming-soon"),
                    {"module": module},
                )
                self.assertEqual(response.status_code, 404)

    def test_alert_endpoints_are_not_registered(self):
        for url in (
            "/logs/datasets/1/alerts/",
            "/logs/datasets/1/alerts/check/",
            "/logs/datasets/1/alerts/history/",
            "/logs/datasets/1/alerts/auto/",
            "/logs/datasets/1/alerts/1/",
            "/logs/alerts/all-history/",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_removed_admin_and_simulator_endpoints_are_not_registered(self):
        for url in (
            "/admin/",
            "/logs/admin/users/",
            "/logs/invites/",
            "/logs/datasets/1/simulate/",
            "/logs/datasets/1/simulate/error-spike/",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_registration_is_not_invite_gated(self):
        self.client.logout()
        response = self.client.post(
            reverse("accounts:register"),
            {
                "username": "new-user",
                "email": "new-user@example.com",
                "password1": "N2ght!Owl-river-47",
                "password2": "N2ght!Owl-river-47",
            },
        )

        self.assertRedirects(response, reverse("app-shell"))
        self.assertTrue(
            get_user_model().objects.filter(username="new-user").exists()
        )

    def test_authenticated_user_can_upload_without_a_role(self):
        response = self.client.post(reverse("logs:upload"), {})

        self.assertEqual(response.status_code, 400)
