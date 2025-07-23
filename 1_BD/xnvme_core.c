#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <libxnvme.h>
#include <string.h>
#include <numpy/arrayobject.h>

static PyObject* xnvme_core_dev_open(PyObject* self, PyObject* args) {
    const char* device_path;
    int use_io_uring;

    if (!PyArg_ParseTuple(args, "si", &device_path, &use_io_uring)) {
        return NULL;
    }

    struct xnvme_dev* dev;

    if (use_io_uring) {
        struct xnvme_opts opts = {0};
        opts.sync = "nvme";
        opts.async = "io_uring_cmd";
        // options
        opts.poll_io = 1;          // io-polling 
        opts.poll_sq = 1;          // sqthread-polling 
        opts.direct = 1;
        opts.register_buffers = 1; 

        dev = xnvme_dev_open(device_path, &opts);
    } else {
        dev = xnvme_dev_open(device_path, NULL);
    }

    if (!dev) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to open device");
        return NULL;
    }
    const struct xnvme_opts *opts = xnvme_dev_get_opts(dev);
    printf("[DEBUG][xnvme_core][dev_open] device opened with async backend: %s\n", opts->async);

    return PyLong_FromVoidPtr(dev);
}

static PyObject* xnvme_core_dev_close(PyObject* self, PyObject* args) {
    PyObject* dev_ptr_obj;

    if (!PyArg_ParseTuple(args, "O", &dev_ptr_obj)) {
        return NULL;
    }

    struct xnvme_dev* dev = (struct xnvme_dev*)PyLong_AsVoidPtr(dev_ptr_obj);
    if (dev) {
        xnvme_dev_close(dev);
    }

    Py_RETURN_NONE;
}

static PyObject* xnvme_core_dev_get_geo(PyObject* self, PyObject* args) {
    PyObject* dev_ptr_obj;

    if (!PyArg_ParseTuple(args, "O", &dev_ptr_obj)) {
        return NULL;
    }

    struct xnvme_dev* dev = (struct xnvme_dev*)PyLong_AsVoidPtr(dev_ptr_obj);
    if (!dev) {
        PyErr_SetString(PyExc_RuntimeError, "Invalid device pointer");
        return NULL;
    }

    const struct xnvme_geo* geo = xnvme_dev_get_geo(dev);
    if (!geo) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to get geometry");
        return NULL;
    }

    PyObject* result = PyDict_New();
    PyDict_SetItemString(result, "nbytes", PyLong_FromUnsignedLong(geo->nbytes));
    PyDict_SetItemString(result, "nbytes_oob", PyLong_FromUnsignedLong(geo->nbytes_oob));
    PyDict_SetItemString(result, "lba_nbytes", PyLong_FromUnsignedLong(geo->lba_nbytes));
    PyDict_SetItemString(result, "lba_extended", PyLong_FromUnsignedLong(geo->lba_extended));
    PyDict_SetItemString(result, "type", PyLong_FromUnsignedLong(geo->type));
    PyDict_SetItemString(result, "nsect", PyLong_FromUnsignedLong(geo->nsect));
    PyDict_SetItemString(result, "mdts", PyLong_FromUnsignedLong(geo->mdts_nbytes));
    
    return result;
}

