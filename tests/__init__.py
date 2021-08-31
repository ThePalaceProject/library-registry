import os
import sys

# Add the parent directory to the path so that import statements will work
# the same in tests as in code.
this_dir = os.path.abspath(os.path.dirname(__file__))
parent = os.path.split(this_dir)[0]
sys.path.insert(0, parent)

# Having problems with the database not being initialized? This module is
# being imported twice through two different paths. Uncomment these two lines
# and see where the second one is happening.
#
# from pdb import set_trace
# set_trace()

from testing import DatabaseTest, package_setup  # noqa: E402,F401

package_setup()
