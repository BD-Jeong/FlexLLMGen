#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <libxnvme.h>
#include <string.h>
#include <numpy/arrayobject.h>

// Python 모듈 정의
static PyObject* xnvme_core_open_device(PyObject* self, PyObject* args) {
    const char* device_path;
    int use_io_uring;
    
    if (!PyArg_ParseTuple(args, "si", &device_path, &use_io_uring)) {
        return NULL;
    }
    
    struct xnvme_dev* dev;
    
    if (use_io_uring) {
        // io_uring 백엔드로 디바이스 열기 (공식 문서에 따름)
        struct xnvme_opts opts = {0};
        opts.sync = "nvme";
        opts.async = "io_uring_cmd";
        dev = xnvme_dev_open(device_path, &opts);
    } else {
        // 기본 백엔드로 디바이스 열기
        dev = xnvme_dev_open(device_path, NULL);
    }
    
    if (!dev) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to open device");
        return NULL;
    }
    
    // 디바이스 포인터를 Python long으로 반환
    return PyLong_FromVoidPtr(dev);
}

static PyObject* xnvme_core_close_device(PyObject* self, PyObject* args) {
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

static PyObject* xnvme_core_get_geometry(PyObject* self, PyObject* args) {
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
    
    // Python 딕셔너리로 geometry 정보 반환
    PyObject* result = PyDict_New();
    PyDict_SetItemString(result, "nbytes", PyLong_FromUnsignedLong(geo->nbytes));
    PyDict_SetItemString(result, "nbytes_oob", PyLong_FromUnsignedLong(geo->nbytes_oob));
    PyDict_SetItemString(result, "lba_nbytes", PyLong_FromUnsignedLong(geo->lba_nbytes));
    PyDict_SetItemString(result, "lba_extended", PyLong_FromUnsignedLong(geo->lba_extended));
    PyDict_SetItemString(result, "type", PyLong_FromUnsignedLong(geo->type));
    PyDict_SetItemString(result, "nsect", PyLong_FromUnsignedLong(geo->nsect));
    
    return result;
}

static PyObject* xnvme_core_sync_write(PyObject* self, PyObject* args) {
    PyObject* dev_ptr_obj;
    uint32_t nsid;
    uint64_t lba;
    uint16_t nlb;
    const char* data;
    Py_ssize_t data_len;
    
    if (!PyArg_ParseTuple(args, "OIKHs#", &dev_ptr_obj, &nsid, &lba, &nlb, &data, &data_len)) {
        return NULL;
    }
    
    struct xnvme_dev* dev = (struct xnvme_dev*)PyLong_AsVoidPtr(dev_ptr_obj);
    if (!dev) {
        PyErr_SetString(PyExc_RuntimeError, "Invalid device pointer");
        return NULL;
    }
    
    // 버퍼 할당 및 데이터 복사
    size_t buf_size = (nlb + 1) * xnvme_dev_get_geo(dev)->nbytes;
    void* buf = xnvme_buf_alloc(dev, buf_size);
    if (!buf) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to allocate buffer");
        return NULL;
    }
    
    memcpy(buf, data, data_len);
    
    // Command context 생성
    struct xnvme_cmd_ctx ctx = xnvme_cmd_ctx_from_dev(dev);
    
    // 쓰기 명령 실행
    int err = xnvme_nvm_write(&ctx, nsid, lba, nlb, buf, NULL);
    if (err || xnvme_cmd_ctx_cpl_status(&ctx)) {
        xnvme_buf_free(dev, buf);
        PyErr_SetString(PyExc_RuntimeError, "Write command failed");
        return NULL;
    }
    
    // 버퍼 해제
    xnvme_buf_free(dev, buf);
    
    Py_RETURN_NONE;
}

static PyObject* xnvme_core_sync_read(PyObject* self, PyObject* args) {
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
    
    // 버퍼 할당
    size_t buf_size = (nlb + 1) * xnvme_dev_get_geo(dev)->nbytes;
    void* buf = xnvme_buf_alloc(dev, buf_size);
    if (!buf) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to allocate buffer");
        return NULL;
    }
    
    // Command context 생성
    struct xnvme_cmd_ctx ctx = xnvme_cmd_ctx_from_dev(dev);
    
    // 읽기 명령 실행
    int err = xnvme_nvm_read(&ctx, nsid, lba, nlb, buf, NULL);
    if (err || xnvme_cmd_ctx_cpl_status(&ctx)) {
        xnvme_buf_free(dev, buf);
        PyErr_SetString(PyExc_RuntimeError, "Read command failed");
        return NULL;
    }
    
    // 데이터를 Python bytes로 변환
    PyObject* result = PyBytes_FromStringAndSize(buf, buf_size);
    
    // 버퍼 해제
    xnvme_buf_free(dev, buf);
    
    return result;
}

