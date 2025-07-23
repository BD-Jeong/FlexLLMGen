from setuptools import setup, Extension
import os
import numpy as np

# xNVMe library path
XNVME_INCLUDE_DIR = "/usr/local/include"
XNVME_LIB_DIR = "/usr/local/lib"

# Check environment variables
if 'XNVME_INCLUDE_DIR' in os.environ:
    XNVME_INCLUDE_DIR = os.environ['XNVME_INCLUDE_DIR']
if 'XNVME_LIB_DIR' in os.environ:
    XNVME_LIB_DIR = os.environ['XNVME_LIB_DIR']

# xnvme_core extension module definition (no package name, create in current folder)
xnvme_core = Extension(
    'xnvme_core',
    sources=['xnvme_core.c'],
    include_dirs=[XNVME_INCLUDE_DIR, np.get_include()],
    library_dirs=[XNVME_LIB_DIR],
    libraries=['xnvme', 'uring'],
    #extra_compile_args=['-std=c99'],
    extra_compile_args=[
        "-std=c99",
        "-O3",
        "-march=native",
        "-fomit-frame-pointer",
        "-flto", # option
    ],
    extra_link_args=[f"-Wl,-rpath,{XNVME_LIB_DIR}", "-flto"],
)

setup(
    name="xnvme_core",
    version="1.0.0",
    description="Python bindings for xNVMe core (local build)",
    ext_modules=[xnvme_core],
    install_requires=["numpy"],
)