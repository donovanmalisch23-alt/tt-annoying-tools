"""Exception hierarchy for Simple TT Media.

Keeping the exceptions in one tiny, dependency-free module lets every layer
(config, SDK loader, TeamTalk client, audio engine, sources, commands) raise
and catch precisely without import cycles.
"""

from __future__ import annotations


class TTMediaError(Exception):
    """Base class for every error this application raises on purpose."""


class ConfigError(TTMediaError):
    """Configuration is missing, malformed, or fails validation."""


class SdkError(TTMediaError):
    """The TeamTalk SDK could not be located, loaded, or initialised."""


class ConnectionFailed(TTMediaError):
    """Could not connect to / log in to / join the TeamTalk server."""


class AudioError(TTMediaError):
    """The audio engine (ffmpeg / decoding / playback) failed."""


class SourceError(TTMediaError):
    """A media source could not resolve a query into a playable track."""


class CommandError(TTMediaError):
    """A chat command could not be carried out; the message is user-facing."""


class PermissionDenied(CommandError):
    """The requesting user is not allowed to run the command."""


class UnknownCommand(CommandError):
    """No command matched the name the user sent."""
