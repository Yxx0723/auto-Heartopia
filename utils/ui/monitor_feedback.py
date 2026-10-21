"""User-facing error translation without exposing memory addresses."""


def user_feedback(message):
    text = str(message or '')
    folded = text.casefold()
    if '指纹' in text or '版本不匹配' in text:
        return '当前游戏版本暂不支持，请更新程序后重试。'
    if any(word in folded for word in ('权限', '拒绝访问', 'access is denied')):
        return '无法读取游戏，请以管理员身份运行程序。'
    if any(word in folded for word in ('0x', '指针', '基址', '内存', '角色对象', 'readprocessmemory',
                                       'openprocess', 'winerror', 'unityplayer.dll', 'gameassembly.dll', 'mono-2.0')):
        return '角色坐标读取中断，回放已停止；请进入角色后重新开始。' if '回放' in text \
            else '暂时无法读取角色坐标，请进入角色后重新连接。'
    return text
