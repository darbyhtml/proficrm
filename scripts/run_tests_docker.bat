@echo off
REM Запуск тестов Django в Docker (сервис web, БД PostgreSQL).
REM Использование из корня проекта:
REM   scripts\run_tests_docker.bat              — все тесты
REM   scripts\run_tests_docker.bat ui.tests.test_view_as   — только тесты view_as
cd /d "%~dp0\.."
set TEST_TARGET=%~1
if "%TEST_TARGET%"=="" (
  docker compose run --rm web sh -c "pip install -q -r /app/backend/requirements.txt && python manage.py test --verbosity=2"
) else (
  docker compose run --rm web sh -c "pip install -q -r /app/backend/requirements.txt && python manage.py test %TEST_TARGET% --verbosity=2"
)
