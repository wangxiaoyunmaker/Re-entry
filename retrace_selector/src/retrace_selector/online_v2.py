"""Backward-compatible import facade for the organized online v2 package.

New integrations should import ``retrace_selector.online_inference_v2``.
"""

from .online_inference_v2 import *  # noqa: F401,F403
from .models import ValidationError
