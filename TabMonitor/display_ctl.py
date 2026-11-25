import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]

class DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]

def get_virtual_display_device():
    dd = DISPLAY_DEVICEW()
    dd.cb = ctypes.sizeof(DISPLAY_DEVICEW)
    i = 0
    non_primary = None
    while user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
        if dd.StateFlags & 1:  # DISPLAY_DEVICE_ATTACHED_TO_DESKTOP
            if "Virtual Display" in dd.DeviceString or "MttVDD" in dd.DeviceID:
                return dd.DeviceName
            if not (dd.StateFlags & 4):  # Not primary
                non_primary = dd.DeviceName
        i += 1
    return non_primary

def get_virtual_display_resolution():
    dev = get_virtual_display_device()
    if not dev:
        return None
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    if user32.EnumDisplaySettingsW(dev, -1, ctypes.byref(dm)):
        return (int(dm.dmPelsWidth), int(dm.dmPelsHeight))
    return None

def set_virtual_display_resolution(width, height, hz=60):
    """
    Switch resolution purely in-place via Win32 ChangeDisplaySettingsExW without
    reloading the Virtual Display Driver. This ensures that the Windows display
    identifier (e.g. `\\.\DISPLAY2`) remains fixed across resolution switches.
    """
    dev = get_virtual_display_device()
    if not dev:
        return False, "가상 디스플레이 장치를 찾을 수 없습니다."

    cur = get_virtual_display_resolution()
    if cur == (width, height):
        return True, f"이미 {width}×{height} @ {hz}Hz ({dev}) 해상도로 유지 중입니다."

    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    i = 0
    while user32.EnumDisplaySettingsW(dev, i, ctypes.byref(dm)):
        if dm.dmPelsWidth == width and dm.dmPelsHeight == height and dm.dmDisplayFrequency == hz:
            res = user32.ChangeDisplaySettingsExW(dev, ctypes.byref(dm), None, 1, None)
            if res != 0:
                res = user32.ChangeDisplaySettingsExW(dev, ctypes.byref(dm), None, 0, None)
            if res == 0:
                return True, f"디스플레이({dev}) 번호 유지 상태로 {width}×{height} @ {hz}Hz 전환 완료!"
            break
        i += 1

    # Fallback: modify current DEVMODEW fields directly in-place (never reloads driver)
    if user32.EnumDisplaySettingsW(dev, -1, ctypes.byref(dm)):
        dm.dmPelsWidth = width
        dm.dmPelsHeight = height
        dm.dmDisplayFrequency = hz
        dm.dmFields = 0x00080000 | 0x00100000 | 0x00400000  # DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY
        res = user32.ChangeDisplaySettingsExW(dev, ctypes.byref(dm), None, 0, None)
        if res == 0:
            return True, f"디스플레이({dev}) 번호 유지 상태로 {width}×{height} @ {hz}Hz 전환 완료!"

    return False, f"해상도 변경 실패 (현재 디스플레이: {dev})"
