admin = """
<!doctype html>
<html lang="en">
<head>
<title>Library Registry</title>
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<link href=\"/static/registry-admin.css\" rel="stylesheet" />
</head>
<body>
  <script src=\"/static/registry-admin.js\"></script>
  <script>
    var registryAdmin = new RegistryAdmin({username: \"{{ username }}\"});
  </script>
</body>
</html>
"""
