"""Windows process reader. Requests QUERY_INFORMATION | VM_READ only; no writes."""
import ctypes as C
from ctypes import wintypes as W
import struct

K=C.WinDLL('kernel32',use_last_error=True)
class MBI(C.Structure):
    _fields_=[('BaseAddress',C.c_void_p),('AllocationBase',C.c_void_p),
              ('AllocationProtect',W.DWORD),('_padding',W.DWORD),
              ('RegionSize',C.c_size_t),('State',W.DWORD),('Protect',W.DWORD),
              ('Type',W.DWORD),('_padding2',W.DWORD)]
K.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];K.OpenProcess.restype=W.HANDLE
K.CloseHandle.argtypes=[W.HANDLE]
K.ReadProcessMemory.argtypes=[W.HANDLE,C.c_void_p,C.c_void_p,C.c_size_t,C.POINTER(C.c_size_t)]
K.ReadProcessMemory.restype=W.BOOL
K.VirtualQueryEx.argtypes=[W.HANDLE,C.c_void_p,C.POINTER(MBI),C.c_size_t]
K.VirtualQueryEx.restype=C.c_size_t
K.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,C.POINTER(W.DWORD)]
K.QueryFullProcessImageNameW.restype=W.BOOL

class Reader:
    def __init__(self,pid):
        self.pid=pid;self.handle=K.OpenProcess(0x0400|0x0010,False,pid)
        if not self.handle: raise OSError(C.get_last_error(),'OpenProcess read-only')
    def close(self):
        if self.handle: K.CloseHandle(self.handle);self.handle=None
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def image_path(self):
        """Resolve the actual process installation, not a developer's drive."""
        capacity=W.DWORD(32768)
        path=C.create_unicode_buffer(capacity.value)
        if not K.QueryFullProcessImageNameW(self.handle,0,path,C.byref(capacity)):
            raise OSError(C.get_last_error(),'QueryFullProcessImageNameW')
        return path.value
    def read(self,addr,size):
        if not addr or size<0 or size>64*1024*1024:raise ValueError('Invalid read')
        buf=C.create_string_buffer(size);done=C.c_size_t()
        ok=K.ReadProcessMemory(self.handle,addr,buf,size,C.byref(done))
        if not ok and not done.value:raise OSError(C.get_last_error(),f'ReadProcessMemory {addr:#x}')
        return buf.raw[:done.value]
    def unpack(self,fmt,addr):return struct.unpack(fmt,self.read(addr,struct.calcsize(fmt)))[0]
    def u64(self,addr):return self.unpack('<Q',addr)
    def i32(self,addr):return self.unpack('<i',addr)
    def f64(self,addr):return self.unpack('<d',addr)
    def regions(self,private_only=False):
        addr=0
        while addr<0x7fffffffffff:
            mbi=MBI()
            if not K.VirtualQueryEx(self.handle,addr,C.byref(mbi),C.sizeof(mbi)):break
            base=mbi.BaseAddress or 0;size=mbi.RegionSize
            if size<=0:break
            if mbi.State==0x1000 and not mbi.Protect&(0x100|0x01) and (not private_only or mbi.Type==0x20000):
                yield base,size,mbi.Type,mbi.Protect
            addr=base+size
    def scan(self,patterns,private_only=True,max_total=1024*1024*1024):
        """Yield (pattern key, address); never stores a process dump."""
        total=0;overlap=max(map(len,patterns.values()))-1
        for base,size,_,_ in self.regions(private_only):
            for offset in range(0,size,4*1024*1024):
                count=min(4*1024*1024+overlap,size-offset)
                total+=count
                if total>max_total:raise RuntimeError('Scan read budget exhausted')
                try:data=self.read(base+offset,count)
                except OSError:continue
                for key,needle in patterns.items():
                    start=0
                    while True:
                        pos=data.find(needle,start)
                        if pos<0:break
                        yield key,base+offset+pos
                        start=pos+1