static PyObject* xnvme_core_trim(PyObject* self, PyObject* args) {
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
    
    // Command context 생성
    struct xnvme_cmd_ctx ctx = xnvme_cmd_ctx_from_dev(dev);
    
    // DSM range 구조체 생성 (실제 TRIM 명령)
    struct xnvme_spec_dsm_range dsm_range = {0};
    dsm_range.cattr = 0;  // Context attributes (0 for TRIM)
    dsm_range.llb = nlb;  // Number of logical blocks
    dsm_range.slba = lba; // Starting LBA
    
    // TRIM 명령 실행 (DSM - Dataset Management)
    int err = xnvme_nvm_dsm(&ctx, nsid, &dsm_range, 1, true, false, false);
    if (err || xnvme_cmd_ctx_cpl_status(&ctx)) {
        PyErr_SetString(PyExc_RuntimeError, "TRIM command failed");
        return NULL;
    }
    
    Py_RETURN_NONE;
}

static PyObject* xnvme_core_create_queue(PyObject* self, PyObject* args) {
    PyObject* dev_ptr_obj;
    uint32_t qdepth;
    
    if (!PyArg_ParseTuple(args, "OI", &dev_ptr_obj, &qdepth)) {
        return NULL;
    }
    
    struct xnvme_dev* dev = (struct xnvme_dev*)PyLong_AsVoidPtr(dev_ptr_obj);
    if (!dev) {
        PyErr_SetString(PyExc_RuntimeError, "Invalid device pointer");
        return NULL;
    }
    
    // 큐 생성
    struct xnvme_queue* queue = NULL;
    printf("qdepth: %d\n", qdepth);
    int err = xnvme_queue_init(dev, qdepth, 0, &queue);
    if (err || !queue) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to create queue");
        return NULL;
    }
    
    // 큐 포인터를 Python long으로 반환
    return PyLong_FromVoidPtr(queue);
}

