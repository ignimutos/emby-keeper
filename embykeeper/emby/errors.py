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
    """非成功状态码. error_code 保存服务器返回的 ErrorCode (如有), 供上层区分策略性拒绝."""

    def __init__(self, message, error_code=None):
        super().__init__(message)
        self.error_code = error_code


class EmbyPlayError(EmbyError):
    pass


class EmbyStoppedReportError(EmbyPlayError):
    pass


class EmbyStreamRejectedError(EmbyPlayError):
    """服务器拒绝放行媒体流 (如 playback grant). 属条目级失败, 应换下一个条目而非重试."""
