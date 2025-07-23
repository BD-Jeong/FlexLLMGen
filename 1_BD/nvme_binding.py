from re import U
import sys
import os
import ctypes
import ctypes.util
import numpy as np
import threading
import time
from typing import Union, Optional

# Import C extension module
try:
    import xnvme_core
    USE_C_EXTENSION = True
    print("Using C extension module: xnvme_core")
except ImportError:
    assert False, "C extension module (xnvme_core) not found. Please build and try again."

class XNVMeNamespace:
    """
    Python binding for xNVMe namespace read/write API, with LBA size and TRIM support (ctypes low-level).
    """
    def __init__(self, uri: str, use_io_uring: bool = True):
        # Use C extension module
        self.dev = xnvme_core.dev_open(uri, use_io_uring)
        if not self.dev:
            raise RuntimeError(f"Failed to open device: {uri}")

        # Get geometry information
        geo = xnvme_core.get_geo(self.dev)
        self.lba_nbytes = geo['lba_nbytes']
        self.max_lba = geo['nsect'] - 1
        self.mdts = geo['mdts']

        self.nsid = 1
        # Calculate max_blocks based on MDTS (Maximum Data Transfer Size)
        self.max_blocks = self.mdts // self.lba_nbytes
        self.queue_depth = 64
        self.use_io_uring = use_io_uring
        self._thread_local = threading.local() # # for thread-local queues

        print("\033[32mBD: NVMe Device Initialized (C extension)\033[0m")
        print("- device: ", {uri})
        print("- nsid: ", {self.nsid})
        print("- max_lba: ", {self.max_lba})
        print("- mdts: ", {self.mdts})
        print("- lba_nbytes: ", {self.lba_nbytes})
        print("- max_blocks: ", {self.max_blocks})
        print("- queue_depth: ", {self.queue_depth})
        print("- io_uring: ", {self.use_io_uring})        

    def get_thread_queue(self):
        """Get or create thread-local queue for C extension"""
        if not hasattr(self._thread_local, 'queue'):
            # Create async queue using C extension module
            try:
                queue = xnvme_core.queue_init(self.dev, self.queue_depth)
                self._thread_local.queue = queue
            except Exception as e:
                print(f"Failed to create async queue: {e}")
                raise
        return self._thread_local.queue

    def get_lba_size(self):
        return self.lba_nbytes

    def free_buffer(self, buf_ptr):
        """Free a buffer allocated by the NVMe device"""
        xnvme_core.buf_free(self.dev, buf_ptr)

    def sync_write(self, buf, lba: int = 0):
        """
        Write data to the NVMe namespace using C extension.
        Args:
            buf: memoryview object
            lba: starting LBA (default 0)
        """
        if isinstance(buf, memoryview):
            data = buf
            nbytes = len(data) * data.itemsize
        else:
            raise ValueError("buf must be memoryview")

        total_blocks = nbytes // self.lba_nbytes
        if nbytes % self.lba_nbytes != 0:
            raise ValueError("Buffer size must be a multiple of LBA size")

        # Use synchronous processing for reliability
        current_lba = lba
        data_offset = 0
        remaining_blocks = total_blocks

        while remaining_blocks > 0:
            # Calculate blocks for this chunk
            blocks_this_chunk = min(remaining_blocks, self.max_blocks)
            nlb = max(1, blocks_this_chunk) - 1
            bytes_this_chunk = blocks_this_chunk * self.lba_nbytes
            
            # Get chunk data using memoryview (no copy)
            chunk_mv = data[data_offset:data_offset + bytes_this_chunk]
            # Let C extension allocate buffer and copy data
            xnvme_core.sync_write(self.dev, chunk_mv, self.nsid, current_lba, nlb)

            current_lba += blocks_this_chunk
            data_offset += bytes_this_chunk
            remaining_blocks -= blocks_this_chunk

    def sync_read(self, nbytes: int, lba: int = 0, dtype=np.uint8):
        """
        Read data from the NVMe namespace using C extension.
        Args:
            nbytes: number of bytes to read (must be multiple of LBA size)
            lba: starting LBA (default 0)
            dtype: numpy dtype for the returned array (default np.uint8)
        Returns:
            numpy.ndarray: data read with specified dtype
        """
        if nbytes % self.lba_nbytes != 0:
            raise ValueError("nbytes must be a multiple of LBA size")

        total_blocks = nbytes // self.lba_nbytes

        # Allocate large buffer once for all data
        total_buf_ptr = xnvme_core.buf_alloc(self.dev, nbytes)
        total_buf_mv = xnvme_core.get_buffer_view(total_buf_ptr, nbytes)

        # Use synchronous processing for reliability
        current_lba = lba
        data_offset = 0
        remaining_blocks = total_blocks

        while remaining_blocks > 0:
            # Calculate blocks for this chunk
            blocks_this_chunk = min(remaining_blocks, self.max_blocks)
            nlb = max(1, blocks_this_chunk) - 1
            bytes_this_chunk = blocks_this_chunk * self.lba_nbytes

            # Calculate chunk pointer by offsetting from base pointer
            chunk_ptr = total_buf_ptr + data_offset

            try:
                xnvme_core.sync_read(self.dev, chunk_ptr, self.nsid, current_lba, nlb)
            except Exception as e:
                xnvme_core.buf_free(self.dev, total_buf_ptr)
                raise RuntimeError(f"Read failed: {e}")

            current_lba += blocks_this_chunk
            data_offset += bytes_this_chunk
            remaining_blocks -= blocks_this_chunk

        # Return the entire buffer as numpy array and buffer pointer for user to free
        result = np.frombuffer(total_buf_mv, dtype=dtype)
        
        return result, total_buf_ptr

    def trim(self, lba: int, nblocks: int):
        """
        Issue a TRIM (deallocate) command for a range of LBAs in this namespace.
        Uses C extension for DSM commands.
        Args:
            lba: starting LBA
            nblocks: number of blocks to trim
        """

        # Validate parameters
        if lba < 0:
            raise ValueError(f"Invalid LBA: {lba}")
        if nblocks <= 0:
            raise ValueError(f"Invalid nblocks: {nblocks}")
        nlb = max(1, nblocks) - 1
        # Use C extension module (zero-based nlb)
        try:
            xnvme_core.nvm_dsm(self.dev, self.nsid, lba, nlb)
        except Exception as e:
            print(f"Failed to trim: {e}")
            raise

    def close(self):
        # Clean up thread-local queue if exists
        if hasattr(self._thread_local, 'queue'):
            xnvme_core.queue_term(self._thread_local.queue)
            del self._thread_local.queue

        xnvme_core.dev_close(self.dev)

    def async_write(self, buf, lba: int = 0):
        """
        Write data to the NVMe namespace using asynchronous processing.
        Args:
            buf: memoryview object
            lba: starting LBA (default 0)
        """
        if isinstance(buf, memoryview):
            data = buf
            nbytes = len(data) * data.itemsize
        else:
            raise ValueError("buf must be memoryview")
        
        if nbytes % self.lba_nbytes != 0:
            raise ValueError("Buffer size must be a multiple of LBA size")

        total_blocks = nbytes // self.lba_nbytes

        # Get queue
        queue = self.get_thread_queue()

        # Use asynchronous processing
        current_lba = lba
        data_offset = 0
        remaining_blocks = total_blocks
        commands_in_queue = 0

        while remaining_blocks > 0:
            # Calculate blocks for this chunk
            blocks_this_chunk = min(remaining_blocks, self.max_blocks)
            nlb = max(1, blocks_this_chunk) - 1
            bytes_this_chunk = blocks_this_chunk * self.lba_nbytes

            # Get chunk data
            data_mv = memoryview(data)
            chunk_mv = data_mv[data_offset:data_offset + bytes_this_chunk]
            #chunk_data = data_mv[data_offset:data_offset + bytes_this_chunk]

            # Let C extension allocate buffer and copy data
            xnvme_core.async_write(queue, self.dev, chunk_mv, self.nsid, current_lba, nlb)
            commands_in_queue += 1

            consecutive_zeros = 0
            while commands_in_queue >= self.queue_depth:
                completed = xnvme_core.queue_poke(queue, 0)
                #print(f"[DEBUG][nvme_binding][async_write] queue_poke: completed={completed}")
                if completed == 0:
                    consecutive_zeros += 1
                    if consecutive_zeros > 3:  # 3번 연속 0이면 짧게 대기
                        time.sleep(0.0001)  # 0.1ms
                else:
                    consecutive_zeros = 0
                commands_in_queue -= completed

            current_lba += blocks_this_chunk
            data_offset += bytes_this_chunk
            remaining_blocks -= blocks_this_chunk

        if commands_in_queue > 0:
            xnvme_core.queue_drain(queue)

    def async_read(self, nbytes: int, lba: int = 0, dtype=np.uint8):
        """
        Read data from the NVMe namespace using asynchronous processing.
        Args:
            nbytes: number of bytes to read (must be multiple of LBA size)
            lba: starting LBA (default 0)
            dtype: numpy dtype for the returned array (default np.uint8)
        Returns:
            numpy.ndarray: data read with specified dtype
        """
        if nbytes % self.lba_nbytes != 0:
            raise ValueError("nbytes must be a multiple of LBA size")

        total_blocks = nbytes // self.lba_nbytes

        # Get async queue
        queue = self.get_thread_queue()

        # Allocate large buffer once for all data
        total_buf_ptr = xnvme_core.buf_alloc(self.dev, nbytes)
        total_buf_mv = xnvme_core.get_buffer_view(total_buf_ptr, nbytes)

        # Use asynchronous processing
        current_lba = lba
        data_offset = 0
        remaining_blocks = total_blocks
        commands_in_queue = 0

        while remaining_blocks > 0:
            blocks_this_chunk = min(remaining_blocks, self.max_blocks)
            nlb = max(1, blocks_this_chunk) - 1
            bytes_this_chunk = blocks_this_chunk * self.lba_nbytes

            # Calculate chunk pointer by offsetting from base pointer
            chunk_ptr = total_buf_ptr + data_offset

            # Queue read command with pre-allocated buffer
            xnvme_core.async_read(queue, self.dev, chunk_ptr, self.nsid, current_lba, nlb)
            commands_in_queue += 1

            consecutive_zeros = 0
            while commands_in_queue >= self.queue_depth:
                completed = xnvme_core.queue_poke(queue, 0)
                #print(f"[DEBUG][nvme_binding][async_read] queue_poke: completed={completed}")
                if completed == 0:
                    consecutive_zeros += 1
                    if consecutive_zeros > 3:  # 3번 연속 0이면 짧게 대기
                        time.sleep(0.0001)  # 0.1ms
                else:
                    consecutive_zeros = 0
                commands_in_queue -= completed

            current_lba += blocks_this_chunk
            data_offset += bytes_this_chunk
            remaining_blocks -= blocks_this_chunk

        if commands_in_queue > 0:
            xnvme_core.queue_drain(queue)

        # Return the entire buffer as numpy array (zero-copy) and buffer pointer
        result = np.frombuffer(total_buf_mv, dtype=dtype)
        
        return result, total_buf_ptr