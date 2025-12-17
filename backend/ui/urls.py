from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("companies/", views.company_list, name="company_list"),
    path("companies/duplicates/", views.company_duplicates, name="company_duplicates"),
    path("companies/export/", views.company_export, name="company_export"),
    path("companies/new/", views.company_create, name="company_create"),
    path("companies/<uuid:company_id>/", views.company_detail, name="company_detail"),
    path("companies/<uuid:company_id>/update/", views.company_update, name="company_update"),
    path("companies/<uuid:company_id>/contacts/new/", views.contact_create, name="contact_create"),
    path("contacts/<uuid:contact_id>/edit/", views.contact_edit, name="contact_edit"),
    path("companies/<uuid:company_id>/notes/add/", views.company_note_add, name="company_note_add"),
    path("companies/<uuid:company_id>/notes/<int:note_id>/delete/", views.company_note_delete, name="company_note_delete"),
    path("tasks/", views.task_list, name="task_list"),
    path("tasks/new/", views.task_create, name="task_create"),
    path("tasks/<uuid:task_id>/status/", views.task_set_status, name="task_set_status"),

    # Settings (admin only)
    path("settings/", views.settings_dashboard, name="settings_dashboard"),
    path("settings/branches/", views.settings_branches, name="settings_branches"),
    path("settings/branches/new/", views.settings_branch_create, name="settings_branch_create"),
    path("settings/branches/<int:branch_id>/edit/", views.settings_branch_edit, name="settings_branch_edit"),
    path("settings/users/", views.settings_users, name="settings_users"),
    path("settings/users/new/", views.settings_user_create, name="settings_user_create"),
    path("settings/users/<int:user_id>/edit/", views.settings_user_edit, name="settings_user_edit"),
    path("settings/dicts/", views.settings_dicts, name="settings_dicts"),
    path("settings/dicts/company-status/new/", views.settings_company_status_create, name="settings_company_status_create"),
    path("settings/dicts/company-sphere/new/", views.settings_company_sphere_create, name="settings_company_sphere_create"),
    path("settings/dicts/task-type/new/", views.settings_task_type_create, name="settings_task_type_create"),
    path("settings/activity/", views.settings_activity, name="settings_activity"),
    path("settings/import/", views.settings_import, name="settings_import"),
    path("settings/company-columns/", views.settings_company_columns, name="settings_company_columns"),
    path("settings/security/", views.settings_security, name="settings_security"),
]