static PyObject* xnvme_core_sync_write(PyObject* self, PyObject* args) {
    PyObject* dev_ptr_obj;
    PyObject* data_obj;
    uint32_t nsid;
    uint64_t lba;
    uint16_t nlb;

    if (!PyArg_ParseTuple(args, "OOIKH", &dev_ptr_obj, &data_obj, &nsid, &lba, &nlb)) {
        return NULL;
    }

    struct xnvme_dev* dev = (struct xnvme_dev*)PyLong_AsVoidPtr(dev_ptr_obj);
    if (!dev) {
        PyErr_SetString(PyExc_RuntimeError, "Invalid device pointer");
        return NULL;
    }

    // Get buffer from Python object
    Py_buffer view;
    if (PyObject_GetBuffer(data_obj, &view, PyBUF_SIMPLE) != 0) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to get buffer from data object");
        return NULL;
    }

    // Calculate buffer size and allocate buffer
    size_t buf_size = (nlb + 1) * xnvme_dev_get_geo(dev)->lba_nbytes;
    void* buf = xnvme_buf_alloc(dev, buf_size);
    if (!buf) {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_RuntimeError, "Failed to allocate buffer");
        return NULL;
    }

    // Copy data to allocated buffer
    memcpy(buf, view.buf, buf_size);

    PyBuffer_Release(&view);

    struct xnvme_cmd_ctx ctx = xnvme_cmd_ctx_from_dev(dev);

    int err = xnvme_nvm_write(&ctx, nsid, lba, nlb, buf, NULL);
    if (err || xnvme_cmd_ctx_cpl_status(&ctx)) {
        printf("[DEBUG][xnvme_core][sync_write] FAILED: err=%d, status=%d\n", err, xnvme_cmd_ctx_cpl_status(&ctx));
        xnvme_buf_free(dev, buf);
        PyErr_SetString(PyExc_RuntimeError, "Write command failed");
        return NULL;
    }

    xnvme_buf_free(dev, buf);
    Py_RETURN_NONE;
}

static PyObject* xnvme_core_sync_read(PyObject* self, PyObject* args) {
    PyObject* dev_ptr_obj;
    PyObject* buf_ptr_obj;
    uint32_t nsid;
    uint64_t lba;
    uint16_t nlb;

    if (!PyArg_ParseTuple(args, "OOIKH", &dev_ptr_obj, &buf_ptr_obj, &nsid, &lba, &nlb)) {
        return NULL;
    }

    struct xnvme_dev* dev = (struct xnvme_dev*)PyLong_AsVoidPtr(dev_ptr_obj);
    void* buf = (void*)PyLong_AsVoidPtr(buf_ptr_obj);
    if (!dev || !buf) {
        PyErr_SetString(PyExc_RuntimeError, "Invalid device or buffer pointer");
        return NULL;
    }

    struct xnvme_cmd_ctx ctx = xnvme_cmd_ctx_from_dev(dev);

    int err = xnvme_nvm_read(&ctx, nsid, lba, nlb, buf, NULL);
    if (err || xnvme_cmd_ctx_cpl_status(&ctx)) {
        printf("[DEBUG][xnvme_core][sync_read] FAILED: err=%d, status=%d\n", err, xnvme_cmd_ctx_cpl_status(&ctx));
        PyErr_SetString(PyExc_RuntimeError, "Read command failed");
        return NULL;
    }

    Py_RETURN_NONE;
}

static PyObject* xnvme_core_nvm_dsm(PyObject* self, PyObject* args) {
    PyObject* dev_ptr_obj;
    uint32_t nsid;
    uint64_t lba;
    uint16_t nlb;

    if (!PyArg_ParseTuple(args, "OIKH", &dev_ptr_obj, &nsid, &lba, &nlb)) {
        return NULL;
    }

    struct xnvme_dev* dev = (struct xnvme_dev*)PyLong_AsVoidPtr(dev_ptr_obj);
    if (!dev) {
        PyErr_SetString(PyExc_RuntimeError, "Invalid device pointer");
        return NULL;
    }

    struct xnvme_cmd_ctx ctx = xnvme_cmd_ctx_from_dev(dev);

    // DSM range
    struct xnvme_spec_dsm_range dsm_range = {0};
    dsm_range.cattr = 0;  // Context attributes (0 for TRIM)
    dsm_range.llb = nlb;  // Number of logical blocks
    dsm_range.slba = lba; // Starting LBA

    // Trim command (DSM - Dataset Management)
    int err = xnvme_nvm_dsm(&ctx, nsid, &dsm_range, 1, true, false, false);
    if (err || xnvme_cmd_ctx_cpl_status(&ctx)) {
        PyErr_SetString(PyExc_RuntimeError, "TRIM command failed");
        return NULL;
    }

    Py_RETURN_NONE;
}

