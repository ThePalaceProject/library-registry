admin = """
<!doctype html>
<html lang="en">
<head>
<title>Library Registry</title>
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<link href=\"/static/registry-admin.css\" rel="stylesheet" />
<style>
  .error {
    color: #135772;
    font-family: sans-serif;
    margin-left: 30px
  }
</style>
</head>
<body>
  <p class="error" id="error1" style="font-weight:bold;font-size:x-large;margin-top:50px"></p>
  <p class="error" id="error2" style="font-size:medium;margin-top:10px"></p>
  <script src=\"/static/registry-admin.js\"></script>
  <script>
  try {
    var registryAdmin = new RegistryAdmin({username: \"{{ username }}\"});
    const elementsToRemove = document.getElementsByClassName("error");
    while(elementsToRemove.length > 0){
        elementsToRemove[0].parentNode.removeChild(elementsToRemove[0]);
      }
  } catch (e) {
    document.getElementById("error1").innerHTML = "We're having trouble displaying this page."
    document.getElementById("error2").innerHTML = "Contact your administrator, and ask them to check the console for more information."
    console.error("The following error occurred: ", e)
    console.warn("The CSS and/or JavaScript files for this page could not be found. Try running `npm install` in the library_registry repo.")
  }
  </script>
</body>
</html>
"""
