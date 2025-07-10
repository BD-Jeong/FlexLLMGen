from setuptools import setup, Extension
import numpy

# C 확장 모듈 정의
xnvme_core_module = Extension(
    'xnvme_core',
    sources=['xnvme_core.c'],
    libraries=['xnvme'],
    include_dirs=[numpy.get_include()],
    extra_compile_args=['-O2'],
    extra_link_args=['-O2']
)

setup(
    name='xnvme_core',
    version='1.0',
    description='xNVMe core functions for Python',
    ext_modules=[xnvme_core_module],
    install_requires=['numpy'],
) 