# -*- coding: utf-8 -*-
"""Best-effort OS scheduling priority for the streaming hot-path threads."""
from __future__ import absolute_import

import os
import threading

# Modest, safe priority bump for the threads directly on the client-facing
# streaming hot path (segment cutting, pipeline management) - never the
# whole process, since this plugin shares its process with the enigma2 GUI
# and boosting that would starve it instead. Linux schedules each thread as
# its own target for os.setpriority when given its native tid, so this
# only affects the calling thread. A more negative value is higher
# priority; -5 is a deliberate, modest edge over the default (0), not an
# aggressive realtime claim. Silently a no-op if unprivileged
# (CAP_SYS_NICE) or unsupported (non-Linux dev machine) - a missed
# priority bump must never be treated as a startup failure.
STREAMING_THREAD_NICENESS = -5


def boost_current_thread_priority(niceness=STREAMING_THREAD_NICENESS):
    """Best-effort: raise the calling thread's OS scheduling priority.

    Call this as the first line of a thread's run/target function, from
    that thread itself.
    """
    try:
        tid = threading.get_native_id()
        os.setpriority(os.PRIO_PROCESS, tid, niceness)
    except Exception:
        pass
