module.exports = {
    run: [
        // 0. Write temporary overrides file to resolve whisperx vs transformers conflicts
        {
            method: "fs.write",
            params: {
                path: "overrides.txt",
                text: "huggingface-hub>=0.34.0,<1.0"
            }
        },
        // 1. Create venv and install initial dependencies (gradio + requirements.txt)
        // NOTE: whisperx pins torch~=2.8.0 which installs CPU-only torch from PyPI.
        // We MUST install requirements first, then override with CUDA torch in step 2.
        {
            method: "shell.run",
            params: {
                venv: "env",
                path: ".",
                env: {
                    "UV_NATIVE_TLS": "true",
                    "UV_SYSTEM_CERTS": "true",
                    "UV_INSECURE_HOST": "pypi.org,pypi.python.org,files.pythonhosted.org",
                    "PIP_TRUSTED_HOST": "pypi.org pypi.python.org files.pythonhosted.org"
                },
                message: [
                    "uv pip install gradio",
                    "uv pip install pip-system-certs",
                    "uv pip install -r requirements.txt --override overrides.txt"
                ]
            }
        },
        // 2. Override torch with CUDA-enabled version (replaces CPU-only from step 1)
        {
            method: "script.start",
            params: {
                uri: "torch.js",
                params: {
                    venv: "env",
                    path: "."
                }
            }
        },
        // 3. Install VoxCPM 2 (sole TTS and voice cloning backend)
        {
            method: "shell.run",
            params: {
                venv: "env",
                path: ".",
                env: {
                    "UV_NATIVE_TLS": "true",
                    "UV_SYSTEM_CERTS": "true",
                    "UV_INSECURE_HOST": "pypi.org,pypi.python.org,files.pythonhosted.org",
                    "PIP_TRUSTED_HOST": "pypi.org pypi.python.org files.pythonhosted.org"
                },
                message: "uv pip install \"voxcpm>=2.0.2\""
            }
        },
        // 3.1 Install the BgTTS-38M-V2 local Bulgarian cloning backend.
        // MioCodec declares requires-python>=3.12 but its source is 3.10-compatible
        // (no PEP 695 generics / 3.12-only stdlib). Install it without dependency
        // resolution or the version guard; its runtime deps come from requirements.txt.
        // Non-fatal: if it fails, BgTTS-38M is simply hidden from the engine list.
        {
            method: "shell.run",
            params: {
                venv: "env",
                path: ".",
                env: {
                    "UV_NATIVE_TLS": "true",
                    "UV_SYSTEM_CERTS": "true",
                    "UV_INSECURE_HOST": "pypi.org,pypi.python.org,files.pythonhosted.org",
                    "PIP_TRUSTED_HOST": "pypi.org pypi.python.org files.pythonhosted.org"
                },
                message: "uv pip install --no-deps --ignore-requires-python \"git+https://github.com/Aratako/MioCodec\" || echo \"BgTTS-38M backend unavailable - Bulgarian cloning falls back to Edge-TTS BG\""
            }
        },
        // 4. Install llama-cpp-python for high-speed C++ GGUF inference & KV Cache.
        // IMPORTANT (Python 3.14): the old pinned wheel was cp310-only
        // (llama_cpp_python-0.3.4-cp310-...-win_amd64.whl) and cannot install on
        // 3.14, which is why local .gguf models failed while predefined models
        // worked (they fall back to the PyTorch engine).
        // New strategy — a fallback chain that always lands on something installable
        // on 3.14 (ABI-agnostic `py3-none` wheels, loaded via ctypes):
        //   1) Official CPU wheel from abetlen's index (py3-none, no compiler).
        //      Guaranteed to install on 3.14 and run everywhere (CPU inference).
        //   2) Source build as a last resort (GPU users can swap in an arch-specific
        //      wheel, e.g. dougeeai sm_86 Ampere for RTX 3070 — see README).
        {
            when: "{{platform === 'win32'}}",
            method: "shell.run",
            params: {
                venv: "env",
                path: ".",
                env: {
                    "UV_SYSTEM_CERTS": "true",
                    "UV_INSECURE_HOST": "pypi.org,pypi.python.org,files.pythonhosted.org",
                    "PIP_TRUSTED_HOST": "pypi.org pypi.python.org files.pythonhosted.org"
                },
                message: "uv pip install \"llama-cpp-python @ https://abetlen.github.io/llama-cpp-python/whl/cpu/llama-cpp-python/llama_cpp_python-0.3.36-py3-none-win_amd64.whl\" || uv pip install llama-cpp-python || echo \"llama-cpp-python unavailable - local .gguf Custom models disabled (predefined models still work via PyTorch)\""
            }
        },
        {
            when: "{{platform !== 'win32'}}",
            method: "shell.run",
            params: {
                venv: "env",
                path: ".",
                env: {
                    "UV_SYSTEM_CERTS": "true",
                    "UV_INSECURE_HOST": "pypi.org,pypi.python.org,files.pythonhosted.org",
                    "PIP_TRUSTED_HOST": "pypi.org pypi.python.org files.pythonhosted.org"
                },
                message: "uv pip install llama-cpp-python"
            }
        },
        // 6. Try installing flash-attn (optional, speeds up TTS inference)
        {
            when: "{{platform !== 'win32'}}",
            method: "shell.run",
            params: {
                venv: "env",
                path: ".",
                env: {
                    "UV_NATIVE_TLS": "true",
                    "UV_SYSTEM_CERTS": "true",
                    "UV_INSECURE_HOST": "pypi.org,pypi.python.org,files.pythonhosted.org",
                    "PIP_TRUSTED_HOST": "pypi.org pypi.python.org files.pythonhosted.org"
                },
                message: "uv pip install wheel psutil ninja packaging && uv pip install flash-attn --no-build-isolation || echo FlashAttention not available - will use default attention"
            }
        },


        // 7. Verify full installation
        {
            method: "shell.run",
            params: {
                venv: "env",
                path: ".",
                env: {
                    "UV_NATIVE_TLS": "true",
                    "UV_SYSTEM_CERTS": "true",
                    "UV_INSECURE_HOST": "pypi.org,pypi.python.org,files.pythonhosted.org",
                    "PIP_TRUSTED_HOST": "pypi.org pypi.python.org files.pythonhosted.org"
                },
                message: [
                    "python check_env.py",
                    "python -c \"import torch; print('torch', torch.__version__, 'CUDA' if torch.cuda.is_available() else 'CPU')\"",
                    "python -c \"import voxcpm; print('VoxCPM 2 OK')\"",
                    "python -c \"import miocodec; print('MioCodec OK (BgTTS-38M cloning)')\"",
                    "python -c \"import bitsandbytes; print('bitsandbytes', bitsandbytes.__version__, 'OK')\"",
                    "python -c \"import llama_cpp; print('llama_cpp', llama_cpp.__version__, 'OK')\""
                ]
            }
        },
        // 8. Write sentinel file to indicate success
        {
            method: "fs.write",
            params: {
                path: "env/installed.sentinel",
                text: "success"
            }
        },
        // 9. Clean up overrides file
        {
            method: "fs.rm",
            params: {
                path: "overrides.txt"
            }
        },
        // 10. Done
        {
            method: "notify",
            params: {
                title: "Installation Complete!",
                description: "ZastTranslate is ready with voice cloning support. Click 'Start' to launch."
            }
        }
    ]
}