static PyObject* xnvme_core_queue_init(PyObject* self, PyObject* args) {
    PyObject* dev_ptr_obj;
    int queue_depth;

    if (!PyArg_ParseTuple(args, "Oi", &dev_ptr_obj, &queue_depth)) {
        return NULL;
    }

    struct xnvme_dev* dev = (struct xnvme_dev*)PyLong_AsVoidPtr(dev_ptr_obj);
    if (!dev) {
        PyErr_SetString(PyExc_RuntimeError, "Invalid device pointer");
        return NULL;
    }
    struct xnvme_queue* queue = NULL;
    int err = xnvme_queue_init(dev, queue_depth, 0, &queue);
    if (err || !queue) {
        printf("[DEBUG][xnvme_core][queue_init] Failed to create queue: err=%d, queue=%p\n", err, queue);
        PyErr_SetString(PyExc_RuntimeError, "Failed to create async queue");
        return NULL;
    }
    return PyLong_FromVoidPtr(queue);
}

static PyObject* xnvme_core_queue_term(PyObject* self, PyObject* args) {
    PyObject* queue_ptr_obj;

    if (!PyArg_ParseTuple(args, "O", &queue_ptr_obj)) {
        return NULL;
    }

    struct xnvme_queue* queue = (struct xnvme_queue*)PyLong_AsVoidPtr(queue_ptr_obj);
    if (queue) {
        xnvme_queue_term(queue);
    }

    Py_RETURN_NONE;
}

static PyObject* xnvme_core_buf_free(PyObject* self, PyObject* args) {
    PyObject* dev_ptr_obj;
    PyObject* buf_ptr_obj;

    if (!PyArg_ParseTuple(args, "OO", &dev_ptr_obj, &buf_ptr_obj)) {
        return NULL;
    }

    struct xnvme_dev* dev = (struct xnvme_dev*)PyLong_AsVoidPtr(dev_ptr_obj);
    void* buf = PyLong_AsVoidPtr(buf_ptr_obj);

    if (dev && buf) {
        // printf("[DEBUG][xnvme_core][core_buf_free] buf_ptr=%p\n", buf);
        xnvme_buf_free(dev, buf);
    }

    Py_RETURN_NONE;
}

static PyObject* xnvme_core_buf_alloc(PyObject* self, PyObject* args) {
    PyObject* dev_ptr_obj;
    size_t size;

    if (!PyArg_ParseTuple(args, "On", &dev_ptr_obj, &size)) {
        return NULL;
    }

    struct xnvme_dev* dev = (struct xnvme_dev*)PyLong_AsVoidPtr(dev_ptr_obj);
    if (!dev) {
        PyErr_SetString(PyExc_RuntimeError, "Invalid device pointer");
        return NULL;
    }

    void* buf = xnvme_buf_alloc(dev, size);
    if (!buf) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to allocate buffer");
        return NULL;
    }

    return PyLong_FromVoidPtr(buf);
}

static void write_cb(struct xnvme_cmd_ctx *ctx, void *cb_arg) {
    if (ctx->cpl.status.sc) {
        fprintf(stderr, "[DEBUG][xnvme_core][write_cb] Write failed! status: %d (0x%02x)\n", 
                ctx->cpl.status.sc, ctx->cpl.status.sc);
        fprintf(stderr, "[DEBUG][xnvme_core][write_cb] Command details: nsid=%u, slba=%lu, nlb=%u\n",
                ctx->cmd.common.nsid, ctx->cmd.nvm.slba, ctx->cmd.nvm.nlb);
    }

    if (cb_arg) {
        xnvme_buf_free(ctx->dev, cb_arg);
    }

    xnvme_queue_put_cmd_ctx(ctx->async.queue, ctx);
}

