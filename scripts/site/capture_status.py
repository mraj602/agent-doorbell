"""Capture real `doorbell status` output from the daemon, run against the mock Slack in tests/.

    DOORBELL_SITE_TMP=/tmp/doorbell-site python3 scripts/site/capture_status.py

Writes status-dormant.json and status-polling.json into $DOORBELL_SITE_TMP. Uses a temporary
DOORBELL_HOME and a fake token; nothing touches ~/.config/doorbell or a real Slack workspace.
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from conftest import Env, until  # noqa: E402

out = Path(os.environ.get("DOORBELL_SITE_TMP", "/tmp/doorbell-site"))
out.mkdir(parents=True, exist_ok=True)

e = Env(config={"poll_tick": 1, "cadence_hot": 1, "cadence_warm": 1, "cadence_cold": 1, "rate_per_min": 600,
                "notify": False, "lease": 900}).start()
try:
    e.add_identity()
    team = e.mock.add_channel("team-builds")
    inc = e.mock.add_channel("incidents")
    alex = e.mock.add_user("alex")
    e.mock.users[alex]["real_name"] = e.mock.users[alex]["profile"]["real_name"] = "Alex Doe"
    dm = e.mock.add_dm(alex)
    e.register({"dms": "none", "channels": [team]}, name="warmup")
    until(lambda: e.db().execute("SELECT 1 FROM conv WHERE channel=? AND cursor IS NOT NULL", (team,)).fetchone(),
          20, "priming")
    e.deregister_all()
    time.sleep(1.5)
    (out / "status-dormant.json").write_text(json.dumps(e.call("status"), indent=2))
    e.register({"dms": "all", "mentions_of_me": True, "channels": [team]}, name="main")
    e.register({"dms": "none", "mentions_of_me": False, "threads_im_in": False, "everyone_in": [inc]}, name="oncall")
    oncall_dir = out / "oncall"
    oncall_dir.mkdir(exist_ok=True)
    (oncall_dir / ".doorbell-agent").write_text("oncall\n")
    waiter = e.popen("wait", "--name", "oncall")   # an agent blocked on `doorbell wait`: costs nothing
    time.sleep(3)
    e.mock.post(dm, "is staging up? the deploy finished a while ago", user=alex)
    e.mock.post(team, "<@%s> can you check why the build is slow?" % e.me, user=alex)
    until(lambda: len(e.items("main")) >= 1, 20, "items")
    time.sleep(4)
    (out / "status-polling.json").write_text(json.dumps(e.call("status"), indent=2))
    print((out / "status-polling.json").read_text())
finally:
    try:
        waiter.kill()
    except NameError:
        pass
    e.stop()
