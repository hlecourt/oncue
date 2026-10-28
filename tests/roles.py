# SPDX-License-Identifier: GPL-3.0-or-later
import os
import time


def crash_once_then_wait(marker):
    if not os.path.exists(marker):
        with open(marker, "w") as handle:
            handle.write(str(os.getpid()))
        os._exit(3)
    with open(marker + ".restarted", "w") as handle:
        handle.write(str(os.getpid()))
    while True:
        time.sleep(1)
