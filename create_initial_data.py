"""
Run this script after migrations to create initial data:
  python manage.py shell < create_initial_data.py
"""
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from datetime import date
from apps.tax_forms.models import TaxYear
from apps.authentication.models import CustomUser

# ── Tax Year 2025/2026 ────────────────────────────────────────────────────────
# `year` follows the convention used by migration 0006_seed_previous_tax_years
# (and every `tax_year__year` filter in the app): it's the start year of the
# assessment period, so Y/A 2025/2026 (Apr 2025 - Mar 2026) is year=2025.
tax_year, created = TaxYear.objects.get_or_create(
    year=2025,
    defaults={
        'label': 'Y/A 2025/2026',
        'assessment_year_start': date(2025, 4, 1),
        'assessment_year_end': date(2026, 3, 31),
        'personal_relief': 1800000.00,
        'is_active': True,
    }
)
print(f"{'Created' if created else 'Exists'} Tax Year: {tax_year.label}")

# ── Super Admin ───────────────────────────────────────────────────────────────
SUPER_ADMINS = [
    {
        'email': 'bharatha@dpr.lk',
        'username': 'bharatha',
        'first_name': 'Bharatha',
        'last_name': 'Subasinghe',
        'password': 'Admin@12345',
    },
    {
        'email': 'madusanka@dpr.lk',
        'username': 'madusanka',
        'first_name': 'Madusanka',
        'last_name': 'Hewage',
        'password': 'Admin@12345',
    },
    {
        'email': 'manager.tax@dpr.lk',
        'username': 'manager.tax',
        'first_name': 'Tax',
        'last_name': 'Manager',
        'password': 'Admin@12345',
    },
]

for sa in SUPER_ADMINS:
    if not CustomUser.objects.filter(email=sa['email']).exists():
        user = CustomUser.objects.create_user(
            email=sa['email'],
            username=sa['username'],
            first_name=sa['first_name'],
            last_name=sa['last_name'],
            password=sa['password'],
            role='super_admin',
        )
        print(f"Created super admin: {user.email} / {sa['password']}")
    else:
        print(f"Exists: {sa['email']}")

print("\nSetup complete!")
print("All users use password: Admin@12345")
print("Remember to change default passwords in production!")
