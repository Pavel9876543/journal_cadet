#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"
KEEP_TEST_DB="${KEEP_TEST_DB:-0}"

keepdb_args=()
if [[ "$KEEP_TEST_DB" == "1" ]]; then
  keepdb_args+=(--keepdb)
fi

exec "$PYTHON_BIN" manage.py test \
  journal.tests.AdminDashboardTests \
  journal.tests.PasswordRecoveryViewTests \
  journal.tests.CacheConfigurationTests \
  journal.tests.PerformanceConfigurationTests \
  journal.tests.SeedDataCommandTests \
  journal.tests.ElementAssessmentWorkflowTests \
  journal.tests.SelectedAcademicYearExportTests \
  --settings=config.test_settings \
  "${keepdb_args[@]}" \
  "$@"