static PyObject* xnvme_core_destroy_queue(PyObject* self, PyObject* args) {
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

static PyObject* xnvme_core_free_buffer(PyObject* self, PyObject* args) {
    PyObject* dev_ptr_obj;
    PyObject* buf_ptr_obj;
    
    if (!PyArg_ParseTuple(args, "OO", &dev_ptr_obj, &buf_ptr_obj)) {
        return NULL;
    }
    
    struct xnvme_dev* dev = (struct xnvme_dev*)PyLong_AsVoidPtr(dev_ptr_obj);
    void* buf = PyLong_AsVoidPtr(buf_ptr_obj);
    
    if (!dev || !buf) {
        PyErr_SetString(PyExc_RuntimeError, "Invalid device or buffer pointer");
        return NULL;
    }
    
    // 버퍼 해제
    xnvme_buf_free(dev, buf);
    
    Py_RETURN_NONE;
}

// 진짜 async batch 처리를 위한 새로운 함수들
static PyObject* xnvme_core_create_async_queue(PyObject* self, PyObject* args) {
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
    
    printf("qdepth: %d\n", queue_depth);
    
    // 큐 생성 - 기본 백엔드 사용 (io_uring_cmd 대신)
    struct xnvme_queue* queue = NULL;
    int err = xnvme_queue_init(dev, queue_depth, 0, &queue);
    if (err || !queue) {
        printf("Failed to create queue: err=%d, queue=%p\n", err, queue);
        PyErr_SetString(PyExc_RuntimeError, "Failed to create async queue");
        return NULL;
    }
    
    printf("Queue created successfully: %p\n", queue);
    
    // 큐 포인터를 Python long으로 반환
    return PyLong_FromVoidPtr(queue);
}

static PyObject* xnvme_core_queue_write_command(PyObject* self, PyObject* args) {
    PyObject* queue_ptr_obj;
    PyObject* dev_ptr_obj;
    uint32_t nsid;
    uint64_t lba;
    uint16_t nlb;
    const char* data;
    Py_ssize_t data_len;
    
    if (!PyArg_ParseTuple(args, "OOIKHs#", &queue_ptr_obj, &dev_ptr_obj, &nsid, &lba, &nlb, &data, &data_len)) {
        return NULL;
    }
    
    struct xnvme_queue* queue = (struct xnvme_queue*)PyLong_AsVoidPtr(queue_ptr_obj);
    struct xnvme_dev* dev = (struct xnvme_dev*)PyLong_AsVoidPtr(dev_ptr_obj);
    if (!queue || !dev) {
        PyErr_SetString(PyExc_RuntimeError, "Invalid queue or device pointer");
        return NULL;
    }
    
    // 명령 컨텍스트 가져오기
    struct xnvme_cmd_ctx* ctx = xnvme_queue_get_cmd_ctx(queue);
    if (!ctx) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to get command context");
        return NULL;
    }
    
    // 버퍼 할당 및 데이터 복사
    size_t buf_size = (nlb + 1) * xnvme_dev_get_geo(dev)->nbytes;
    void* buf = xnvme_buf_alloc(dev, buf_size);
    if (!buf) {
        xnvme_queue_put_cmd_ctx(queue, ctx);
        PyErr_SetString(PyExc_RuntimeError, "Failed to allocate buffer");
        return NULL;
    }
    
    memcpy(buf, data, data_len);
    
    // io_uring 백엔드에서 명령을 수동으로 설정
    ctx->cmd.common.opcode = 0x01;  // XNVME_SPEC_NVM_OPC_WRITE
    ctx->cmd.common.nsid = nsid;
    ctx->cmd.nvm.slba = lba;
    ctx->cmd.nvm.nlb = nlb;
    ctx->cmd.common.dptr.prp.prp1 = (uint64_t)buf;
    ctx->cmd.common.dptr.prp.prp2 = 0;
    
    // 명령을 큐에 제출 (submit은 안 함)
    int err = xnvme_queue_put_cmd_ctx(queue, ctx);
    if (err) {
        xnvme_buf_free(dev, buf);
        PyErr_SetString(PyExc_RuntimeError, "Failed to queue command");
        return NULL;
    }
    
    // 버퍼 포인터를 Python long으로 반환 (나중에 해제할 때 사용)
    return PyLong_FromVoidPtr(buf);
}

static PyObject* xnvme_core_queue_read_command(PyObject* self, PyObject* args) {
    PyObject* queue_ptr_obj;
    PyObject* dev_ptr_obj;
    uint32_t nsid;
    uint64_t lba;
    uint16_t nlb;
    
    if (!PyArg_ParseTuple(args, "OOIKH", &queue_ptr_obj, &dev_ptr_obj, &nsid, &lba, &nlb)) {
        return NULL;
    }
    
    struct xnvme_queue* queue = (struct xnvme_queue*)PyLong_AsVoidPtr(queue_ptr_obj);
    struct xnvme_dev* dev = (struct xnvme_dev*)PyLong_AsVoidPtr(dev_ptr_obj);
    if (!queue || !dev) {
        PyErr_SetString(PyExc_RuntimeError, "Invalid queue or device pointer");
        return NULL;
    }
    
    // 명령 컨텍스트 가져오기
    struct xnvme_cmd_ctx* ctx = xnvme_queue_get_cmd_ctx(queue);
    if (!ctx) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to get command context");
        return NULL;
    }
    
    // 버퍼 할당
    size_t buf_size = (nlb + 1) * xnvme_dev_get_geo(dev)->nbytes;
    void* buf = xnvme_buf_alloc(dev, buf_size);
    if (!buf) {
        xnvme_queue_put_cmd_ctx(queue, ctx);
        PyErr_SetString(PyExc_RuntimeError, "Failed to allocate buffer");
        return NULL;
    }
    
    // io_uring 백엔드에서 명령을 수동으로 설정
    ctx->cmd.common.opcode = 0x02;  // XNVME_SPEC_NVM_OPC_READ
    ctx->cmd.common.nsid = nsid;
    ctx->cmd.nvm.slba = lba;
    ctx->cmd.nvm.nlb = nlb;
    ctx->cmd.common.dptr.prp.prp1 = (uint64_t)buf;
    ctx->cmd.common.dptr.prp.prp2 = 0;
    
    // 명령을 큐에 제출 (submit은 안 함)
    int err = xnvme_queue_put_cmd_ctx(queue, ctx);
    if (err) {
        xnvme_buf_free(dev, buf);
        PyErr_SetString(PyExc_RuntimeError, "Failed to queue command");
        return NULL;
    }
    
    // 버퍼 포인터를 Python long으로 반환 (나중에 결과를 가져올 때 사용)
    return PyLong_FromVoidPtr(buf);
}

