"""Python's clock moved to another day, for `python tests/run_all.py --clock DAY`.

run_all.py puts this folder on PYTHONPATH and the day in WORTHWATCH_TEST_DATE, and Python
imports this file by itself as each test process starts. time-machine (from the dev extra)
moves time.time, datetime.now, date.today and the rest by whole days, keeping the time of day,
and lets the clock tick. It never moves the monotonic clock, so timeouts still work. File times
on disk stay real.

A suite that gives its child process its own PYTHONPATH would drop this folder, so
subprocess.Popen is wrapped: a child given its own environment gets this run's PYTHONPATH
folders back in front of its own, and keeps WORTHWATCH_TEST_DATE.
"""
import os

DAY = os.environ.get("WORTHWATCH_TEST_DATE")

if DAY:
    import datetime
    import subprocess
    import time

    import time_machine

    days = (datetime.date.fromisoformat(DAY) - datetime.date.today()).days
    time_machine.travel(time.time() + days * 86400, tick=True).start()

    OURS = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    real_init = subprocess.Popen.__init__

    def init(self, *args, **kwargs):
        env = kwargs.get("env")
        if env is not None:
            theirs = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
            env = dict(env, PYTHONPATH=os.pathsep.join(OURS + [p for p in theirs if p not in OURS]))
            env.setdefault("WORTHWATCH_TEST_DATE", DAY)
            kwargs["env"] = env
        real_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = init
