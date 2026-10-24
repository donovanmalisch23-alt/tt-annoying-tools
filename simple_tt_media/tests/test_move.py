"""The client must re-init audio only on a genuine channel move of the bot."""

from ttmedia.teamtalk.client import TeamTalkClient


class _Inst:
    pass


class _FakeTT:
    """Minimal stand-in so the client constructs without the real SDK."""
    def TeamTalk(self):
        return _Inst()

    class ClientEvent:
        pass


class _User:
    def __init__(self, uid, cid):
        self.nUserID = uid
        self.nChannelID = cid


def _client():
    c = TeamTalkClient(cfg=object(), tt_module=_FakeTT())
    c._my_user_id = 7
    return c


def test_first_join_does_not_fire():
    c = _client()
    fired = []
    c.on_channel_changed = fired.append
    c._maybe_channel_change(_User(7, 1))   # 0 -> 1 is the initial join
    assert fired == []
    assert c._channel_id == 1


def test_real_move_fires_with_new_channel():
    c = _client()
    fired = []
    c.on_channel_changed = fired.append
    c._maybe_channel_change(_User(7, 1))   # initial join
    c._maybe_channel_change(_User(7, 2))   # moved 1 -> 2
    assert fired == [2]


def test_duplicate_and_other_users_ignored():
    c = _client()
    fired = []
    c.on_channel_changed = fired.append
    c._maybe_channel_change(_User(7, 1))
    c._maybe_channel_change(_User(7, 2))
    c._maybe_channel_change(_User(7, 2))   # same channel again -> no-op
    c._maybe_channel_change(_User(99, 5))  # a different user -> ignored
    assert fired == [2]


def test_move_back_fires_again():
    c = _client()
    fired = []
    c.on_channel_changed = fired.append
    c._maybe_channel_change(_User(7, 1))
    c._maybe_channel_change(_User(7, 2))
    c._maybe_channel_change(_User(7, 1))   # moved back 2 -> 1
    assert fired == [2, 1]
