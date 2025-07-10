from setuptools import setup, Extension
import os
import numpy as np

# xNVMe 라이브러리 경로 설정
XNVME_INCLUDE_DIR = "/usr/local/include"
XNVME_LIB_DIR = "/usr/local/lib"

# 환경 변수에서 경로 확인
if 'XNVME_INCLUDE_DIR' in os.environ:
    XNVME_INCLUDE_DIR = os.environ['XNVME_INCLUDE_DIR']
if 'XNVME_LIB_DIR' in os.environ:
    XNVME_LIB_DIR = os.environ['XNVME_LIB_DIR']

# xnvme_core 확장 모듈 정의 (패키지명 없이 현재 폴더에 생성)
xnvme_core = Extension(
    'xnvme_core',
    sources=['xnvme_core.c'],
    include_dirs=[XNVME_INCLUDE_DIR, np.get_include()],
    library_dirs=[XNVME_LIB_DIR],
    libraries=['xnvme'],
    extra_compile_args=['-std=c99'],
    extra_link_args=['-Wl,-rpath,' + XNVME_LIB_DIR]
)

setup(
    name='xnvme-core-local',
    version='1.0',
    description='xNVMe core functions (local build)',
    ext_modules=[xnvme_core],
    install_requires=['numpy'],
) 