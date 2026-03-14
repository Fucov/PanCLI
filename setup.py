
from setuptools import setup, find_packages

with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh if line.strip()]

setup(
    name='bhpan',
    version='2.0.0',
    author='LZR',
    license='MIT',
    description='bhpan commandline tool — modern refactored edition',
    py_modules=[],
    packages=find_packages(),
    install_requires=requirements,
    python_requires='>=3.10',
    classifiers=[
        "Operating System :: OS Independent",
    ],
    entry_points='''
        [console_scripts]
        bhpan=pancli.main:cli
    '''
)