"""Windows directory handles and relative creation, without path re-resolution."""
from contextlib import contextmanager


@contextmanager
def directory_handle(path):
    import ctypes
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 3, None, 3, 0x02000000 | 0x00200000, None)
    if handle == w.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield handle
    finally:
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def signature(handle):
    import ctypes
    from ctypes import wintypes as w

    class Information(ctypes.Structure):
        _fields_ = [('attributes', w.DWORD), ('created', w.FILETIME), ('accessed', w.FILETIME),
                    ('written', w.FILETIME), ('volume', w.DWORD), ('size_high', w.DWORD),
                    ('size_low', w.DWORD), ('links', w.DWORD), ('index_high', w.DWORD), ('index_low', w.DWORD)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetFileInformationByHandle.argtypes = [w.HANDLE, ctypes.POINTER(Information)]
    kernel.GetFileInformationByHandle.restype = w.BOOL
    info = Information()
    if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not info.attributes & 0x10 or info.attributes & 0x400:
        raise OSError('unsafe_project_directory')
    return {'device': info.volume, 'inode': info.index_high << 32 | info.index_low}


def mkdir_relative(parent_handle, name):
    """NtCreateFile FILE_CREATE with a real RootDirectory, never a full path."""
    import ctypes
    from ctypes import wintypes as w

    class UnicodeString(ctypes.Structure):
        _fields_ = [('length', w.USHORT), ('maximum_length', w.USHORT), ('buffer', w.LPWSTR)]
    class Attributes(ctypes.Structure):
        _fields_ = [('length', w.ULONG), ('root', w.HANDLE), ('name', ctypes.POINTER(UnicodeString)),
                    ('attributes', w.ULONG), ('security', w.LPVOID), ('quality', w.LPVOID)]
    class Status(ctypes.Structure):
        _fields_ = [('status', w.LPVOID), ('information', ctypes.c_size_t)]
    value = ctypes.create_unicode_buffer(name)
    size = len(name.encode('utf-16-le'))
    unicode = UnicodeString(size, size + 2, ctypes.cast(value, w.LPWSTR))
    attributes = Attributes(ctypes.sizeof(Attributes), parent_handle, ctypes.pointer(unicode), 0x40, None, None)
    result, status = w.HANDLE(), Status()
    native = ctypes.WinDLL('ntdll')
    native.NtCreateFile.argtypes = [ctypes.POINTER(w.HANDLE), w.ULONG, ctypes.POINTER(Attributes),
        ctypes.POINTER(Status), w.LPVOID, w.ULONG, w.ULONG, w.ULONG, w.ULONG, w.LPVOID, w.ULONG]
    native.NtCreateFile.restype = w.LONG
    # SYNCHRONIZE|FILE_READ_ATTRIBUTES, FILE_CREATE, DIRECTORY|SYNCHRONOUS_IO_NONALERT.
    code = native.NtCreateFile(ctypes.byref(result), 0x100080, ctypes.byref(attributes), ctypes.byref(status),
                               None, 0x80, 7, 2, 0x21, None, 0)
    if code < 0:
        native.RtlNtStatusToDosError.argtypes = [w.LONG]
        native.RtlNtStatusToDosError.restype = w.ULONG
        raise ctypes.WinError(native.RtlNtStatusToDosError(code))
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    try:
        return signature(result)
    finally:
        if not kernel.CloseHandle(result):
            raise ctypes.WinError(ctypes.get_last_error())
