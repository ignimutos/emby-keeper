"""Emby 域错误层级. 独立成模块以便 api / keepalive / playback 共用而不产生循环导入."""


class EmbyError(Exception):
    pass


class EmbyRequestError(EmbyError):
    pass


class EmbyConnectError(EmbyError):
    pass


class EmbyLoginError(EmbyRequestError):
    pass


class EmbyStatusError(EmbyRequestError):
    pass


class EmbyPlayError(EmbyError):
    pass


class EmbyStoppedReportError(EmbyPlayError):
    pass
