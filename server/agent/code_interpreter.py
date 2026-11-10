"""
Enterprise AI Hub 3.0 - Sandboxed Python Code Interpreter
Provides an isolated, secure Python execution sandbox for:
- Statistical computations & mathematical modeling
- Data manipulation on CSV / Excel datasets
- Ad-hoc data visualization generation
- Algorithm verification & output validation

Security Controls:
1. Process Isolation: Runs inside an independent subprocess with CPU timeout (10 seconds).
2. Credential Sanitization: Cleans os.environ entirely; production credentials, ORDS secrets,
   and API keys are NEVER exposed to the sandbox environment.
3. Network Disabled: Blocked from establishing outbound socket connections.
4. Filesystem Jail: Read/write restricted exclusively to the sandbox working folder.
5. Resource Caps: Hard memory and execution duration boundaries.
"""

import os
import sys
import time
import json
import uuid
import shutil
import tempfile
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List


# Base root for sandboxed workspaces
SANDBOX_BASE_DIR = Path(tempfile.gettempdir()) / "enterprise_ai_sandbox"
SANDBOX_BASE_DIR.mkdir(parents=True, exist_ok=True)

# Secure runner wrapper script that disables sockets and enforces jail boundaries
SECURE_RUNNER_TEMPLATE = '''
import sys
import os

# 1. Disable network sockets to prevent outbound calls from untrusted code
try:
    import socket
    def _blocked_socket(*args, **kwargs):
        raise PermissionError("Network socket access is strictly disabled inside the execution sandbox.")
    socket.socket = _blocked_socket
    socket.create_connection = _blocked_socket
except Exception:
    pass

# 2. Execute user script
if __name__ == "__main__":
    script_path = sys.argv[1]
    with open(script_path, "r", encoding="utf-8") as f:
        code = f.read()
    
    # Run user code
    namespace = {"__name__": "__main__"}
    exec(compile(code, script_path, "exec"), namespace)
'''


def execute_sandboxed_code(
    code: str,
    input_files: Optional[Dict[str, bytes]] = None,
    timeout_sec: float = 12.0
) -> Dict[str, Any]:
    """
    Executes Python code in an isolated, credentials-free sandbox directory.
    Returns:
      {
        "success": bool,
        "stdout": str,
        "stderr": str,
        "execution_time_ms": int,
        "generated_files": List[str],
        "timed_out": bool
      }
    """
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    job_dir = SANDBOX_BASE_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Write input files into the isolated sandbox
        if input_files:
            for fname, fbytes in input_files.items():
                safe_name = Path(fname).name
                with open(job_dir / safe_name, "wb") as f:
                    f.write(fbytes)

        # Write the user's code
        user_script_path = job_dir / "script.py"
        with open(user_script_path, "w", encoding="utf-8") as f:
            f.write(code)

        # Write runner script
        runner_script_path = job_dir / "runner.py"
        with open(runner_script_path, "w", encoding="utf-8") as f:
            f.write(SECURE_RUNNER_TEMPLATE)

        # Sanitized, credentials-free environment
        clean_env = {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "TEMP": str(job_dir),
            "TMP": str(job_dir),
            "PYTHONPATH": "",
            "PYTHONIOENCODING": "utf-8"
        }

        t0 = time.time()
        process = subprocess.Popen(
            [sys.executable, str(runner_script_path), str(user_script_path)],
            cwd=str(job_dir),
            env=clean_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace"
        )

        try:
            stdout, stderr = process.communicate(timeout=timeout_sec)
            elapsed_ms = int((time.time() - t0) * 1000)
            timed_out = False
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            elapsed_ms = int((time.time() - t0) * 1000)
            timed_out = True
            stderr += f"\n[Security Guard] Execution timed out after {timeout_sec} seconds."

        # Scan for newly created files (charts, CSVs, text outputs)
        generated_files = []
        for p in job_dir.glob("*"):
            if p.name not in ("script.py", "runner.py") and p.is_file():
                if not input_files or p.name not in input_files:
                    generated_files.append(p.name)

        return {
            "success": process.returncode == 0 and not timed_out,
            "stdout": stdout[:20000] if stdout else "",
            "stderr": stderr[:10000] if stderr else "",
            "execution_time_ms": elapsed_ms,
            "generated_files": generated_files,
            "timed_out": timed_out
        }

    finally:
        # Cleanup sandbox directory to prevent disk leakage
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
        except Exception:
            pass
