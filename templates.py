admin = """
<!doctype html>
<html lang="en">
<head>
<title>Library Registry</title>
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<link href=\"/static/library-registry-admin.css\" rel="stylesheet" />
</head>
<body>
  <script src=\"/static/library-registry-admin.js\"></script>
  <script>
    var registryAdmin = new LibraryRegistryAdmin({username: \"{{ username }}\"});
  </script>
</body>
</html>
"""
