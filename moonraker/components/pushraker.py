# diehard-pushraker: Moonraker's pushraker component. Kiln OS loads it
# from the plugin's moonraker/components; on Zmod and the FLSUN T1 the
# installer links it into Moonraker's components. It puts the plugin's
# python/ (pushraker and the packages it needs, tools/vendor.py) at the end
# of sys.path, so what Moonraker's own Python already has (Apprise, Pillow,
# Jinja2 on Zmod and the T1) wins, and hands over to the package.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os
import sys

_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.realpath(__file__)))), "python")
if _PY not in sys.path:
    sys.path.append(_PY)

from pushraker.component import load_component  # noqa: E402,F401