static void read_cb(struct xnvme_cmd_ctx *ctx, void *cb_arg) {
    if (ctx->cpl.status.sc) {
        fprintf(stderr, "[DEBUG][xnvme_core][read_cb] Read failed! status: %d\n", ctx->cpl.status.sc);
    }
    // async io: buffer for read is freed by user
    xnvme_queue_put_cmd_ctx(ctx->async.queue, ctx);
}

static PyObject* xnvme_core_async_write(PyObject* self, PyObject* args) {
    PyObject* queue_ptr_obj;
    PyObject* dev_ptr_obj;
    PyObject* data_obj;
    uint32_t nsid;
    uint64_t lba;
    uint16_t nlb;

    if (!PyArg_ParseTuple(args, "OOOIKH", &queue_ptr_obj, &dev_ptr_obj, &data_obj, &nsid, &lba, &nlb)) {
        return NULL;
    }

    struct xnvme_queue* queue = (struct xnvme_queue*)PyLong_AsVoidPtr(queue_ptr_obj);
    struct xnvme_dev* dev = (struct xnvme_dev*)PyLong_AsVoidPtr(dev_ptr_obj);
    if (!queue || !dev) {
        PyErr_SetString(PyExc_RuntimeError, "Invalid queue or device pointer");
        return NULL;
    }

    // Get buffer from Python object
    Py_buffer view;
    if (PyObject_GetBuffer(data_obj, &view, PyBUF_SIMPLE) != 0) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to get buffer from data object");
        return NULL;
    }

    // [TEST] Try xnvme_mem_map/unmap on the Python buffer
    //int map_err = xnvme_mem_map(NULL, view.buf, view.len);
    //printf("[DEBUG][xnvme_core][async_write] mem_map: err=%d, buf=%p, len=%zd\n", map_err, view.buf, view.len);
    //xnvme_mem_unmap(NULL, view.buf);
    //printf("[DEBUG][xnvme_core][async_write] mem_unmap done\n");


    // Calculate buffer size and allocate buffer
    size_t buf_size = (nlb + 1) * xnvme_dev_get_geo(dev)->lba_nbytes;
    void* buf = xnvme_buf_alloc(dev, buf_size);
    if (!buf) {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_RuntimeError, "Failed to allocate buffer");
        return NULL;
    }

    // Copy data to allocated buffer
    memcpy(buf, view.buf, buf_size);

    PyBuffer_Release(&view);

    struct xnvme_cmd_ctx* ctx = xnvme_queue_get_cmd_ctx(queue);
    if (!ctx) {
        printf("[DEBUG][xnvme_core][async_write] Failed to get command context from queue\n");
        xnvme_buf_free(dev, buf);
        PyErr_SetString(PyExc_RuntimeError, "Failed to get command context");
        return NULL;
    }

    ctx->async.cb = write_cb;
    ctx->async.cb_arg = buf;

    int err = xnvme_nvm_write(ctx, nsid, lba, nlb, buf, NULL);
    if (err) {
        printf("[DEBUG][xnvme_core][async_write] Write failed: err=%d\n", err);
        xnvme_buf_free(dev, buf);
        PyErr_SetString(PyExc_RuntimeError, "Failed to write data");
        return NULL;
    }

    Py_RETURN_NONE;
}

