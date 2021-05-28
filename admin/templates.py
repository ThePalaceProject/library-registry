admin = """
<!doctype html>
<html lang="en">
<head>
<title>Library Registry</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link href="{{ admin_css }}" rel="stylesheet" />
</head>
<body>
  <script src="{{ admin_js }}"></script>
  <script>
    var registryAdmin = new LibraryRegistryAdmin({username: "{{ username }}"});
  </script>
</body>
</html>
"""
