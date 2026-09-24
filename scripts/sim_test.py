import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','loganalytics.settings')
import django
django.setup()
from django.contrib.auth import get_user_model
from logs.models import Dataset, Alert, Notification
from logs.services.simulator import simulate_error_spike
from logs.services.alerts import auto_check_alerts
User = get_user_model()
user = User.objects.first()
if not user:
    user = User.objects.create_user('sim_test_user', 'sim@test.local', 'password')
    print('Created user', user.pk)
else:
    print('Using existing user', user.pk)
# find or create a dataset for this user
dataset = Dataset.objects.filter(owner=user).first()
if not dataset:
    dataset = Dataset.objects.create(owner=user, name='sim-test-dataset')
    print('Created dataset', dataset.pk)
else:
    print('Using dataset', dataset.pk)
# Record counts before
alerts_before = Alert.objects.filter(rule__dataset=dataset).count()
notes_before = Notification.objects.filter(user=user).count()
print('Alerts before:', alerts_before, 'Notifications before:', notes_before)
# Run simulator error spike
res = simulate_error_spike(dataset.id, owner=user, count=35)
print('Simulator result generated:', res.get('generated'))
# Run auto check
check = auto_check_alerts(dataset.id, owner=user)
print('Auto-check status:', check.get('status'))
print('Auto-check results:', check.get('results'))
# Show alerts created
alerts_after = Alert.objects.filter(rule__dataset=dataset).order_by('-triggered_at')[:5]
print('Alerts created:')
for a in alerts_after:
    print(a.pk, a.alert_type, a.severity, a.message[:120])
# Show new notifications
notes_after = Notification.objects.filter(user=user).order_by('-created_at')[:5]
print('Recent notifications:')
for n in notes_after:
    print(n.pk, n.severity, n.notification_type, n.message[:120])