static PyObject* xnvme_core_async_read(PyObject* self, PyObject* args) {
    PyObject* queue_ptr_obj;
    PyObject* dev_ptr_obj;
    PyObject* buf_ptr_obj;
    uint32_t nsid;
    uint64_t lba;
    uint16_t nlb;

    if (!PyArg_ParseTuple(args, "OOOIKH", &queue_ptr_obj, &dev_ptr_obj, &buf_ptr_obj, &nsid, &lba, &nlb)) {
        return NULL;
    }

    struct xnvme_queue* queue = (struct xnvme_queue*)PyLong_AsVoidPtr(queue_ptr_obj);
    struct xnvme_dev* dev = (struct xnvme_dev*)PyLong_AsVoidPtr(dev_ptr_obj);
    void* buf = (void*)PyLong_AsVoidPtr(buf_ptr_obj);
    if (!queue || !dev || !buf) {
        PyErr_SetString(PyExc_RuntimeError, "Invalid queue, device, or buffer pointer");
        return NULL;
    }

    struct xnvme_cmd_ctx* ctx = xnvme_queue_get_cmd_ctx(queue);
    if (!ctx) {
        printf("[DEBUG][xnvme_core][async_read] Failed to get command context from queue\n");
        PyErr_SetString(PyExc_RuntimeError, "Failed to get command context");
        return NULL;
    }

    ctx->async.cb = read_cb;
    ctx->async.cb_arg = NULL;  // buf is managed by caller

    int err = xnvme_nvm_read(ctx, nsid, lba, nlb, buf, NULL);
    if (err) {
        printf("[DEBUG][xnvme_core][async_read] Read failed: err=%d\n", err);
        PyErr_SetString(PyExc_RuntimeError, "Failed to read data");
        return NULL;
    }

    Py_RETURN_NONE;
}

static PyObject* xnvme_core_queue_poke(PyObject* self, PyObject* args) {
    PyObject* queue_ptr_obj;
    uint32_t max;

    if (!PyArg_ParseTuple(args, "OI", &queue_ptr_obj, &max)) {
        return NULL;
    }

    struct xnvme_queue* queue = (struct xnvme_queue*)PyLong_AsVoidPtr(queue_ptr_obj);
    if (!queue) {
        PyErr_SetString(PyExc_RuntimeError, "Invalid queue pointer");
        return NULL;
    }

    int completed = xnvme_queue_poke(queue, max);
    return PyLong_FromLong(completed);
}

static PyObject* xnvme_core_queue_drain(PyObject* self, PyObject* args) {
    PyObject* queue_ptr_obj;

    if (!PyArg_ParseTuple(args, "O", &queue_ptr_obj)) {
        return NULL;
    }

    struct xnvme_queue* queue = (struct xnvme_queue*)PyLong_AsVoidPtr(queue_ptr_obj);
    if (!queue) {
        PyErr_SetString(PyExc_RuntimeError, "Invalid queue pointer");
        return NULL;
    }

    while (xnvme_queue_get_outstanding(queue) > 0) {
        xnvme_queue_poke(queue, 0);  // 0 = no limit (process all completions)
    }

    Py_RETURN_NONE;
}
/*
static PyObject* xnvme_core_get_buffer_data(PyObject* self, PyObject* args) {
    PyObject* buf_ptr_obj;
    size_t size;

    if (!PyArg_ParseTuple(args, "On", &buf_ptr_obj, &size)) {
        return NULL;
    }

    void* buf = PyLong_AsVoidPtr(buf_ptr_obj);
    if (!buf) {
        PyErr_SetString(PyExc_RuntimeError, "Invalid buffer pointer");
        return NULL;
    }

    PyObject* result = PyBytes_FromStringAndSize((char*)buf, size);
    if (!result) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to create bytes object");
        return NULL;
    }

    return result;
}
*/
static PyObject* xnvme_core_get_buffer_view(PyObject* self, PyObject* args) {
    PyObject* buf_ptr_obj;
    Py_ssize_t size;

    if (!PyArg_ParseTuple(args, "On", &buf_ptr_obj, &size)) {
        return NULL;
    }

    void* buf = PyLong_AsVoidPtr(buf_ptr_obj);
    if (!buf) {
        PyErr_SetString(PyExc_RuntimeError, "Invalid buffer pointer");
        return NULL;
    }

    // PyBUF_WRITE: 읽기/쓰기 가능한 memoryview 반환
    return PyMemoryView_FromMemory((char*)buf, size, PyBUF_WRITE);
}

