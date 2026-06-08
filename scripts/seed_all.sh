#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

python manage.py migrate
python manage.py seed_data
python manage.py create_teacher_accounts
python manage.py create_student_accounts
python manage.py ensure_superuser
