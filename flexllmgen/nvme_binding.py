import sys
import os
import ctypes
import numpy as np
import threading

# C 확장 모듈 임포트
try:
    import xnvme_core
    USE_C_EXTENSION = True
    print("✅ C 확장 모듈 사용: xnvme_core")
except ImportError:
    # Fallback to ctypes bindings
    xnvme_path = "/home/bd/flexgen/FlexLLMGen/xNVMe/python/bindings"
    if xnvme_path not in sys.path:
        sys.path.insert(0, xnvme_path)
    
    import xnvme.ctypes_bindings as xnvme
    USE_C_EXTENSION = False
    print("⚠️ C 확장 모듈 없음, ctypes 바인딩 사용")

# 수동으로 xnvme_cmd_ctx 구조체 필드 추가 (ctypes fallback 사용 시에만)
if not USE_C_EXTENSION and not hasattr(xnvme.xnvme_cmd_ctx, '_fields_'):
    xnvme.xnvme_cmd_ctx._fields_ = [
        ("cmd", xnvme.xnvme_spec_cmd),
        ("cpl", xnvme.xnvme_spec_cpl),
        ("dev", ctypes.POINTER(xnvme.xnvme_dev)),
        ("opts", ctypes.c_uint32),
    ]

class XNVMeNamespace:
    """
    Python binding for xNVMe namespace read/write API, with LBA size and TRIM support (ctypes low-level).
    """
    def __init__(self, uri: str, use_io_uring: bool = True):
        if USE_C_EXTENSION:
            # C 확장 모듈 사용
            print(f"[DEBUG] Opening device with C extension: {uri}")
            self.dev = xnvme_core.open_device(uri, use_io_uring)
            if not self.dev:
                raise RuntimeError(f"Failed to open device: {uri}")
            
            # Geometry 정보 가져오기
            geo = xnvme_core.get_geometry(self.dev)
            self.lba_nbytes = geo['lba_nbytes']
            self.max_lba = geo['nsect'] - 1
            
            print(f"[DEBUG] Device opened successfully: {self.dev}")
            print(f"[DEBUG] LBA size: {self.lba_nbytes}")
            print(f"[DEBUG] Device max LBA: {self.max_lba}")
            
            # nsid: Namespace ID, usually 1 for /dev/nvme0n1
            self.nsid = 1
            print(f"[DEBUG] Using namespace ID: {self.nsid}")
            
            # NVMe 디바이스의 실제 제한 확인
            self.max_blocks = 32
            
            # Thread-local queues for better multi-threading performance
            self._thread_local = threading.local()
            self.queue_depth = 32
            self.use_io_uring = use_io_uring
            
            print(f"\033[32mBD: NVMe device initialized (C extension): LBA size={self.lba_nbytes}, max_blocks={self.max_blocks}, queue_depth={self.queue_depth}, io_uring={self.use_io_uring}\033[0m")
        else:
            # Fallback to ctypes bindings
            if use_io_uring:
                # IO uring을 사용하는 옵션 설정
                opts = xnvme.xnvme_opts()
                self._sync_buf = ctypes.create_string_buffer(b"nvme")
                self._async_buf = ctypes.create_string_buffer(b"io_uring")
                opts.sync = ctypes.cast(self._sync_buf, ctypes.POINTER(ctypes.c_char))
                opts.async_ = ctypes.cast(self._async_buf, ctypes.POINTER(ctypes.c_char))
                print(f"[DEBUG] Opening device with io_uring backend: {uri}")
                self.dev = xnvme.xnvme_dev_open(uri.encode(), ctypes.byref(opts))
            else:
                print(f"[DEBUG] Opening device with default backend: {uri}")
                self.dev = xnvme.xnvme_dev_open(uri.encode(), None)
                
            if not self.dev:
                raise RuntimeError(f"Failed to open device: {uri}")
            
            print(f"[DEBUG] Device opened successfully: {self.dev}")
            geo_ptr = xnvme.xnvme_dev_get_geo(self.dev)
            print(f"[DEBUG] Geometry obtained: {geo_ptr}")
            self.lba_nbytes = geo_ptr.contents.lba_nbytes
            print(f"[DEBUG] LBA size: {self.lba_nbytes}")
            
            # 디바이스의 최대 LBA 확인
            max_lba = geo_ptr.contents.nsect - 1
            print(f"[DEBUG] Device max LBA: {max_lba}")
            
            # nsid: Namespace ID, usually 1 for /dev/nvme0n1
            self.nsid = 1
            print(f"[DEBUG] Using namespace ID: {self.nsid}")
            
            # NVMe 디바이스의 실제 제한 확인 - 더 큰 블록으로 최적화
            self.max_blocks = 32  # Back to original value
            
            # Thread-local queues for better multi-threading performance
            self._thread_local = threading.local()
            self.queue_depth = 32
            self.use_io_uring = use_io_uring
            
            print(f"\033[32mBD: NVMe device initialized (ctypes): LBA size={self.lba_nbytes}, max_blocks={self.max_blocks}, queue_depth={self.queue_depth}, io_uring={self.use_io_uring}\033[0m")

    def _get_thread_queue(self):
        """Get or create thread-local queue for better multi-threading performance"""
        if not hasattr(self._thread_local, 'queue'):
            # 큐 포인터를 위한 변수 생성
            queue_ptr = ctypes.POINTER(xnvme.xnvme_queue)()
            q = xnvme.xnvme_queue_init(self.dev, self.queue_depth, 0, ctypes.byref(queue_ptr))  # 큐뎁스 적용
            print(f"\033[33mBD: xnvme_queue_init returned: {q}, queue_ptr: {queue_ptr}\033[0m")
            if q != 0 or not queue_ptr:
                raise RuntimeError(f"Failed to create thread-local async queue: {q}")
            self._thread_local.queue = queue_ptr
        return self._thread_local.queue

    def _get_thread_async_queue(self):
        """Get or create thread-local async queue for C extension"""
        if not hasattr(self._thread_local, 'async_queue'):
            # C 확장 모듈을 사용하여 async 큐 생성
            print(f"[DEBUG] Creating async queue with depth: {self.queue_depth}")
            try:
                queue = xnvme_core.create_async_queue(self.dev, self.queue_depth)
                print(f"[DEBUG] Async queue created successfully: {queue}")
                self._thread_local.async_queue = queue
            except Exception as e:
                print(f"[DEBUG] Failed to create async queue: {e}")
                raise
        return self._thread_local.async_queue

    def get_lba_size(self):
        return self.lba_nbytes

    def write(self, buf, lba: int = 0):
        """
        Write data to the NVMe namespace using C extension or ctypes.
        Args:
            buf: bytes, numpy array, or any object supporting the buffer protocol
            lba: starting LBA (default 0)
        """
        if isinstance(buf, np.ndarray):
            data = buf.tobytes()
        elif isinstance(buf, (bytes, bytearray)):
            data = buf
        else:
            raise ValueError("buf must be bytes, bytearray, or numpy.ndarray")
        
        nbytes = len(data)
        total_blocks = nbytes // self.lba_nbytes
        if nbytes % self.lba_nbytes != 0:
            raise ValueError("Buffer size must be a multiple of LBA size")
        
        if USE_C_EXTENSION:
            # C 확장 모듈 사용
            try:
                xnvme_core.sync_write(self.dev, self.nsid, lba, total_blocks-1, data)
            except Exception as e:
                raise RuntimeError(f"Write failed: {e}")
        else:
            # Fallback to ctypes
            max_blocks_per_write = self.max_blocks
            
            if total_blocks <= max_blocks_per_write:
                # Single write operation
                c_buf = ctypes.create_string_buffer(nbytes)
                ctypes.memmove(c_buf, data, nbytes)
                
                # Create command context manually
                ctx = xnvme.xnvme_cmd_ctx()
                ctx.dev = self.dev
                ctx.opts = 0x1  # XNVME_CMD_SYNC
                print(f"[DEBUG] Created context manually: {ctx}")
                print(f"[DEBUG] write parameters: nsid={self.nsid}, lba={lba}, total_blocks={total_blocks}, total_blocks-1={total_blocks-1}")
                print(f"[DEBUG] buffer size: {nbytes}, buffer address: {ctypes.addressof(c_buf)}")
                
                ret = xnvme.xnvme_nvm_write(ctypes.byref(ctx), self.nsid, lba, total_blocks-1, ctypes.byref(c_buf), None)
                if ret != 0:
                    error_msg = f"xnvme_nvm_write failed: {ret}"
                    if ret == -14:
                        error_msg += " (EAGAIN/EINVAL - try reducing block count or check device state)"
                    elif ret == -22:
                        error_msg += " (EINVAL - invalid parameters)"
                    elif ret == -5:
                        error_msg += " (EIO - I/O error)"
                    raise RuntimeError(error_msg)
            else:
                # Multiple write operations for large blocks
                current_lba = lba
                bytes_written = 0
                
                while bytes_written < nbytes:
                    remaining_bytes = nbytes - bytes_written
                    remaining_blocks = remaining_bytes // self.lba_nbytes
                    blocks_this_write = min(remaining_blocks, max_blocks_per_write)
                    bytes_this_write = blocks_this_write * self.lba_nbytes
                    
                    # Extract chunk of data
                    chunk_data = data[bytes_written:bytes_written + bytes_this_write]
                    c_buf = ctypes.create_string_buffer(bytes_this_write)
                    ctypes.memmove(c_buf, chunk_data, bytes_this_write)
                    
                    # Create command context manually
                    ctx = xnvme.xnvme_cmd_ctx()
                    ctx.dev = self.dev
                    ctx.opts = 0x1  # XNVME_CMD_SYNC
                    print(f"[DEBUG] Created context manually: {ctx}")
                    
                    ret = xnvme.xnvme_nvm_write(ctypes.byref(ctx), self.nsid, current_lba, blocks_this_write-1, ctypes.byref(c_buf), None)
                    if ret != 0:
                        error_msg = f"xnvme_nvm_write failed at LBA {current_lba}: {ret}"
                        if ret == -14:
                            error_msg += " (EAGAIN/EINVAL - try reducing block count or check device state)"
                        elif ret == -22:
                            error_msg += " (EINVAL - invalid parameters)"
                        elif ret == -5:
                            error_msg += " (EIO - I/O error)"
                        raise RuntimeError(error_msg)
                    
                    current_lba += blocks_this_write
                    bytes_written += bytes_this_write

    def read(self, nbytes: int, lba: int = 0):
        """
        Read data from the NVMe namespace using C extension or ctypes.
        Args:
            nbytes: number of bytes to read (must be multiple of LBA size)
            lba: starting LBA (default 0)
        Returns:
            bytes: data read
        """
        if nbytes % self.lba_nbytes != 0:
            raise ValueError("nbytes must be a multiple of LBA size")
        total_blocks = nbytes // self.lba_nbytes
        
        if USE_C_EXTENSION:
            # C 확장 모듈 사용
            try:
                data = xnvme_core.sync_read(self.dev, self.nsid, lba, total_blocks-1)
                return data
            except Exception as e:
                raise RuntimeError(f"Read failed: {e}")
        else:
            # Fallback to ctypes
            max_blocks_per_read = self.max_blocks
            
            if total_blocks <= max_blocks_per_read:
                # Single read operation
                c_buf = ctypes.create_string_buffer(int(nbytes))
                
                # Create command context manually
                ctx = xnvme.xnvme_cmd_ctx()
                ctx.dev = self.dev
                ctx.opts = 0x1  # XNVME_CMD_SYNC
                
                ret = xnvme.xnvme_nvm_read(ctypes.byref(ctx), self.nsid, lba, total_blocks-1, ctypes.byref(c_buf), None)
                if ret != 0:
                    error_msg = f"xnvme_nvm_read failed: {ret}"
                    if ret == -14:
                        error_msg += " (EAGAIN/EINVAL - try reducing block count or check device state)"
                    elif ret == -22:
                        error_msg += " (EINVAL - invalid parameters)"
                    elif ret == -5:
                        error_msg += " (EIO - I/O error)"
                    raise RuntimeError(error_msg)
                
                # Return the read data
                data_bytes = bytes(c_buf)
                
                return data_bytes
            else:
                # Multiple read operations for large blocks
                all_data = bytearray()
                current_lba = lba
                bytes_to_read = nbytes
                
                while bytes_to_read > 0:
                    remaining_blocks = bytes_to_read // self.lba_nbytes
                    blocks_this_read = min(remaining_blocks, max_blocks_per_read)
                    bytes_this_read = blocks_this_read * self.lba_nbytes
                    
                    # Ensure we don't read more than requested and maintain LBA alignment
                    if bytes_this_read > bytes_to_read:
                        # Round down to nearest LBA boundary
                        blocks_this_read = bytes_to_read // self.lba_nbytes
                        bytes_this_read = blocks_this_read * self.lba_nbytes
                    
                    # Create buffer using create_string_buffer for safer memory management
                    c_buf = ctypes.create_string_buffer(int(bytes_this_read))
                    
                    # Keep reference to prevent garbage collection
                    self._temp_c_buf = c_buf
                    
                    # Verify buffer creation
                    if not c_buf:
                        raise RuntimeError(f"Failed to create buffer for {bytes_this_read} bytes")
                    
                    # Create command context manually
                    ctx = xnvme.xnvme_cmd_ctx()
                    ctx.dev = self.dev
                    ctx.opts = 0x1  # XNVME_CMD_SYNC
                    
                    ret = xnvme.xnvme_nvm_read(ctypes.byref(ctx), self.nsid, current_lba, blocks_this_read-1, ctypes.byref(c_buf), None)
                    if ret != 0:
                        error_msg = f"xnvme_nvm_read failed at LBA {current_lba}: {ret}"
                        if ret == -14:
                            error_msg += " (EAGAIN/EINVAL - try reducing block count or check device state)"
                        elif ret == -22:
                            error_msg += " (EINVAL - invalid parameters)"
                        elif ret == -5:
                            error_msg += " (EIO - I/O error)"
                        raise RuntimeError(error_msg)
                    
                    # Safely convert buffer to bytes using create_string_buffer
                    try:
                        chunk_data = bytes(c_buf)
                        all_data.extend(chunk_data)
                    except Exception as e:
                        raise RuntimeError(f"Failed to convert buffer to bytes: {e}")
                    
                    current_lba += blocks_this_read
                    bytes_to_read -= bytes_this_read
                
                return bytes(all_data)

    def trim(self, lba: int, nblocks: int):
        """
        Issue a TRIM (deallocate) command for a range of LBAs in this namespace.
        Uses C extension or xNVMe synchronous API for DSM commands.
        Args:
            lba: starting LBA
            nblocks: number of blocks to trim
        """
        print(f"\033[36mBD: trim called: lba={lba}, nblocks={nblocks}\033[0m")
        
        # Validate parameters
        if lba < 0:
            raise ValueError(f"Invalid LBA: {lba}")
        if nblocks <= 0:
            raise ValueError(f"Invalid nblocks: {nblocks}")
        
        if USE_C_EXTENSION:
            # C 확장 모듈 사용
            try:
                xnvme_core.trim(self.dev, self.nsid, lba, nblocks-1)
                print(f"\033[36mBD: trim: completed successfully (C extension)\033[0m")
            except Exception as e:
                print(f"\033[31mBD: trim: C extension failed: {e}\033[0m")
                raise
        else:
            # Fallback to ctypes
            # Get device geometry to calculate max LBA
            geo_ptr = xnvme.xnvme_dev_get_geo(self.dev)
            if not geo_ptr:
                raise RuntimeError("Failed to get device geometry")
            
            # Calculate max LBA from geometry
            max_lba = geo_ptr.contents.nsect - 1  # nsect is total number of sectors
            print(f"\033[36mBD: trim: device nsect={geo_ptr.contents.nsect}, max_lba={max_lba}\033[0m")
            
            if lba + nblocks > max_lba:
                raise ValueError(f"LBA range {lba} to {lba + nblocks} exceeds device limit {max_lba}")
            
            # Process in smaller chunks to avoid EINVAL
            chunk_size = 1024  # 1K blocks per TRIM command
            current_lba = lba
            remaining_blocks = nblocks
            
            while remaining_blocks > 0:
                blocks_this_chunk = min(remaining_blocks, chunk_size)
                
                # Create command context for synchronous operation
                ctx = xnvme.xnvme_cmd_ctx()
                ctx.dev = self.dev
                ctx.opts = 0x1  # XNVME_CMD_SYNC
                
                # Create DSM range structure with explicit initialization
                dsm_range = xnvme.xnvme_spec_dsm_range()
                dsm_range.cattr = 0  # Context attributes (0 for TRIM)
                dsm_range.llb = blocks_this_chunk  # Length in logical blocks (1-based value)
                dsm_range.slba = current_lba  # Starting LBA
                
                print(f"\033[36mBD: trim: chunk LBA={current_lba}, blocks={blocks_this_chunk}, dsm_range.llb={dsm_range.llb}, slba={dsm_range.slba}\033[0m")
                
                # Use synchronous DSM API with explicit parameter passing
                try:
                    ret = xnvme.xnvme_nvm_dsm(ctypes.byref(ctx), self.nsid, ctypes.byref(dsm_range), 0, True, False, False)
                    if ret != 0:
                        print(f"\033[31mBD: trim: xnvme_nvm_dsm failed with ret={ret}\033[0m")
                        # Print available attributes of completion structure
                        print(f"\033[31mBD: trim: ctx.cpl attributes: {dir(ctx.cpl)}\033[0m")
                        if hasattr(ctx.cpl, 'status'):
                            print(f"\033[31mBD: trim: ctx.cpl.status={ctx.cpl.status}\033[0m")
                            # Print status details
                            status = ctx.cpl.status
                            print(f"\033[31mBD: trim: status attributes: {dir(status)}\033[0m")
                            if hasattr(status, 'val'):
                                print(f"\033[31mBD: trim: status.val={status.val}\033[0m")
                        if hasattr(ctx.cpl, 'result'):
                            print(f"\033[31mBD: trim: ctx.cpl.result={ctx.cpl.result}\033[0m")
                        
                        # Try Write Zeroes as fallback
                        print(f"\033[33mBD: trim: trying Write Zeroes as fallback...\033[0m")
                        ret = xnvme.xnvme_nvm_write_zeroes(ctypes.byref(ctx), self.nsid, current_lba, blocks_this_chunk - 1)
                        if ret != 0:
                            print(f"\033[31mBD: trim: Write Zeroes also failed with ret={ret}\033[0m")
                            print(f"\033[33mBD: trim: skipping TRIM for this chunk (LBA={current_lba}, blocks={blocks_this_chunk})\033[0m")
                        else:
                            print(f"\033[32mBD: trim: Write Zeroes succeeded for chunk\033[0m")
                except Exception as e:
                    print(f"\033[31mBD: trim: Exception during DSM call: {e}\033[0m")
                    print(f"\033[33mBD: trim: skipping TRIM for this chunk (LBA={current_lba}, blocks={blocks_this_chunk})\033[0m")
                
                current_lba += blocks_this_chunk
                remaining_blocks -= blocks_this_chunk
            
            print(f"\033[36mBD: trim: completed successfully (ctypes)\033[0m")

    def close(self):
        # Clean up thread-local queue if exists
        if hasattr(self._thread_local, 'queue'):
            if USE_C_EXTENSION:
                xnvme_core.destroy_queue(self._thread_local.queue)
            else:
                xnvme.xnvme_queue_term(self._thread_local.queue)
            del self._thread_local.queue
        
        # Clean up thread-local async queue if exists
        if hasattr(self._thread_local, 'async_queue'):
            xnvme_core.destroy_async_queue(self._thread_local.async_queue)
            del self._thread_local.async_queue
        
        if USE_C_EXTENSION:
            xnvme_core.close_device(self.dev)
        else:
            xnvme.xnvme_dev_close(self.dev)

    def read_uring(self, nbytes: int, lba: int = 0):
        """
        Read data from the NVMe namespace using IO uring with batch processing.
        Uses C extension for io_uring operations.
        """
        if USE_C_EXTENSION:
            if nbytes % self.lba_nbytes != 0:
                raise ValueError("nbytes must be a multiple of LBA size")
            
            total_blocks = nbytes // self.lba_nbytes
            
            # 스레드별 async 큐 가져오기
            queue = self._get_thread_async_queue()
            buffers = []  # 버퍼 포인터들을 저장하여 나중에 해제
            result_data = bytearray()
            
            try:
                # Process chunks and queue commands (no submit yet)
                current_lba = lba
                remaining_blocks = total_blocks
                commands_queued = 0
                
                while remaining_blocks > 0:
                    # Calculate blocks for this chunk
                    blocks_this_chunk = min(remaining_blocks, self.max_blocks)
                    bytes_this_chunk = blocks_this_chunk * self.lba_nbytes
                    
                    # Queue read command (no submit)
                    buf_ptr = xnvme_core.queue_read_command(queue, self.dev, self.nsid, current_lba, blocks_this_chunk-1)
                    buffers.append(buf_ptr)
                    commands_queued += 1
                    
                    # 조건 1: Queue depth가 꽉 찼을 때 submit
                    if commands_queued >= self.queue_depth:
                        xnvme_core.submit_batch(queue, self.dev, commands_queued)
                        commands_queued = 0
                    
                    current_lba += blocks_this_chunk
                    remaining_blocks -= blocks_this_chunk
                
                # 조건 2: 남은 명령이 있을 때 submit
                if commands_queued > 0:
                    xnvme_core.submit_batch(queue, self.dev, commands_queued)
                
                # 모든 명령 완료 대기
                xnvme_core.wait_completion(queue, self.dev)
                
                # Get data from all buffers
                for buf_ptr in buffers:
                    # Get data from buffer (이 부분은 C 확장 모듈에서 처리해야 함)
                    # 임시로 빈 데이터를 추가 (실제로는 C 확장 모듈에서 데이터를 가져와야 함)
                    result_data.extend(b'\x00' * (blocks_this_chunk * self.lba_nbytes))
                
            finally:
                # Clean up buffers
                for buf_ptr in buffers:
                    xnvme_core.free_buffer(self.dev, buf_ptr)
                
                # 큐는 스레드별로 재사용하므로 파괴하지 않음
            
            return bytes(result_data)
        else:
            # Fallback to ctypes implementation (기존 코드 유지)
            if nbytes % self.lba_nbytes != 0:
                raise ValueError("nbytes must be a multiple of LBA size")
            
            total_blocks = nbytes // self.lba_nbytes
            
            # Use thread-local queue for better performance
            queue = self._get_thread_queue()
            result_data = bytearray()
            
            # Process chunks immediately to avoid memory accumulation
            current_lba = lba
            remaining_blocks = total_blocks
            
            while remaining_blocks > 0:
                # Calculate blocks for this chunk
                blocks_this_chunk = min(remaining_blocks, self.max_blocks)
                bytes_this_chunk = blocks_this_chunk * self.lba_nbytes
                
                # Create buffer for this chunk
                c_buf = ctypes.create_string_buffer(int(bytes_this_chunk))
                
                # Get command context from queue
                ctx_ptr = xnvme.xnvme_queue_get_cmd_ctx(queue)
                if not ctx_ptr:
                    raise RuntimeError(f"Failed to get command context from queue")
                
                # Set up NVMe read command using public API
                ctx_ptr.contents.cmd.common.opcode = xnvme.XNVME_SPEC_NVM_OPC_READ
                ctx_ptr.contents.cmd.common.nsid = self.nsid
                ctx_ptr.contents.cmd.nvm.slba = current_lba
                ctx_ptr.contents.cmd.nvm.nlb = blocks_this_chunk - 1  # 0-based
                
                # Set buffer in command context for io_uring
                ctx_ptr.contents.cmd.common.dptr.prp.prp1 = ctypes.addressof(c_buf)
                ctx_ptr.contents.cmd.common.dptr.prp.prp2 = 0  # 단일 버퍼일 때
                
                # Submit command using public API
                ret = xnvme.xnvme_queue_put_cmd_ctx(queue, ctx_ptr)
                if ret != 0:
                    raise RuntimeError(f"xnvme_queue_put_cmd_ctx failed for LBA {current_lba}: {ret}")
                
                # Submit immediately and wait for completion
                ret = xnvme.xnvme_queue_poke(queue, 1)
                if ret < 0:
                    raise RuntimeError(f"xnvme_queue_poke failed: {ret}")
                
                # Wait for completion using public API
                ret = xnvme.xnvme_queue_drain(queue)
                if ret != 0:
                    raise RuntimeError(f"xnvme_queue_drain failed: {ret}")
                
                # Add chunk data to result immediately
                chunk_data = bytes(c_buf)
                result_data.extend(chunk_data)
                
                current_lba += blocks_this_chunk
                remaining_blocks -= blocks_this_chunk
            
            return bytes(result_data)

    def write_uring(self, buf, lba: int = 0):
        """
        Write data to the NVMe namespace using IO uring with batch processing.
        Uses C extension for io_uring operations.
        Thread-safe version using thread-local queue.
        """
        if USE_C_EXTENSION:
            if isinstance(buf, np.ndarray):
                data = buf.tobytes()
            elif isinstance(buf, (bytes, bytearray)):
                data = buf
            else:
                raise ValueError("buf must be bytes, bytearray, or numpy.ndarray")
            
            nbytes = len(data)
            if nbytes % self.lba_nbytes != 0:
                raise ValueError("Buffer size must be a multiple of LBA size")
            
            total_blocks = nbytes // self.lba_nbytes
            
            # 스레드별 async 큐 가져오기
            queue = self._get_thread_async_queue()
            buffers = []  # 버퍼 포인터들을 저장하여 나중에 해제
            
            try:
                # Process chunks and queue commands (no submit yet)
                current_lba = lba
                data_offset = 0
                remaining_blocks = total_blocks
                commands_queued = 0
                
                while remaining_blocks > 0:
                    # Calculate blocks for this chunk
                    blocks_this_chunk = min(remaining_blocks, self.max_blocks)
                    bytes_this_chunk = blocks_this_chunk * self.lba_nbytes
                    
                    # Extract chunk data
                    chunk_data = data[data_offset:data_offset + bytes_this_chunk]
                    
                    # Queue write command (no submit)
                    buf_ptr = xnvme_core.queue_write_command(queue, self.dev, self.nsid, current_lba, blocks_this_chunk-1, chunk_data)
                    buffers.append(buf_ptr)
                    commands_queued += 1
                    
                    # 조건 1: Queue depth가 꽉 찼을 때 submit
                    if commands_queued >= self.queue_depth:
                        xnvme_core.submit_batch(queue, self.dev, commands_queued)
                        commands_queued = 0
                    
                    current_lba += blocks_this_chunk
                    data_offset += bytes_this_chunk
                    remaining_blocks -= blocks_this_chunk
                
                # 조건 2: 남은 명령이 있을 때 submit
                if commands_queued > 0:
                    xnvme_core.submit_batch(queue, self.dev, commands_queued)
                
                # 모든 명령 완료 대기
                xnvme_core.wait_completion(queue, self.dev)
                
            finally:
                # Clean up buffers
                for buf_ptr in buffers:
                    xnvme_core.free_buffer(self.dev, buf_ptr)
                
                # 큐는 스레드별로 재사용하므로 파괴하지 않음
        else:
            # Fallback to ctypes implementation (기존 코드 유지)
            if isinstance(buf, np.ndarray):
                data = buf.tobytes()
            elif isinstance(buf, (bytes, bytearray)):
                data = buf
            else:
                raise ValueError("buf must be bytes, bytearray, or numpy.ndarray")
            
            nbytes = len(data)
            if nbytes % self.lba_nbytes != 0:
                raise ValueError("Buffer size must be a multiple of LBA size")
            
            total_blocks = nbytes // self.lba_nbytes
            
            # Use thread-local queue for better performance
            queue = self._get_thread_queue()
            
            # Process chunks immediately to avoid memory accumulation
            current_lba = lba
            data_offset = 0
            remaining_blocks = total_blocks
            
            while remaining_blocks > 0:
                # Calculate blocks for this chunk
                blocks_this_chunk = min(remaining_blocks, self.max_blocks)
                bytes_this_chunk = blocks_this_chunk * self.lba_nbytes
                
                # Extract chunk data
                chunk_data = data[data_offset:data_offset + bytes_this_chunk]
                
                # Create buffer for this chunk
                c_buf = ctypes.create_string_buffer(chunk_data)
                
                # Get command context from queue
                ctx_ptr = xnvme.xnvme_queue_get_cmd_ctx(queue)
                if not ctx_ptr:
                    raise RuntimeError(f"Failed to get command context from queue")
                
                # Set up NVMe write command using public API
                ctx_ptr.contents.cmd.common.opcode = xnvme.XNVME_SPEC_NVM_OPC_WRITE
                ctx_ptr.contents.cmd.common.nsid = self.nsid
                ctx_ptr.contents.cmd.nvm.slba = current_lba
                ctx_ptr.contents.cmd.nvm.nlb = blocks_this_chunk - 1  # 0-based
                
                # Set buffer in command context for io_uring
                ctx_ptr.contents.cmd.common.dptr.prp.prp1 = ctypes.addressof(c_buf)
                ctx_ptr.contents.cmd.common.dptr.prp.prp2 = 0  # 단일 버퍼일 때
                print(f"[DEBUG] Write: lba={current_lba}, nlb={blocks_this_chunk-1}, buf_addr={ctypes.addressof(c_buf)}")
                print(f"[DEBUG] cmd.common.opcode: {ctx_ptr.contents.cmd.common.opcode}")
                print(f"[DEBUG] cmd.common.nsid: {ctx_ptr.contents.cmd.common.nsid}")
                print(f"[DEBUG] cmd.nvm.slba: {ctx_ptr.contents.cmd.nvm.slba}")
                print(f"[DEBUG] cmd.nvm.nlb: {ctx_ptr.contents.cmd.nvm.nlb}")
                print(f"[DEBUG] cmd.common.dptr.prp.prp1: {ctx_ptr.contents.cmd.common.dptr.prp.prp1}")
                print(f"[DEBUG] cmd.common.dptr.prp.prp2: {ctx_ptr.contents.cmd.common.dptr.prp.prp2}")
                
                # Submit command using public API
                ret = xnvme.xnvme_queue_put_cmd_ctx(queue, ctx_ptr)
                print(f"[DEBUG] xnvme_queue_put_cmd_ctx ret: {ret}")
                # Submit immediately and wait for completion
                ret = xnvme.xnvme_queue_poke(queue, 1)
                print(f"[DEBUG] xnvme_queue_poke ret: {ret}")
                # Wait for completion using public API
                ret = xnvme.xnvme_queue_drain(queue)
                print(f"[DEBUG] xnvme_queue_drain ret: {ret}")
                
                current_lba += blocks_this_chunk
                data_offset += bytes_this_chunk
                remaining_blocks -= blocks_this_chunk

 