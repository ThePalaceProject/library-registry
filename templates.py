admin = """
<!doctype html>
<html>
<head>
<title>Library Registry</title>
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<link href=\"/admin/static/registry-admin.css\" rel="stylesheet" />
</head>
<body>
  <script src=\"/admin/static/registry-admin.js\"></script>
  <script>
    var registryAdmin = new RegistryAdmin({
        csrfToken: \"{{ csrf_token }}\",
    });
  </script>
  <h1>It's the server</h1>
</body>
</html>
"""

# showCircEventsDownload: {{ "true" if show_circ_events_download else "false" }},
# settingUp: {{ "true" if setting_up else "false" }},
# email: \"{{ email }}\",
# roles: [{% for role in roles %}{"role": \"{{role.role}}\"{% if role.library %}, "library": \"{{role.library.short_name}}\"{% endif %} },{% endfor %}]
