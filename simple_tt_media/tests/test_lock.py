from ttmedia.commands import build_dispatcher
from ttmedia.lock import LockManager


class FakeClient:
    def __init__(self):
        self.pms = []       # (user_id, text)
        self.channel = []   # text

    def reply_private(self, user_id, text):
        self.pms.append((user_id, text))

    def send_channel(self, text, channel_id=None):
        self.channel.append(text)


ADMIN, USER = 1, 2


def test_admin_locks_and_anyone_unlocks():
    c = FakeClient()
    lm = LockManager(c)
    replies = []

    # admin starts a lock -> gets a private prompt
    lm.request(ADMIN, "Boss", is_admin=True, is_private=True, reply=replies.append)
    assert lm.has_pending(ADMIN)
    assert c.pms and "password you want to set" in c.pms[-1][1]
    assert not lm.locked

    # admin's next PM sets the password and locks the bot
    lm.submit_password(ADMIN, "sekret")
    assert lm.locked
    assert any("locked by Boss" in m for m in c.channel)

    # a non-admin can start unlocking
    lm.request(USER, "Randy", is_admin=False, is_private=True, reply=replies.append)
    assert lm.has_pending(USER)
    assert "unlock password" in c.pms[-1][1]

    # wrong password keeps it locked
    lm.submit_password(USER, "nope")
    assert lm.locked
    assert "Incorrect" in c.pms[-1][1]

    # correct password unlocks it
    lm.request(USER, "Randy", is_admin=False, is_private=True, reply=replies.append)
    lm.submit_password(USER, "sekret")
    assert not lm.locked
    assert any("unlocked by Randy" in m for m in c.channel)


def test_non_admin_cannot_lock():
    c = FakeClient()
    lm = LockManager(c)
    out = []
    lm.request(USER, "Randy", is_admin=False, is_private=True, reply=out.append)
    assert not lm.locked
    assert not lm.has_pending(USER)
    assert out and "not allowed" in out[0].lower()


def test_cancel_aborts():
    c = FakeClient()
    lm = LockManager(c)
    lm.request(ADMIN, "Boss", is_admin=True, is_private=True, reply=lambda m: None)
    lm.submit_password(ADMIN, "cancel")
    assert not lm.locked
    assert not lm.has_pending(ADMIN)


def test_channel_lock_points_to_pm():
    c = FakeClient()
    lm = LockManager(c)
    out = []
    # lock sent from the channel (not private)
    lm.request(ADMIN, "Boss", is_admin=True, is_private=False, reply=out.append)
    assert out and "private messages" in out[0].lower()
    assert c.pms  # the actual prompt went to PM


# ---- dispatcher integration ---------------------------------------------

class FakePlayer:
    current = None
    def stop(self): pass


class FakeSources:
    service = "youtube"


class Cfg:
    class audio:
        max_volume = 200
        max_track_seconds = 0


def _run(d, text, user_id, is_admin, reply):
    import time
    d.handle(text=text, requester="N", user_id=user_id, is_admin=is_admin,
             is_private=True, reply=reply)
    time.sleep(0.05)


def test_dispatcher_blocks_commands_while_locked():
    c = FakeClient()
    lm = LockManager(c)
    d = build_dispatcher(FakePlayer(), FakeSources(), c, Cfg(), lock_manager=lm)
    # lock it directly
    lm.request(ADMIN, "Boss", is_admin=True, is_private=True, reply=lambda m: None)
    lm.submit_password(ADMIN, "pw")
    assert lm.locked

    out = []
    _run(d, "q", USER, False, out.append)       # a normal command
    assert out and "locked" in out[0].lower()

    # `lock` still gets through (to start unlocking) -> a PM prompt, no block
    out2 = []
    _run(d, "lock", USER, False, out2.append)
    assert lm.has_pending(USER)

    # the user's next PM is captured as the password, not run as a command
    _run(d, "pw", USER, False, lambda m: None)
    assert not lm.locked