static PyObject* xnvme_core_submit_batch(PyObject* self, PyObject* args) {
    PyObject* queue_ptr_obj;
    PyObject* dev_ptr_obj;
    int batch_size;
    
    if (!PyArg_ParseTuple(args, "OOi", &queue_ptr_obj, &dev_ptr_obj, &batch_size)) {
        return NULL;
    }
    
    struct xnvme_queue* queue = (struct xnvme_queue*)PyLong_AsVoidPtr(queue_ptr_obj);
    struct xnvme_dev* dev = (struct xnvme_dev*)PyLong_AsVoidPtr(dev_ptr_obj);
    if (!queue || !dev) {
        PyErr_SetString(PyExc_RuntimeError, "Invalid queue or device pointer");
        return NULL;
    }
    
    // 지정된 크기만큼만 submit
    int completions = xnvme_queue_poke(queue, batch_size);
    if (completions < 0) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to submit commands");
        return NULL;
    }
    
    Py_RETURN_NONE;
}

static PyObject* xnvme_core_wait_completion(PyObject* self, PyObject* args) {
    PyObject* queue_ptr_obj;
    PyObject* dev_ptr_obj;
    
    if (!PyArg_ParseTuple(args, "OO", &queue_ptr_obj, &dev_ptr_obj)) {
        return NULL;
    }
    
    struct xnvme_queue* queue = (struct xnvme_queue*)PyLong_AsVoidPtr(queue_ptr_obj);
    struct xnvme_dev* dev = (struct xnvme_dev*)PyLong_AsVoidPtr(dev_ptr_obj);
    if (!queue || !dev) {
        PyErr_SetString(PyExc_RuntimeError, "Invalid queue or device pointer");
        return NULL;
    }
    
    // 모든 명령 완료 대기
    int err = xnvme_queue_drain(queue);
    if (err != 0) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to drain queue");
        return NULL;
    }
    
    Py_RETURN_NONE;
}

static PyObject* xnvme_core_destroy_async_queue(PyObject* self, PyObject* args) {
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

// 메서드 정의
static PyMethodDef XnvmeCoreMethods[] = {
    {"open_device", xnvme_core_open_device, METH_VARARGS, "Open NVMe device"},
    {"close_device", xnvme_core_close_device, METH_VARARGS, "Close NVMe device"},
    {"get_geometry", xnvme_core_get_geometry, METH_VARARGS, "Get device geometry"},
    {"sync_write", xnvme_core_sync_write, METH_VARARGS, "Synchronous write"},
    {"sync_read", xnvme_core_sync_read, METH_VARARGS, "Synchronous read"},
    {"trim", xnvme_core_trim, METH_VARARGS, "TRIM (write zeroes)"},
    {"create_queue", xnvme_core_create_queue, METH_VARARGS, "Create async queue"},
    {"destroy_queue", xnvme_core_destroy_queue, METH_VARARGS, "Destroy async queue"},
    {"free_buffer", xnvme_core_free_buffer, METH_VARARGS, "Free allocated buffer"},
    {"create_async_queue", xnvme_core_create_async_queue, METH_VARARGS, "Create async queue for batch processing"},
    {"queue_write_command", xnvme_core_queue_write_command, METH_VARARGS, "Queue write command (no submit)"},
    {"queue_read_command", xnvme_core_queue_read_command, METH_VARARGS, "Queue read command (no submit)"},
    {"submit_batch", xnvme_core_submit_batch, METH_VARARGS, "Submit batch of commands"},
    {"wait_completion", xnvme_core_wait_completion, METH_VARARGS, "Wait for all commands to complete"},
    {"destroy_async_queue", xnvme_core_destroy_async_queue, METH_VARARGS, "Destroy async queue"},
    {NULL, NULL, 0, NULL}
};

// 모듈 정의
static struct PyModuleDef xnvme_core_module = {
    PyModuleDef_HEAD_INIT,
    "xnvme_core",
    "Core xNVMe functions for Python",
    -1,
    XnvmeCoreMethods
};

// 모듈 초기화 함수
PyMODINIT_FUNC PyInit_xnvme_core(void) {
    return PyModule_Create(&xnvme_core_module);
} 