import os
import sys

# Add the parent directory to the path so that import statements will work
# the same in tests as in code.
this_dir = os.path.abspath(os.path.dirname(__file__))
parent = os.path.split(this_dir)[0]
sys.path.insert(0, parent)