static PyObject* xnvme_core_mem_map(PyObject* self, PyObject* args) {
    PyObject* data_obj;
    Py_ssize_t offset;
    Py_ssize_t size;

    if (!PyArg_ParseTuple(args, "Onn", &data_obj, &offset, &size)) {
        return NULL;
    }

    // Python 객체에서 버퍼 프로토콜을 통해 포인터 얻기
    Py_buffer view;
    if (PyObject_GetBuffer(data_obj, &view, PyBUF_SIMPLE) != 0) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to get buffer from data object");
        return NULL;
    }

    // 오프셋과 크기 검증
    if (offset < 0 || size < 0 || offset + size > view.len) {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_ValueError, "Invalid offset or size");
        return NULL;
    }

    // xnvme_mem_map을 사용하여 DMA 매핑 (dev는 NULL로 전달)
    int err = xnvme_mem_map(NULL, (char*)view.buf + offset, size);
    if (err != 0) {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_RuntimeError, "Failed to map memory for DMA");
        return NULL;
    }
    void* mapped_ptr = (char*)view.buf + offset;

    // 버퍼 뷰 해제 (포인터는 여전히 유효)
    PyBuffer_Release(&view);

    return PyLong_FromVoidPtr(mapped_ptr);
}

static PyObject* xnvme_core_mem_unmap(PyObject* self, PyObject* args) {
    PyObject* ptr_obj;

    if (!PyArg_ParseTuple(args, "O", &ptr_obj)) {
        return NULL;
    }

    void* mapped_ptr = PyLong_AsVoidPtr(ptr_obj);
    if (mapped_ptr) {
        xnvme_mem_unmap(NULL, mapped_ptr);
    }
    
    Py_RETURN_NONE;
}

// methods
static PyMethodDef XnvmeCoreMethods[] = {
    {"dev_open", xnvme_core_dev_open, METH_VARARGS, "Open NVMe device"},
    {"dev_close", xnvme_core_dev_close, METH_VARARGS, "Close NVMe device"},
    {"get_geo", xnvme_core_dev_get_geo, METH_VARARGS, "Get device geometry"},
    {"sync_write", xnvme_core_sync_write, METH_VARARGS, "Synchronous write"},
    {"sync_read", xnvme_core_sync_read, METH_VARARGS, "Synchronous read"},
    {"nvm_dsm", xnvme_core_nvm_dsm, METH_VARARGS, "TRIM"},
    {"queue_init", xnvme_core_queue_init, METH_VARARGS, "Create async queue"},
    {"queue_term", xnvme_core_queue_term, METH_VARARGS, "Destroy async queue"},
    {"buf_free", xnvme_core_buf_free, METH_VARARGS, "Free allocated buffer"},
    {"buf_alloc", xnvme_core_buf_alloc, METH_VARARGS, "Allocate buffer for device"},
    {"async_write", xnvme_core_async_write, METH_VARARGS, "Async write"},
    {"async_read", xnvme_core_async_read, METH_VARARGS, "Async read"},
    {"queue_drain", xnvme_core_queue_drain, METH_VARARGS, "Process outstanding commands"},
    {"queue_poke", xnvme_core_queue_poke, METH_VARARGS, "Process outstanding commands"},
    //{"get_buffer_data", xnvme_core_get_buffer_data, METH_VARARGS, "Get data from buffer"},
    {"get_buffer_view", xnvme_core_get_buffer_view, METH_VARARGS, "Get memoryview from buffer (zero-copy)"},
    {"mem_map", xnvme_core_mem_map, METH_VARARGS, "Map memory region from Python object (zero-copy)"},
    {"mem_unmap", xnvme_core_mem_unmap, METH_VARARGS, "Unmap memory region (no-op for safety)"},
    {NULL, NULL, 0, NULL}
};

// module definition
static struct PyModuleDef xnvme_core_module = {
    PyModuleDef_HEAD_INIT,
    "xnvme_core",
    "Core xNVMe functions for Python",
    -1,
    XnvmeCoreMethods
};

// module initialization function
PyMODINIT_FUNC PyInit_xnvme_core(void) {
    return PyModule_Create(&xnvme_core_module);
} 