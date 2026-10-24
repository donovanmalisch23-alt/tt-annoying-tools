from ttmedia.sources.applemusic import AppleMusicSource, _country
from ttmedia.sources.direct import DirectUrlSource
from ttmedia.sources.spotify import SpotifySource, parse_spotify
from ttmedia.sources.ytdlp import YouTubeSource, SoundCloudSource, _host


def test_youtube_matches():
    yt = YouTubeSource(runner=None)
    assert yt.matches("https://www.youtube.com/watch?v=abc")
    assert yt.matches("https://youtu.be/abc")
    assert not yt.matches("https://soundcloud.com/x/y")


def test_soundcloud_matches():
    sc = SoundCloudSource(runner=None)
    assert sc.matches("https://soundcloud.com/forss/flickermood")
    assert not sc.matches("https://example.com/a.mp3")


def test_host_helper():
    assert _host("https://Youtu.be/x") == "youtu.be"
    assert _host("not a url") == ""


def test_spotify_url_parsing():
    assert parse_spotify("https://open.spotify.com/track/4PTG3Z6ehGkBFwjybzWkR8") == (
        "track", "4PTG3Z6ehGkBFwjybzWkR8")
    assert parse_spotify("https://open.spotify.com/intl-de/album/6XzHyAWCSRYVWTcqBkkeMh")[0] == "album"
    assert parse_spotify("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M") == (
        "playlist", "37i9dQZF1DXcBWIGoYBM5M")
    assert parse_spotify("https://example.com") is None


def test_spotify_needs_credentials_for_album():
    s = SpotifySource(client_id="", client_secret="")
    assert not s.has_credentials
    try:
        s.from_url("https://open.spotify.com/album/6XzHyAWCSRYVWTcqBkkeMh")
    except Exception as e:
        assert "developer app" in str(e)
    else:
        raise AssertionError("expected an error without credentials")


def test_apple_matches_and_country():
    am = AppleMusicSource()
    assert am.matches("https://music.apple.com/us/album/x/123?i=456")
    assert _country("https://music.apple.com/gb/album/x/1") == "gb"
    assert _country("https://music.apple.com/album/x/1") == "us"


def test_apple_playlist_unsupported():
    am = AppleMusicSource()
    try:
        am.from_url("https://music.apple.com/us/playlist/x/pl.123")
    except Exception as e:
        assert "playlist" in str(e).lower()
    else:
        raise AssertionError("expected playlist to be rejected")


def test_direct_url_matches_and_live_flag():
    d = DirectUrlSource()
    assert d.matches("https://cdn.example.com/song.mp3")
    assert d.matches("https://ice1.somafm.com/groovesalad-128-mp3")
    assert not d.matches("https://example.com/watch?v=abc")
    [song] = d.from_url("https://cdn.example.com/song.mp3")
    assert song.is_live is False
    [radio] = d.from_url("https://ice1.somafm.com/groovesalad-128-mp3")
    assert radio.is_live is True
