@echo off
echo Setting up VS environment...
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
echo Environment setup complete.

echo Installing llama-cpp-python with CUDA support (Ninja Generator)...
set CMAKE_ARGS=-DGGML_CUDA=on
set CMAKE_GENERATOR=Ninja
set FORCE_CMAKE=1
python -m pip install llama-cpp-python --upgrade --force-reinstall --no-cache-dir

echo Done.
